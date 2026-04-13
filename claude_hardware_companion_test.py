import logging
import time
from pathlib import Path
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


class LocalTestBridge:
    """测试版常驻服务：不依赖 USB，收到事件后直接触发本机动作。"""

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
        normalized, detail = self._normalize_event(payload)
        if not normalized:
            return {
                "accepted": False,
                "reason": "unsupported_event",
                "detail": detail,
            }

        with self._lock:
            should_emit, reason = self._should_emit_locked(normalized)
            if not should_emit:
                return {
                    "accepted": True,
                    "normalized_event": normalized,
                    "emitted": False,
                    "reason": reason,
                    "state": self._current_state,
                }

            emitted = self._trigger_local_action_locked(normalized)
            now = time.monotonic()
            self._last_event_name = normalized
            self._last_event_time = now
            if normalized == "PERMISSION_WAIT":
                self._current_state = "waiting_permission"
            elif normalized == "TASK_DONE":
                self._current_state = "idle"
                self._last_task_done_time = now
            elif normalized == "ROUND_STOP":
                self._current_state = "idle"

            return {
                "accepted": True,
                "normalized_event": normalized,
                "emitted": emitted,
                "reason": "local_action_triggered" if emitted else "local_action_failed",
                "state": self._current_state,
            }

    def _normalize_event(self, payload: Dict[str, Any]) -> Tuple[Optional[str], Dict[str, Any]]:
        """把 Claude Code hooks 事件统一映射成测试版内部使用的 3 种状态。"""
        event_name = str(
            payload.get("hook_event_name")
            or payload.get("event")
            or payload.get("type")
            or ""
        )
        notification_type = str(payload.get("notification_type") or payload.get("subtype") or "")

        if event_name == "PermissionRequest":
            return "PERMISSION_WAIT", {"source": event_name}
        if event_name == "Notification" and notification_type == "permission_prompt":
            return "PERMISSION_WAIT", {"source": f"{event_name}:{notification_type}"}
        if event_name == "TaskCompleted":
            return "TASK_DONE", {"source": event_name}
        if event_name == "Stop":
            return "ROUND_STOP", {"source": event_name}
        return None, {"source": event_name, "notification_type": notification_type}

    def _should_emit_locked(self, normalized_event: str) -> Tuple[bool, str]:
        """测试版与正式版保持一致的去重和状态机规则。"""
        now = time.monotonic()

        if (
            self._last_event_name == normalized_event
            and now - self._last_event_time < DEDUP_WINDOW_SECONDS
        ):
            return False, "dedup_window"

        if normalized_event == "PERMISSION_WAIT" and self._current_state == "waiting_permission":
            return False, "already_waiting_permission"

        if normalized_event == "ROUND_STOP":
            if self._current_state == "idle":
                return False, "stop_ignored_while_idle"
            if now - self._last_task_done_time < TASK_DONE_SUPPRESS_STOP_SECONDS:
                return False, "stop_suppressed_after_task_done"

        return True, "emit"

    def _trigger_local_action_locked(self, normalized_event: str) -> bool:
        """收到事件后仅写入本地日志，便于无需 USB 直接验证。"""
        try:
            TEST_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            TEST_LAST_EVENT_PATH.write_text(
                f"{timestamp} {normalized_event}\n",
                encoding="utf-8",
            )
            with TEST_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(f"{timestamp} {normalized_event}\n")

            logger.info("Triggered local test action: %s", normalized_event)
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
