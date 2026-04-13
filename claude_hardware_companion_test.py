import logging
import json
import time
from pathlib import Path
from dataclasses import dataclass
from threading import Lock
from typing import Any, Dict, Optional, Tuple

from flask import Flask, jsonify, request


APP_HOST = "127.0.0.1"
APP_PORT = 8765
DEDUP_WINDOW_SECONDS = 1.2
TASK_DONE_SUPPRESS_STOP_SECONDS = 3.0
TEST_LOG_PATH = Path(r"C:\ClaudeHardware\test_events.log")
TEST_LAST_EVENT_PATH = Path(r"C:\ClaudeHardware\test_last_event.txt")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("claude-hardware-companion-test")

app = Flask(__name__)


@dataclass
class SignalEvent:
    # 测试版与正式版共用同一层逻辑：只记录“信号”，不绑定硬件效果。
    signal: str
    source: str
    legacy_event: Optional[str] = None
    tool_name: Optional[str] = None
    notification_type: Optional[str] = None
    title: Optional[str] = None
    message: Optional[str] = None


class LocalTestBridge:
    """测试版常驻服务：不依赖 USB，收到事件后只记录统一 signal。"""

    def __init__(self) -> None:
        # 与正式版保持同样的状态机和去重规则，便于先验证整条链路。
        self._lock = Lock()
        self._current_state = "idle"
        self._last_event_name: Optional[str] = None
        self._last_event_time = 0.0
        self._last_task_done_time = 0.0
        self._last_error: Optional[str] = None

    def health_snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "status": "ok",
                "mode": "test",
                "state": self._current_state,
                "local_action": "file_log_only",
                "last_error": self._last_error,
                "last_event_file": str(TEST_LAST_EVENT_PATH),
                "event_log_file": str(TEST_LOG_PATH),
            }

    def process_hook_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        signal_event = self._normalize_event(payload)
        if not signal_event:
            return {
                "accepted": False,
                "reason": "unsupported_event",
                "detail": {
                    "source": str(payload.get("hook_event_name") or payload.get("event") or payload.get("type") or ""),
                    "notification_type": str(payload.get("notification_type") or payload.get("subtype") or ""),
                    "tool_name": str(payload.get("tool_name") or ""),
                },
            }

        with self._lock:
            should_emit, reason = self._should_emit_locked(signal_event)
            if not should_emit:
                return {
                    "accepted": True,
                    "signal": signal_event.signal,
                    "legacy_event": signal_event.legacy_event,
                    "emitted": False,
                    "reason": reason,
                    "state": self._current_state,
                }

            emitted = self._trigger_local_action_locked(signal_event)
            now = time.monotonic()
            self._last_event_name = signal_event.signal
            self._last_event_time = now
            if signal_event.legacy_event == "PERMISSION_WAIT":
                self._current_state = "waiting_permission"
            elif signal_event.signal == "CLAUDE_USER_QUESTION":
                self._current_state = "waiting_user_question"
            elif signal_event.signal == "CLAUDE_IDLE_INPUT":
                self._current_state = "waiting_user_input"
            elif signal_event.legacy_event == "TASK_DONE":
                self._current_state = "idle"
                self._last_task_done_time = now
            elif signal_event.legacy_event == "ROUND_STOP":
                self._current_state = "idle"

            return {
                "accepted": True,
                "signal": signal_event.signal,
                "legacy_event": signal_event.legacy_event,
                "emitted": emitted,
                "reason": "local_action_triggered" if emitted else "local_action_failed",
                "state": self._current_state,
            }

    def _normalize_event(self, payload: Dict[str, Any]) -> Optional[SignalEvent]:
        """把 Claude Code hooks 事件映射成统一 signal，便于测试协议层。"""
        event_name = str(
            payload.get("hook_event_name")
            or payload.get("event")
            or payload.get("type")
            or ""
        )
        notification_type = str(payload.get("notification_type") or payload.get("subtype") or "")
        tool_name = str(payload.get("tool_name") or "")
        title = str(payload.get("title") or "")
        message = str(payload.get("message") or "")

        if event_name == "PermissionRequest":
            signal = "CLAUDE_PROCESS_CONFIRM_REQUEST" if tool_name == "Bash" else "CLAUDE_PERMISSION_REQUEST"
            return SignalEvent(
                signal=signal,
                source=event_name,
                legacy_event="PERMISSION_WAIT",
                tool_name=tool_name or None,
                title=title or None,
                message=message or None,
            )
        if event_name == "Notification" and notification_type == "permission_prompt":
            signal = "CLAUDE_PROCESS_CONFIRM_REQUEST" if "Bash" in message else "CLAUDE_PERMISSION_REQUEST"
            return SignalEvent(
                signal=signal,
                source=f"{event_name}:{notification_type}",
                legacy_event="PERMISSION_WAIT",
                notification_type=notification_type,
                title=title or None,
                message=message or None,
            )
        if event_name == "Notification" and notification_type == "elicitation_dialog":
            return SignalEvent(
                signal="CLAUDE_USER_QUESTION",
                source=f"{event_name}:{notification_type}",
                notification_type=notification_type,
                title=title or None,
                message=message or None,
            )
        if event_name == "Notification" and notification_type == "idle_prompt":
            return SignalEvent(
                signal="CLAUDE_IDLE_INPUT",
                source=f"{event_name}:{notification_type}",
                notification_type=notification_type,
                title=title or None,
                message=message or None,
            )
        if event_name == "PreToolUse" and tool_name == "AskUserQuestion":
            return SignalEvent(
                signal="CLAUDE_USER_QUESTION",
                source=f"{event_name}:{tool_name}",
                tool_name=tool_name,
                title=title or None,
                message=message or None,
            )
        if event_name == "TaskCompleted":
            return SignalEvent(
                signal="CLAUDE_TASK_DONE",
                source=event_name,
                legacy_event="TASK_DONE",
            )
        if event_name == "Stop":
            return SignalEvent(
                signal="CLAUDE_ROUND_STOP",
                source=event_name,
                legacy_event="ROUND_STOP",
            )
        return None

    def _should_emit_locked(self, signal_event: SignalEvent) -> Tuple[bool, str]:
        """测试版与正式版保持一致的去重和状态机规则。"""
        now = time.monotonic()

        if (
            self._last_event_name == signal_event.signal
            and now - self._last_event_time < DEDUP_WINDOW_SECONDS
        ):
            return False, "dedup_window"

        if signal_event.legacy_event == "PERMISSION_WAIT" and self._current_state == "waiting_permission":
            return False, "already_waiting_permission"

        if signal_event.signal == "CLAUDE_USER_QUESTION" and self._current_state == "waiting_user_question":
            return False, "already_waiting_user_question"

        if signal_event.signal == "CLAUDE_IDLE_INPUT" and self._current_state == "waiting_user_input":
            return False, "already_waiting_user_input"

        if signal_event.legacy_event == "ROUND_STOP":
            if self._current_state == "idle":
                return False, "stop_ignored_while_idle"
            if now - self._last_task_done_time < TASK_DONE_SUPPRESS_STOP_SECONDS:
                return False, "stop_suppressed_after_task_done"

        return True, "emit"

    def _trigger_local_action_locked(self, signal_event: SignalEvent) -> bool:
        """收到事件后仅写入本地日志，便于无需 USB 直接验证。"""
        try:
            TEST_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            payload = {
                "timestamp": timestamp,
                "signal": signal_event.signal,
                "legacy_event": signal_event.legacy_event,
                "source": signal_event.source,
                "tool_name": signal_event.tool_name,
                "notification_type": signal_event.notification_type,
                "title": signal_event.title,
                "message": signal_event.message,
            }
            TEST_LAST_EVENT_PATH.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
            with TEST_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

            logger.info("Triggered local test action: %s", signal_event.signal)
            self._last_error = None
            return True
        except Exception as exc:  # pragma: no cover - defensive logging
            self._last_error = str(exc)
            logger.exception("Failed to trigger local action: %s", exc)
            return False


bridge = LocalTestBridge()


@app.post("/event")
def receive_event():
    # Claude Code hooks 的 POST 入口。测试版和正式版接口完全一致。
    payload = request.get_json(silent=True) or {}
    result = bridge.process_hook_payload(payload)
    status_code = 200 if result.get("accepted") else 400
    return jsonify(result), status_code


@app.get("/health")
def health():
    # 返回测试模式状态和最近触发的本地文件路径。
    return jsonify(bridge.health_snapshot())


if __name__ == "__main__":
    logger.info("Starting Claude hardware companion test mode on http://%s:%s", APP_HOST, APP_PORT)
    app.run(host=APP_HOST, port=APP_PORT, debug=False, threaded=True)
