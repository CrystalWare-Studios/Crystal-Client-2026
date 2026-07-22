import os
import sys
import threading
import time
from copy import deepcopy

IS_ANDROID = "ANDROID_ARGUMENT" in os.environ
IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

try:
    if IS_WINDOWS and not IS_ANDROID:
        from pycaw.pycaw import AudioUtilities
        PYCAW_AVAILABLE = True
    else:
        PYCAW_AVAILABLE = False
except Exception:
    PYCAW_AVAILABLE = False

VOLUME_SOURCE_AVAILABLE = IS_ANDROID or PYCAW_AVAILABLE or IS_MACOS


_lock = threading.RLock()
_thread = None
_stop_event = threading.Event()
_configured_enabled = False
_configured_interval = 10

_state = {
    "enabled": False,
    "available": VOLUME_SOURCE_AVAILABLE,
    "source": "quest" if IS_ANDROID else "windows" if PYCAW_AVAILABLE else "macos" if IS_MACOS else "device",
    "status": "disabled" if not VOLUME_SOURCE_AVAILABLE else "stopped",
    "last_error": "" if VOLUME_SOURCE_AVAILABLE else "No volume data source available on this platform.",
    "updated_at": 0,
    "percent": None,
    "muted": False,
}


def _now():
    return time.time()


def get_state():
    with _lock:
        return deepcopy(_state)


def configure(enabled=True, interval=10):
    global _configured_enabled, _configured_interval
    with _lock:
        _configured_enabled = bool(enabled)
        try:
            _configured_interval = max(2, min(int(interval or 10), 60))
        except Exception:
            _configured_interval = 10
        _state["enabled"] = _configured_enabled
        if not _configured_enabled:
            _state["status"] = "disabled"


def start_tracker(enabled=True, interval=10):
    global _thread
    configure(enabled=enabled, interval=interval)
    if not VOLUME_SOURCE_AVAILABLE:
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
        _stop_event.wait(interval if enabled else 3)


def _update_once():
    if IS_ANDROID:
        _update_once_android()
    elif PYCAW_AVAILABLE:
        _update_once_windows()
    elif IS_MACOS:
        _update_once_macos()
    else:
        with _lock:
            _state["status"] = "unavailable"
            _state["last_error"] = "No volume data source available on this platform."


def _windows_endpoint_volume():
    import comtypes

    comtypes.CoInitialize()
    device = AudioUtilities.GetSpeakers()
    if hasattr(device, "EndpointVolume"):
        return device.EndpointVolume

    from ctypes import POINTER, cast
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import IAudioEndpointVolume

    interface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


def _update_once_windows():
    try:
        endpoint_volume = _windows_endpoint_volume()
        level_scalar = endpoint_volume.GetMasterVolumeLevelScalar()
        muted = bool(endpoint_volume.GetMute())
        with _lock:
            _state["status"] = "active"
            _state["last_error"] = ""
            _state["percent"] = round(level_scalar * 100)
            _state["muted"] = muted
            _state["updated_at"] = _now()
    except Exception as exc:
        with _lock:
            _state["status"] = "error"
            _state["last_error"] = str(exc).strip() or type(exc).__name__
            _state["updated_at"] = _now()


def _update_once_macos():
    try:
        import subprocess

        result = subprocess.run(
            ["osascript", "-e", "output volume of (get volume settings)"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "osascript failed")
        muted_result = subprocess.run(
            ["osascript", "-e", "output muted of (get volume settings)"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        percent = max(0, min(100, int(result.stdout.strip())))
        muted = muted_result.stdout.strip().lower() == "true"
        with _lock:
            _state["status"] = "active"
            _state["last_error"] = ""
            _state["percent"] = percent
            _state["muted"] = muted
            _state["updated_at"] = _now()
    except Exception as exc:
        with _lock:
            _state["status"] = "error"
            _state["last_error"] = str(exc).strip() or type(exc).__name__
            _state["updated_at"] = _now()


def _update_once_android():
    try:
        from jnius import autoclass

        ActivityThread = autoclass("android.app.ActivityThread")
        Context = autoclass("android.content.Context")
        AudioManager = autoclass("android.media.AudioManager")
        context = ActivityThread.currentApplication()
        if context is None:
            raise RuntimeError("Quest audio service is not ready yet.")
        audio_manager = context.getSystemService(Context.AUDIO_SERVICE)
        if audio_manager is None:
            raise RuntimeError("Quest audio service is unavailable.")
        stream = AudioManager.STREAM_MUSIC
        current = audio_manager.getStreamVolume(stream)
        max_volume = audio_manager.getStreamMaxVolume(stream)
        percent = round((current / max_volume) * 100) if max_volume else 0
        with _lock:
            _state["status"] = "active"
            _state["last_error"] = ""
            _state["percent"] = percent
            _state["muted"] = current <= 0
            _state["updated_at"] = _now()
    except Exception as exc:
        reason = str(exc).strip() or type(exc).__name__
        with _lock:
            _state["status"] = "error"
            _state["last_error"] = reason
            _state["updated_at"] = _now()


def get_volume_text():
    state = get_state()
    if not state.get("enabled") or state.get("status") != "active":
        return ""
    percent = state.get("percent")
    if percent is None:
        return ""
    if state.get("muted"):
        return "Muted"
    return f"Volume {percent}%"
