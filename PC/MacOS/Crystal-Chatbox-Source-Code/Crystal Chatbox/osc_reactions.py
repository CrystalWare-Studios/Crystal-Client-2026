
import threading
import time

try:
    from pythonosc.dispatcher import Dispatcher
    from pythonosc.osc_server import ThreadingOSCUDPServer
    OSC_SERVER_AVAILABLE = True
except ImportError:
    OSC_SERVER_AVAILABLE = False

from settings import SETTINGS

_lock = threading.Lock()
_server = None
_server_thread = None
_reaction_callback = None
_last_fired = {}
_mute_state = {"muted": False, "changed_at": 0}
_last_avatar_id = ""
_listener_status = {"running": False, "port": 0, "error": ""}


def set_reaction_callback(fn):
    global _reaction_callback
    _reaction_callback = fn


def _fire_reaction(message, duration=None):
    if not message or _reaction_callback is None:
        return
    try:
        _reaction_callback(message, duration or SETTINGS.get("reaction_display_seconds", 6))
    except Exception as e:
        print(f"[OSC Reactions] Reaction callback failed: {e}")


def is_muted():
    with _lock:
        return bool(_mute_state["muted"])


def get_status():
    with _lock:
        return {
            "running": _listener_status["running"],
            "port": _listener_status["port"],
            "error": _listener_status["error"],
            "muted": _mute_state["muted"],
            "last_avatar_id": _last_avatar_id,
        }


def _values_match(incoming, expected):
    if expected is None or expected == "":
        return False
    try:
        return abs(float(incoming) - float(expected)) < 0.001
    except (TypeError, ValueError):
        return str(incoming).strip().lower() == str(expected).strip().lower()


def _handle_parameter(address, *args):
    value = args[0] if args else None

    if address == "/avatar/parameters/MuteSelf":
        muted = bool(value)
        with _lock:
            changed = muted != _mute_state["muted"]
            _mute_state["muted"] = muted
            if changed:
                _mute_state["changed_at"] = time.time()
        return

    if not SETTINGS.get("reaction_rules"):
        return
    now = time.time()
    for rule in SETTINGS.get("reaction_rules", []):
        if not isinstance(rule, dict) or not rule.get("enabled", True):
            continue
        if str(rule.get("address", "")).strip() != address:
            continue
        if not _values_match(value, rule.get("trigger_value")):
            continue
        rule_id = rule.get("id") or rule.get("address")
        cooldown = max(1, int(rule.get("cooldown_seconds", 10) or 10))
        if now - _last_fired.get(rule_id, 0) < cooldown:
            continue
        _last_fired[rule_id] = now
        _fire_reaction(rule.get("message", ""))


def _handle_avatar_change(address, *args):
    global _last_avatar_id
    avatar_id = str(args[0]) if args else ""
    with _lock:
        previous_avatar_id = _last_avatar_id
        _last_avatar_id = avatar_id
    if avatar_id and avatar_id == previous_avatar_id:
        return
    if SETTINGS.get("avatar_change_announce_enabled", False):
        _fire_reaction(SETTINGS.get("avatar_change_message", "Just switched avatars! ✨"))


def start_listener(port=9001):
    global _server, _server_thread

    if not OSC_SERVER_AVAILABLE:
        with _lock:
            _listener_status["running"] = False
            _listener_status["error"] = "python-osc server support not available"
        return

    with _lock:
        if _listener_status["running"] and _listener_status["port"] == port:
            return

    stop_listener()

    try:
        dispatcher = Dispatcher()
        dispatcher.map("/avatar/parameters/*", _handle_parameter)
        dispatcher.map("/avatar/change", _handle_avatar_change)
        server = ThreadingOSCUDPServer(("127.0.0.1", port), dispatcher)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        with _lock:
            _server = server
            _server_thread = thread
            _listener_status["running"] = True
            _listener_status["port"] = port
            _listener_status["error"] = ""
        print(f"[OSC Reactions] Listening for VRChat avatar OSC on 127.0.0.1:{port}")
    except OSError as e:
        with _lock:
            _listener_status["running"] = False
            _listener_status["error"] = f"Could not bind port {port}: {e}"
        print(f"[OSC Reactions] Failed to start listener: {e}")


def stop_listener():
    global _server, _server_thread
    with _lock:
        server = _server
        _server = None
        _server_thread = None
        _listener_status["running"] = False
    if server is not None:
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass
