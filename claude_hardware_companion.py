import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import serial
from flask import Flask, jsonify, request
from serial.tools import list_ports


APP_HOST = "127.0.0.1"
APP_PORT = 8765
BAUDRATE = 115200
PRODUCT_STRING = "ClaudeHookDevice"
USB_VID = None
USB_PID = None
RECONNECT_INTERVAL = 2.0
DEDUP_WINDOW_SECONDS = 1.2
TASK_DONE_SUPPRESS_STOP_SECONDS = 3.0
SERIAL_WRITE_TIMEOUT = 1.0


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("claude-hardware-companion")

app = Flask(__name__)


@dataclass
class SerialTarget:
    # 目标串口设备的基本识别信息，便于健康检查与日志输出。
    device: str
    description: str
    vid: Optional[int]
    pid: Optional[int]
    product: Optional[str]


class HardwareBridge:
    """常驻后台桥接器：负责事件归一化、状态机控制与串口发送。"""

    def __init__(self) -> None:
        # 这把锁用于保护状态机、最近事件记录以及串口对象，避免并发请求互相干扰。
        self._lock = threading.Lock()
        self._serial: Optional[serial.Serial] = None
        self._serial_target: Optional[SerialTarget] = None
        self._current_state = "idle"
        self._last_event_name: Optional[str] = None
        self._last_event_time = 0.0
        self._last_task_done_time = 0.0
        self._last_error: Optional[str] = None
        self._stop_event = threading.Event()
        self._worker = threading.Thread(target=self._serial_worker, daemon=True)

    def start(self) -> None:
        self._worker.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._close_serial()

    def health_snapshot(self) -> Dict[str, Any]:
        # health 接口读取当前服务状态，便于排查串口连接和最近错误。
        with self._lock:
            target = self._serial_target
            is_connected = self._serial is not None and self._serial.is_open
            return {
                "status": "ok",
                "state": self._current_state,
                "serial_connected": is_connected,
                "serial_port": target.device if target else None,
                "serial_product": target.product if target else None,
                "serial_vid": f"{target.vid:04X}" if target and target.vid is not None else None,
                "serial_pid": f"{target.pid:04X}" if target and target.pid is not None else None,
                "last_error": self._last_error,
            }

    def process_hook_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        # 单次处理 Claude Code hook 请求：
        # 1. 归一化事件
        # 2. 按状态机判断是否应发出
        # 3. 若允许则写入串口
        normalized, detail = self._normalize_event(payload)
        if not normalized:
            return {
                "accepted": False,
                "reason": "unsupported_event",
                "detail": detail,
            }

        with self._lock:
            should_send, reason = self._should_emit_locked(normalized)
            if not should_send:
                return {
                    "accepted": True,
                    "normalized_event": normalized,
                    "emitted": False,
                    "reason": reason,
                    "state": self._current_state,
                }

            sent = self._send_serial_locked(normalized)
            now = time.monotonic()
            # 不论串口当下是否可用，只要该事件通过了状态机，都更新去重时钟。
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
                "emitted": sent,
                "reason": "serial_sent" if sent else "serial_unavailable",
                "state": self._current_state,
            }

    def _normalize_event(self, payload: Dict[str, Any]) -> Tuple[Optional[str], Dict[str, Any]]:
        """把 Claude Code hooks 的不同事件统一映射成 3 种硬件事件。"""
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
        """按去重、节流和状态机规则决定当前事件是否应真正输出。"""
        now = time.monotonic()

        if (
            self._last_event_name == normalized_event
            and now - self._last_event_time < DEDUP_WINDOW_SECONDS
        ):
            # 1.2 秒内同类事件直接去重。
            return False, "dedup_window"

        if normalized_event == "PERMISSION_WAIT" and self._current_state == "waiting_permission":
            # 已经处于等待授权状态时，不重复提醒硬件。
            return False, "already_waiting_permission"

        if normalized_event == "ROUND_STOP":
            if self._current_state == "idle":
                # 空闲态收到 Stop 没有意义，直接丢弃。
                return False, "stop_ignored_while_idle"
            if now - self._last_task_done_time < TASK_DONE_SUPPRESS_STOP_SECONDS:
                # 刚完成任务后，紧接着的 Stop 往往只是收尾噪声，不再下发。
                return False, "stop_suppressed_after_task_done"

        return True, "emit"

    def _send_serial_locked(self, normalized_event: str) -> bool:
        """如果串口已连接，则发送一行 ASCII 命令给设备。"""
        serial_conn = self._serial
        if not serial_conn or not serial_conn.is_open:
            self._last_error = "serial_unavailable"
            logger.warning("Serial unavailable, skipped event %s", normalized_event)
            return False

        try:
            serial_conn.write((normalized_event + "\n").encode("ascii"))
            serial_conn.flush()
            self._last_error = None
            logger.info("Sent event to hardware: %s", normalized_event)
            return True
        except serial.SerialException as exc:
            self._last_error = str(exc)
            logger.warning("Serial write failed: %s", exc)
            self._close_serial_locked()
            return False

    def _serial_worker(self) -> None:
        """后台循环探测设备，断线后自动重连。"""
        while not self._stop_event.is_set():
            try:
                if not self._serial or not self._serial.is_open:
                    target = self._discover_target()
                    if target:
                        self._open_serial(target)
                time.sleep(RECONNECT_INTERVAL)
            except Exception as exc:  # pragma: no cover - last-resort guard
                self._last_error = str(exc)
                logger.exception("Serial worker error: %s", exc)
                time.sleep(RECONNECT_INTERVAL)

    def _discover_target(self) -> Optional[SerialTarget]:
        """按 Product String 与可选 VID/PID 查找第一个匹配的 USB CDC 设备。"""
        ports = list_ports.comports()
        for port in ports:
            product = getattr(port, "product", None)
            vid = getattr(port, "vid", None)
            pid = getattr(port, "pid", None)
            product_ok = PRODUCT_STRING is None or product == PRODUCT_STRING
            vid_ok = USB_VID is None or vid == USB_VID
            pid_ok = USB_PID is None or pid == USB_PID
            if product_ok and vid_ok and pid_ok:
                return SerialTarget(
                    device=port.device,
                    description=port.description,
                    vid=vid,
                    pid=pid,
                    product=product,
                )
        return None

    def _open_serial(self, target: SerialTarget) -> None:
        # 建立串口连接。若失败则仅记录日志，后台线程会继续重试。
        with self._lock:
            if self._serial and self._serial.is_open:
                return
            try:
                self._serial = serial.Serial(
                    target.device,
                    BAUDRATE,
                    timeout=1,
                    write_timeout=SERIAL_WRITE_TIMEOUT,
                )
                self._serial_target = target
                self._last_error = None
                logger.info(
                    "Connected to %s (%s, VID=%s PID=%s)",
                    target.device,
                    target.product or target.description,
                    f"{target.vid:04X}" if target.vid is not None else "N/A",
                    f"{target.pid:04X}" if target.pid is not None else "N/A",
                )
            except serial.SerialException as exc:
                self._last_error = str(exc)
                self._serial = None
                self._serial_target = None
                logger.warning("Failed to open %s: %s", target.device, exc)

    def _close_serial(self) -> None:
        with self._lock:
            self._close_serial_locked()

    def _close_serial_locked(self) -> None:
        # 清理串口句柄，供断线重连或进程退出时复用。
        if self._serial:
            try:
                self._serial.close()
            except serial.SerialException:
                pass
        self._serial = None
        self._serial_target = None


bridge = HardwareBridge()
bridge.start()


@app.post("/event")
def receive_event():
    # Claude Code hooks 把 JSON POST 到这里。
    payload = request.get_json(silent=True) or {}
    result = bridge.process_hook_payload(payload)
    status_code = 200 if result.get("accepted") else 400
    return jsonify(result), status_code


@app.get("/health")
def health():
    # 用于本地自检，确认服务是否在线以及串口是否已接入。
    return jsonify(bridge.health_snapshot())


if __name__ == "__main__":
    logger.info("Starting Claude hardware companion on http://%s:%s", APP_HOST, APP_PORT)
    app.run(host=APP_HOST, port=APP_PORT, debug=False, threaded=True)
