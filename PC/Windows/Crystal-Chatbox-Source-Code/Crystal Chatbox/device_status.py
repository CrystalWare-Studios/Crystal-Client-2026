import os
import threading
import time
from copy import deepcopy

IS_ANDROID = "ANDROID_ARGUMENT" in os.environ

_lock = threading.RLock()
_thread = None
_stop_event = threading.Event()
_configured_enabled = False
_configured_interval = 60

_state = {
    "enabled": False,
    "available": IS_ANDROID,
    "status": "disabled" if not IS_ANDROID else "stopped",
    "last_error": "" if IS_ANDROID else "Device status is only available on the Quest build.",
    "updated_at": 0,
    "storage_free_gb": None,
    "storage_total_gb": None,
    "storage_percent_used": None,
}


def _now():
    return time.time()


def get_state():
    with _lock:
        return deepcopy(_state)


def configure(enabled=True, interval=60):
    global _configured_enabled, _configured_interval
    with _lock:
        _configured_enabled = bool(enabled)
        try:
            _configured_interval = max(15, min(int(interval or 60), 600))
        except Exception:
            _configured_interval = 60
        _state["enabled"] = _configured_enabled
        if not _configured_enabled:
            _state["status"] = "disabled"


def start_tracker(enabled=True, interval=60):
    global _thread
    configure(enabled=enabled, interval=interval)
    if not IS_ANDROID:
        return
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_poll_loop, daemon=True)
    _thread.start()


def stop_tracker():
    _stop_event.set()


def refresh_now():
    _update_once()
    return get_state()


def _poll_loop():
    while not _stop_event.is_set():
        with _lock:
            enabled = _configured_enabled
            interval = _configured_interval
        if enabled:
            _update_once()
        else:
            with _lock:
                _state["status"] = "disabled"
        _stop_event.wait(interval if enabled else 5)


def _update_once():
    if not IS_ANDROID:
        with _lock:
            _state["status"] = "unavailable"
            _state["last_error"] = "Device status is only available on the Quest build."
        return

    try:
        from jnius import autoclass

        Environment = autoclass("android.os.Environment")
        StatFs = autoclass("android.os.StatFs")

        data_dir = Environment.getDataDirectory()
        stat = StatFs(data_dir.getAbsolutePath())
        total_bytes = stat.getTotalBytes()
        free_bytes = stat.getAvailableBytes()
        percent_used = round((1 - free_bytes / total_bytes) * 100) if total_bytes else None

        with _lock:
            _state["status"] = "active"
            _state["last_error"] = ""
            _state["storage_free_gb"] = round(free_bytes / (1024 ** 3), 1)
            _state["storage_total_gb"] = round(total_bytes / (1024 ** 3), 1)
            _state["storage_percent_used"] = percent_used
            _state["updated_at"] = _now()
    except Exception as exc:
        reason = str(exc).strip() or type(exc).__name__
        with _lock:
            _state["status"] = "error"
            _state["last_error"] = reason
            _state["updated_at"] = _now()


def get_storage_text():
    state = get_state()
    if not state.get("enabled") or state.get("status") != "active":
        return ""
    free_gb = state.get("storage_free_gb")
    if free_gb is None:
        return ""
    return f"{free_gb}GB free"
