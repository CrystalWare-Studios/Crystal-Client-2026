
import threading

try:
    import keyboard as _keyboard
    KEYBOARD_AVAILABLE = True
except Exception:
    KEYBOARD_AVAILABLE = False

_lock = threading.Lock()
_send_callback = None
_registered_combos = set()
_status = {"enabled": False, "active_count": 0, "error": ""}


def set_send_callback(fn):
    global _send_callback
    _send_callback = fn


def get_status():
    with _lock:
        return dict(_status)


def _make_handler(phrase_text):
    def handler():
        if _send_callback is not None:
            try:
                _send_callback(phrase_text)
            except Exception as e:
                print(f"[Global Hotkeys] Send callback failed: {e}")
    return handler


def _clear_registered():
    for combo in list(_registered_combos):
        try:
            _keyboard.remove_hotkey(combo)
        except (KeyError, ValueError):
            pass
    _registered_combos.clear()


def configure(enabled, hotkeys):
    if not KEYBOARD_AVAILABLE:
        with _lock:
            _status["enabled"] = False
            _status["error"] = "The 'keyboard' package is not installed"
        return

    with _lock:
        _clear_registered()
        if not enabled:
            _status["enabled"] = False
            _status["active_count"] = 0
            _status["error"] = ""
            return

        error = ""
        active = 0
        for entry in hotkeys or []:
            combo = str((entry or {}).get("combo", "")).strip().lower()
            phrase = str((entry or {}).get("phrase", "")).strip()
            if not combo or not phrase:
                continue
            try:
                _keyboard.add_hotkey(combo, _make_handler(phrase))
                _registered_combos.add(combo)
                active += 1
            except Exception as e:
                error = f"Could not register '{combo}': {e}"
        _status["enabled"] = True
        _status["active_count"] = active
        _status["error"] = error


def shutdown():
    if not KEYBOARD_AVAILABLE:
        return
    with _lock:
        _clear_registered()
        _status["enabled"] = False
        _status["active_count"] = 0
