import os
import threading
import time
from copy import deepcopy

IS_ANDROID = "ANDROID_ARGUMENT" in os.environ

try:
    import openvr
    OPENVR_AVAILABLE = True
except Exception:
    openvr = None
    OPENVR_AVAILABLE = False

try:
    if IS_ANDROID:
        raise ImportError
    from plyer import battery as _plyer_battery
    PLYER_AVAILABLE = True
except Exception:
    _plyer_battery = None
    PLYER_AVAILABLE = False

BATTERY_SOURCE_AVAILABLE = IS_ANDROID or OPENVR_AVAILABLE or PLYER_AVAILABLE


_lock = threading.RLock()
_thread = None
_stop_event = threading.Event()
_vr_system = None
_configured_enabled = False
_configured_interval = 15

_state = {
    "enabled": False,
    "available": BATTERY_SOURCE_AVAILABLE,
    "source": "quest" if IS_ANDROID else "steamvr" if OPENVR_AVAILABLE else "device",
    "steamvr_running": False,
    "status": "disabled" if not BATTERY_SOURCE_AVAILABLE else "stopped",
    "last_error": "" if BATTERY_SOURCE_AVAILABLE else "No battery data source available on this platform.",
    "updated_at": 0,
    "hmd": None,
    "controllers": [],
    "trackers": [],
}


def _now():
    return time.time()


def get_state():
    with _lock:
        return deepcopy(_state)


def configure(enabled=True, interval=15):
    global _configured_enabled, _configured_interval
    with _lock:
        _configured_enabled = bool(enabled)
        try:
            _configured_interval = max(5, min(int(interval or 15), 120))
        except Exception:
            _configured_interval = 15
        _state["enabled"] = _configured_enabled
        if not _configured_enabled:
            _state["status"] = "disabled"


def start_tracker(enabled=True, interval=15):
    global _thread
    configure(enabled=enabled, interval=interval)
    if not BATTERY_SOURCE_AVAILABLE:
        return
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_poll_loop, daemon=True)
    _thread.start()


def stop_tracker():
    _stop_event.set()
    _shutdown_vr_system()


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


def _ensure_vr_system():
    global _vr_system
    if _vr_system is not None:
        return _vr_system
    _vr_system = openvr.init(openvr.VRApplication_Background)
    return _vr_system


def _shutdown_vr_system():
    global _vr_system
    if _vr_system is not None:
        try:
            openvr.shutdown()
        except Exception:
            pass
        _vr_system = None


def _role_label(role_value):
    if role_value == getattr(openvr, "TrackedControllerRole_LeftHand", 1):
        return "Left Controller"
    if role_value == getattr(openvr, "TrackedControllerRole_RightHand", 2):
        return "Right Controller"
    return "Controller"


def _safe_bool_prop(system, index, prop, default=False):
    try:
        return bool(system.getBoolTrackedDeviceProperty(index, prop))
    except Exception:
        return default


def _safe_float_prop(system, index, prop, default=0.0):
    try:
        return float(system.getFloatTrackedDeviceProperty(index, prop))
    except Exception:
        return default


def _safe_int_prop(system, index, prop, default=0):
    try:
        return int(system.getInt32TrackedDeviceProperty(index, prop))
    except Exception:
        return default


def _safe_string_prop(system, index, prop, default=""):
    try:
        value = system.getStringTrackedDeviceProperty(index, prop)
        return str(value or default).strip()
    except Exception:
        return default


def _device_reading(system, index, label):
    provides_battery = _safe_bool_prop(system, index, openvr.Prop_DeviceProvidesBatteryStatus_Bool, False)
    percent = _safe_float_prop(system, index, openvr.Prop_DeviceBatteryPercentage_Float, -1.0)
    charging = _safe_bool_prop(system, index, openvr.Prop_DeviceIsCharging_Bool, False)
    model = _safe_string_prop(system, index, openvr.Prop_ModelNumber_String, "")

    has_reading = provides_battery and percent >= 0
    return {
        "label": label,
        "model": model,
        "connected": True,
        "has_battery": has_reading,
        "battery_percent": round(percent * 100) if has_reading else None,
        "charging": bool(charging) if has_reading else False,
    }


def _update_once():
    if IS_ANDROID:
        _update_once_android()
    elif OPENVR_AVAILABLE:
        _update_once_steamvr()
    elif PLYER_AVAILABLE:
        _update_once_plyer()
    else:
        with _lock:
            _state["status"] = "unavailable"
            _state["last_error"] = "No battery data source available on this platform."


def _update_once_android():
    try:
        from jnius import autoclass

        ActivityThread = autoclass("android.app.ActivityThread")
        BatteryManager = autoclass("android.os.BatteryManager")
        Context = autoclass("android.content.Context")
        Build = autoclass("android.os.Build")
        context = ActivityThread.currentApplication()
        if context is None:
            raise RuntimeError("Quest battery service is not ready yet.")
        manager = context.getSystemService(Context.BATTERY_SERVICE)
        if manager is None:
            raise RuntimeError("Quest battery service is unavailable.")
        percent = int(manager.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY))
        has_reading = 0 <= percent <= 100
        hmd = {
            "label": "Quest Headset",
            "model": str(Build.MODEL or "Meta Quest"),
            "connected": True,
            "has_battery": has_reading,
            "battery_percent": percent if has_reading else None,
            "charging": bool(manager.isCharging()) if has_reading else False,
        }
        with _lock:
            _state["steamvr_running"] = False
            _state["status"] = "active" if has_reading else "waiting"
            _state["last_error"] = "" if has_reading else "Quest battery percentage not reported yet."
            _state["hmd"] = hmd
            _state["controllers"] = []
            _state["trackers"] = []
            _state["updated_at"] = _now()
    except Exception as exc:
        reason = str(exc).strip() or type(exc).__name__
        with _lock:
            _state["status"] = "error"
            _state["last_error"] = reason
            _state["updated_at"] = _now()


def _update_once_plyer():
    try:
        status = _plyer_battery.status or {}
        percent = status.get("percentage")
        has_reading = percent is not None and percent >= 0
        hmd = {
            "label": "Headset",
            "model": "This device",
            "connected": True,
            "has_battery": has_reading,
            "battery_percent": round(percent) if has_reading else None,
            "charging": bool(status.get("isCharging")) if has_reading else False,
        }
        with _lock:
            _state["steamvr_running"] = False
            _state["status"] = "active" if has_reading else "waiting"
            _state["last_error"] = "" if has_reading else "Device battery percentage not reported yet."
            _state["hmd"] = hmd
            _state["controllers"] = []
            _state["trackers"] = []
            _state["updated_at"] = _now()
    except Exception as exc:
        with _lock:
            _state["status"] = "error"
            _state["last_error"] = str(exc)
            _state["updated_at"] = _now()


def _update_once_steamvr():
    try:
        system = _ensure_vr_system()
    except Exception as exc:
        _shutdown_vr_system()
        reason = str(exc).strip() or type(exc).__name__
        with _lock:
            _state["steamvr_running"] = False
            _state["status"] = "waiting"
            _state["last_error"] = f"SteamVR not detected ({reason}). Start SteamVR with your headset connected."
            _state["hmd"] = None
            _state["controllers"] = []
            _state["trackers"] = []
            _state["updated_at"] = _now()
        return

    try:
        hmd = None
        controllers = []
        trackers = []

        for index in range(openvr.k_unMaxTrackedDeviceCount):
            try:
                if not system.isTrackedDeviceConnected(index):
                    continue
                device_class = system.getTrackedDeviceClass(index)
            except Exception:
                continue

            if device_class == openvr.TrackedDeviceClass_HMD:
                hmd = _device_reading(system, index, "Headset")
            elif device_class == openvr.TrackedDeviceClass_Controller:
                role = _safe_int_prop(system, index, openvr.Prop_ControllerRoleHint_Int32, 0)
                controllers.append(_device_reading(system, index, _role_label(role)))
            elif device_class == openvr.TrackedDeviceClass_GenericTracker:
                trackers.append(_device_reading(system, index, f"Tracker {len(trackers) + 1}"))

        controllers.sort(key=lambda item: item["label"])

        with _lock:
            _state["steamvr_running"] = True
            _state["status"] = "active"
            _state["last_error"] = ""
            _state["hmd"] = hmd
            _state["controllers"] = controllers
            _state["trackers"] = trackers
            _state["updated_at"] = _now()
    except Exception as exc:
        _shutdown_vr_system()
        with _lock:
            _state["steamvr_running"] = False
            _state["status"] = "error"
            _state["last_error"] = str(exc)
            _state["updated_at"] = _now()


def _format_device(device):
    if not device:
        return ""
    if not device.get("has_battery"):
        return device["label"]
    charge = " (charging)" if device.get("charging") else ""
    return f"{device['label']} {device['battery_percent']}%{charge}"


def get_battery_text(include_controllers=True, include_trackers=False, low_battery_threshold=0):
    state = get_state()
    if not state.get("enabled") or state.get("status") not in {"active"}:
        return ""

    parts = []
    hmd = state.get("hmd")
    if hmd and hmd.get("has_battery"):
        parts.append(f"Headset {hmd['battery_percent']}%")

    if include_controllers:
        for controller in state.get("controllers", []):
            if not controller.get("has_battery"):
                continue
            short_label = controller["label"].replace(" Controller", "")
            parts.append(f"{short_label} {controller['battery_percent']}%")

    if include_trackers:
        for tracker in state.get("trackers", []):
            if tracker.get("has_battery"):
                parts.append(f"{tracker['label']} {tracker['battery_percent']}%")

    if not parts:
        return ""

    try:
        threshold = int(low_battery_threshold or 0)
    except Exception:
        threshold = 0
    if threshold > 0:
        all_devices = [state.get("hmd")] + state.get("controllers", []) + state.get("trackers", [])
        lowest = min(
            (d["battery_percent"] for d in all_devices if d and d.get("has_battery")),
            default=None,
        )
        if lowest is not None and lowest <= threshold:
            parts.append("LOW BATTERY")

    return " | ".join(parts)
