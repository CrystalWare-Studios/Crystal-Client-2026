import os
import re
import threading
import time
from collections import deque
from copy import deepcopy
from datetime import datetime

try:
    import psutil
except Exception:
    psutil = None


LOG_SCAN_LIMIT_BYTES = 2 * 1024 * 1024
EVENT_LIMIT = 120

WORLD_RE = re.compile(r"(wrld_[0-9a-fA-F-]{8}-[0-9a-fA-F-]{4}-[0-9a-fA-F-]{4}-[0-9a-fA-F-]{4}-[0-9a-fA-F-]{12})(?::([^\s\]\"']+))?")
JOIN_RE = re.compile(r"OnPlayerJoined\s+(.+?)(?:\s+\((usr_[^)]+)\))?\s*$")
LEAVE_RE = re.compile(r"OnPlayerLeft\s+(.+?)(?:\s+\((usr_[^)]+)\))?\s*$")


_lock = threading.RLock()
_thread = None
_stop_event = threading.Event()
_configured_enabled = True
_configured_log_dir = ""
_last_read_path = ""
_last_read_offset = 0
_pending_partial_skip = False

_state = {
    "enabled": True,
    "status": "starting",
    "source": "VRChat output log",
    "log_dir": "",
    "log_file": "",
    "log_candidates": 0,
    "readable_log_candidates": 0,
    "log_mtime": 0,
    "last_error": "",
    "world_id": "",
    "world_name": "",
    "author_name": "",
    "capacity": 0,
    "instance_id": "",
    "instance_short": "",
    "instance_privacy": "",
    "location": "",
    "player_count": 0,
    "players": [],
    "last_join": "",
    "last_leave": "",
    "last_event": "",
    "events": [],
    "updated_at": "",
    "lines_scanned": 0,
    "bytes_read": 0,
    "last_line_preview": "",
    "vrchat_process_running": False,
}

_players = {}
_events = deque(maxlen=EVENT_LIMIT)
_last_process_check = 0.0
_process_check_interval = 2.5


def _is_vrchat_running():


    if psutil is None:
        return False
    try:
        for proc in psutil.process_iter(["name"]):
            name = str((proc.info or {}).get("name") or "").lower()
            if name in ("vrchat.exe", "vrchat"):
                return True
    except Exception:
        return False
    return False


def _now_iso():
    return datetime.now().replace(microsecond=0).isoformat()


def default_log_dir():
    profile = os.environ.get("USERPROFILE", "")
    if profile:
        return os.path.join(profile, "AppData", "LocalLow", "VRChat", "VRChat")
    return os.path.join(os.path.expanduser("~"), "AppData", "LocalLow", "VRChat", "VRChat")


def configure(enabled=True, log_dir=""):
    global _configured_enabled, _configured_log_dir
    with _lock:
        _configured_enabled = bool(enabled)
        _configured_log_dir = str(log_dir or "").strip()
        _state["enabled"] = _configured_enabled
        _state["log_dir"] = _configured_log_dir or default_log_dir()
        if not _configured_enabled:
            _state["status"] = "disabled"


def start_tracker(enabled=True, log_dir="", interval=0.5):
    global _thread
    configure(enabled=enabled, log_dir=log_dir)
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()

    _thread = threading.Thread(target=_tail_loop, args=(max(0.25, float(interval or 0.5)),), daemon=True)
    _thread.start()


def stop_tracker():
    _stop_event.set()


def refresh_now():
    with _lock:
        _reset_reader_locked()
    _scan_once()
    return get_state()


def apply_account_location(location="", world_id="", instance_id="", source="VRChat account"):
    world = str(world_id or "").strip()
    instance = str(instance_id or "").strip()
    raw_location = str(location or "").strip()
    if not world and raw_location.startswith("wrld_"):
        if ":" in raw_location:
            world, instance = raw_location.split(":", 1)
        else:
            world = raw_location
    if not world:
        return False
    _set_location(world, instance)
    with _lock:
        _state["status"] = "active"
        _state["source"] = str(source or "VRChat account")
        _state["last_error"] = ""
        _state["updated_at"] = _now_iso()
    return True


def clear_location():
    with _lock:
        _players.clear()
        _events.clear()
        _state.update(
            {
                "world_id": "",
                "world_name": "",
                "author_name": "",
                "capacity": 0,
                "instance_id": "",
                "instance_short": "",
                "instance_privacy": "",
                "location": "",
                "player_count": 0,
                "players": [],
                "last_join": "",
                "last_leave": "",
                "last_event": "",
                "events": [],
                "source": "VRChat output log",
                "updated_at": _now_iso(),
            }
        )


def get_state():
    with _lock:
        out = deepcopy(_state)
        out["events"] = list(_events)
        out["players"] = sorted(_players.values(), key=lambda item: str(item.get("display_name", "")).lower())
        parsed_count = len(_players)
        out["player_count"] = parsed_count if parsed_count else _coerce_int(_state.get("player_count") or 0)
        return out


def apply_world_details(world=None, instance=None):
    with _lock:
        if isinstance(world, dict):
            name = str(world.get("name") or "").strip()
            author = str(world.get("authorName") or world.get("author_name") or "").strip()
            capacity = _coerce_int(world.get("capacity") or world.get("recommendedCapacity") or 0)
            if name:
                _state["world_name"] = name
            if author:
                _state["author_name"] = author
            if capacity > 0:
                _state["capacity"] = capacity
        if isinstance(instance, dict):
            instance_name = str(instance.get("name") or instance.get("instanceName") or "").strip()
            capacity = _coerce_int(instance.get("capacity") or 0)
            occupant_count = _coerce_int(instance.get("n_users") or instance.get("userCount") or instance.get("occupants") or 0)
            if instance_name:
                _state["instance_privacy"] = instance_name
            if capacity > 0:
                _state["capacity"] = capacity
            if occupant_count > 0:
                _state["player_count"] = occupant_count
        _state["updated_at"] = _now_iso()


def _tail_loop(interval):
    while not _stop_event.is_set():
        try:
            _scan_once()
        except Exception as exc:
            with _lock:
                _state["status"] = "error"
                _state["last_error"] = str(exc)
        _stop_event.wait(interval)


def _scan_once():
    global _last_read_path, _last_read_offset, _pending_partial_skip, _last_process_check
    with _lock:
        enabled = _configured_enabled
        log_dir = _configured_log_dir or default_log_dir()
        _state["enabled"] = enabled
        _state["log_dir"] = log_dir
        if not enabled:
            _state["status"] = "disabled"
            return

    now_ts = time.time()
    if now_ts - _last_process_check >= _process_check_interval:
        _last_process_check = now_ts
        running = _is_vrchat_running()
        with _lock:
            _state["vrchat_process_running"] = running

    path, candidate_count, readable_count = _latest_log_file(log_dir)
    with _lock:
        _state["log_candidates"] = candidate_count
        _state["readable_log_candidates"] = readable_count
        if not path:
            _state["log_file"] = ""
            if _state.get("world_id"):
                _state["status"] = "active"
                _state["updated_at"] = _now_iso()
                return
            _state["status"] = "waiting"
            if candidate_count:
                _state["last_error"] = f"Found {candidate_count} VRChat log file(s), but none contain readable data yet. Open or rejoin a VRChat world so VRChat writes to the log."
            else:
                _state["last_error"] = f"No VRChat output logs found in {log_dir}. Start PC VRChat once, then refresh."
            return

        if path != _last_read_path:
            _last_read_path = path
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            _last_read_offset = 0 if size <= LOG_SCAN_LIMIT_BYTES else max(0, size - LOG_SCAN_LIMIT_BYTES)


            _pending_partial_skip = _last_read_offset > 0
            _players.clear()
            _events.clear()
            _state.update(
                {
                    "status": "reading",
                    "log_file": path,
                    "log_mtime": _safe_mtime(path),
                    "last_error": "",
                    "world_id": "",
                    "world_name": "",
                    "author_name": "",
                    "capacity": 0,
                    "instance_id": "",
                    "instance_short": "",
                    "instance_privacy": "",
                    "location": "",
                    "player_count": 0,
                    "players": [],
                    "last_join": "",
                    "last_leave": "",
                    "last_event": "",
                    "events": [],
                    "updated_at": _now_iso(),
                    "lines_scanned": 0,
                    "bytes_read": 0,
                    "last_line_preview": "",
                }
            )

        offset = _last_read_offset
        skip_partial_line = _pending_partial_skip
        _pending_partial_skip = False

    lines, next_offset = _read_new_lines(path, offset, skip_partial_line)
    non_empty_lines = [line for line in lines if str(line or "").strip()]
    for line in lines:
        _process_line(line)

    with _lock:
        _last_read_offset = next_offset
        _state["log_file"] = path
        _state["log_mtime"] = _safe_mtime(path)
        _state["bytes_read"] = next_offset
        if non_empty_lines:
            _state["lines_scanned"] = _state.get("lines_scanned", 0) + len(non_empty_lines)
            _state["last_line_preview"] = non_empty_lines[-1][-200:]
        parsed_count = len(_players)
        if parsed_count or not _state.get("player_count"):
            _state["player_count"] = parsed_count
        _state["players"] = sorted(_players.values(), key=lambda item: str(item.get("display_name", "")).lower())
        _state["events"] = list(_events)
        _state["status"] = "active" if _state.get("world_id") or _events else "waiting for game data"
        _state["updated_at"] = _now_iso()


def _latest_log_file(log_dir):


    try:
        candidates = []
        candidate_count = 0
        readable_count = 0
        for name in os.listdir(log_dir):
            lower = name.lower()
            if (
                (lower.startswith("output_log") and lower.endswith(".txt"))
                or lower in {"player.log", "player-prev.log"}
            ):
                candidate_count += 1
                path = os.path.join(log_dir, name)
                try:
                    mtime = os.path.getmtime(path)
                    readable_count += 1
                except OSError:
                    continue
                candidates.append((mtime, path))
        if not candidates:
            return "", candidate_count, 0
        candidates.sort(reverse=True)
        return candidates[0][1], candidate_count, readable_count
    except OSError:
        return "", 0, 0


def _read_new_lines(path, offset, skip_partial_line=False):
    try:
        size = os.path.getsize(path)
        if size < offset:
            offset = 0
        with open(path, "rb") as f:
            f.seek(offset)
            if offset > 0 and skip_partial_line:
                f.readline()
            data = f.read(LOG_SCAN_LIMIT_BYTES)
            next_offset = f.tell()
        text = data.decode("utf-8", errors="replace")
        return text.splitlines(), next_offset
    except OSError:
        return [], offset


def _process_line(line):
    text = str(line or "").strip()
    if not text:
        return

    world_match = WORLD_RE.search(text)
    if world_match and _looks_like_location_line(text):
        _set_location(world_match.group(1), world_match.group(2) or "")
        return

    join_match = JOIN_RE.search(text)
    if join_match:
        _player_event("join", join_match.group(1), join_match.group(2) or "")
        return

    leave_match = LEAVE_RE.search(text)
    if leave_match:
        _player_event("leave", leave_match.group(1), leave_match.group(2) or "")


def _looks_like_location_line(text):
    lower = text.lower()
    if "wrld_" not in lower:
        return False
    hints = (
        "entering room",
        "joining",
        "location:",
        "switching to room",
        "joining or creating",
        "vrcflownetworkmanager",
        "network manager",
    )
    return any(hint in lower for hint in hints)


def _set_location(world_id, instance_id):
    with _lock:
        location = f"{world_id}:{instance_id}" if instance_id else world_id
        if location == _state.get("location"):
            return
        _players.clear()
        _state["world_id"] = world_id
        _state["instance_id"] = instance_id
        _state["instance_short"] = _instance_short(instance_id)
        _state["instance_privacy"] = _instance_privacy(instance_id)
        _state["location"] = location
        _state["world_name"] = ""
        _state["author_name"] = ""
        _state["capacity"] = 0
        _state["player_count"] = 0
        _state["last_join"] = ""
        _state["last_leave"] = ""
        detail = _state["instance_privacy"] or _state["instance_short"] or "instance"
        _append_event_locked("location", "Joined instance", detail)


def _player_event(kind, raw_name, user_id):
    name = _clean_player_name(raw_name)
    if not name:
        return
    now = _now_iso()
    with _lock:
        key = user_id or name.lower()
        if kind == "join":
            _players[key] = {
                "id": user_id,
                "display_name": name,
                "joined_at": now,
            }
            _state["last_join"] = name
            _append_event_locked("join", f"{name} joined the instance", "")
        else:
            _players.pop(key, None)
            _state["last_leave"] = name
            _append_event_locked("leave", f"{name} left the instance", "")
        _state["player_count"] = len(_players)


def _append_event_locked(kind, title, detail):
    event = {
        "kind": kind,
        "title": str(title or ""),
        "detail": str(detail or ""),
        "created_at": _now_iso(),
    }
    _events.appendleft(event)
    _state["last_event"] = event["title"]
    _state["updated_at"] = event["created_at"]


def _clean_player_name(raw):
    name = str(raw or "").strip()
    if not name:
        return ""
    name = re.sub(r"^\[[^\]]+\]\s*", "", name).strip()
    name = re.sub(r"\s+\(usr_[^)]+\)\s*$", "", name).strip()
    return name[:80]


def _instance_short(instance_id):
    text = str(instance_id or "").strip()
    if not text:
        return ""
    return text.split("~", 1)[0]


def _instance_privacy(instance_id):
    text = str(instance_id or "").lower()
    if not text:
        return ""
    if "~group(" in text or "group" in text:
        return "Group"
    if "~private(" in text:
        return "Invite"
    if "~hidden(" in text:
        return "Friends"
    if "~friends(" in text:
        return "Friends+"
    if text.startswith("offline"):
        return "Offline"
    return "Public"


def _reset_reader_locked():
    global _last_read_path, _last_read_offset
    _last_read_path = ""
    _last_read_offset = 0


def _safe_mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


def _coerce_int(value):
    try:
        return int(value)
    except Exception:
        return 0
