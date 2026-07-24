import json
import os
import sys
import threading
import uuid

import requests

from settings import DATA_DIR, SETTINGS
import github_updater

SERVER_BASE = "https://crystalwarestudios.com"

ACCOUNT_FILE = os.path.join(DATA_DIR, "crystalware_account.json")
INSTALL_ID_FILE = os.path.join(DATA_DIR, "install_id.txt")

SYNC_EXCLUDED_KEYS = {"quest_ip", "quest_port", "app_window"}

if "ANDROID_ARGUMENT" in os.environ:
    PLATFORM = "android"
elif sys.platform == "darwin":
    PLATFORM = "mac"
elif sys.platform == "win32":
    PLATFORM = "windows"
else:
    PLATFORM = "other"

_lock = threading.Lock()
_account = {
    "token": "", "account_id": "", "username": "", "avatar_url": "",
    "total_seconds": 0, "leaderboard_anonymous": False,
}
_push_timer = None
_PUSH_DEBOUNCE_SECONDS = 4.0


def _load_account():
    try:
        if os.path.exists(ACCOUNT_FILE):
            with open(ACCOUNT_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                _account.update({k: data.get(k, _account[k]) for k in _account})
    except Exception:
        pass


def _save_account():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(ACCOUNT_FILE, "w", encoding="utf-8") as f:
            json.dump(_account, f)
    except Exception:
        pass


_load_account()


def get_install_id():
    try:
        if os.path.exists(INSTALL_ID_FILE):
            with open(INSTALL_ID_FILE, "r", encoding="utf-8") as f:
                existing = f.read().strip()
                if existing:
                    return existing
    except Exception:
        pass
    new_id = uuid.uuid4().hex
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(INSTALL_ID_FILE, "w", encoding="utf-8") as f:
            f.write(new_id)
    except Exception:
        pass
    return new_id


def _app_version():
    try:
        return github_updater.get_current_version()
    except Exception:
        return "unknown"


# --- CrystalWare account (Discord login + settings sync) ---

def is_logged_in():
    return bool(_account.get("token"))


def get_account_info():
    with _lock:
        return dict(_account)


def login_url(return_to):
    return f"{SERVER_BASE}/account/discord/login?return_to={requests.utils.quote(return_to, safe='')}"


def _auth_headers():
    return {"Authorization": f"Bearer {_account.get('token', '')}"}


def complete_login(token):
    with _lock:
        _account["token"] = token
    try:
        resp = requests.get(f"{SERVER_BASE}/account/me", headers=_auth_headers(), timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            with _lock:
                _account["account_id"] = data.get("account_id", "")
                _account["username"] = data.get("username", "")
                _account["avatar_url"] = data.get("avatar_url", "")
                _account["total_seconds"] = data.get("total_seconds", 0)
                _account["leaderboard_anonymous"] = data.get("leaderboard_anonymous", False)
    except Exception:
        pass
    _save_account()
    return get_account_info()


def logout():
    token = _account.get("token", "")
    with _lock:
        _account.update({
            "token": "", "account_id": "", "username": "", "avatar_url": "",
            "total_seconds": 0, "leaderboard_anonymous": False,
        })
    _save_account()
    if token:
        try:
            requests.post(f"{SERVER_BASE}/account/logout", headers={"Authorization": f"Bearer {token}"}, timeout=10)
        except Exception:
            pass


def syncable_settings():
    return {k: v for k, v in dict(SETTINGS).items() if k not in SYNC_EXCLUDED_KEYS}


def push_settings():
    if not is_logged_in():
        return False, "Not logged in."
    try:
        resp = requests.put(
            f"{SERVER_BASE}/account/settings",
            headers=_auth_headers(),
            json=syncable_settings(),
            timeout=20,
        )
        if resp.status_code == 200:
            return True, ""
        return False, f"Server returned {resp.status_code}."
    except Exception as e:
        return False, str(e)


def pull_settings():
    if not is_logged_in():
        return None, "Not logged in."
    try:
        resp = requests.get(f"{SERVER_BASE}/account/settings", headers=_auth_headers(), timeout=20)
        if resp.status_code != 200:
            return None, f"Server returned {resp.status_code}."
        blob = resp.json().get("settings")
        if not blob:
            return None, "No settings saved on your account yet."
        return {k: v for k, v in blob.items() if k not in SYNC_EXCLUDED_KEYS}, ""
    except Exception as e:
        return None, str(e)


def send_heartbeat(seconds):
    if not is_logged_in():
        return
    try:
        resp = requests.post(
            f"{SERVER_BASE}/account/heartbeat",
            headers=_auth_headers(),
            json={"seconds": seconds},
            timeout=10,
        )
        if resp.status_code == 200:
            with _lock:
                _account["total_seconds"] = resp.json().get("total_seconds", _account["total_seconds"])
            _save_account()
    except Exception:
        pass


def get_uptime_seconds():
    return int(_account.get("total_seconds", 0) or 0)


def set_leaderboard_visibility(anonymous):
    if not is_logged_in():
        return False, "Not logged in."
    try:
        resp = requests.post(
            f"{SERVER_BASE}/account/leaderboard-visibility",
            headers=_auth_headers(),
            json={"anonymous": bool(anonymous)},
            timeout=10,
        )
        if resp.status_code != 200:
            return False, f"Server returned {resp.status_code}."
        with _lock:
            _account["leaderboard_anonymous"] = bool(anonymous)
        _save_account()
        return True, ""
    except Exception as e:
        return False, str(e)


def get_now_playing():
    if not is_logged_in():
        return None
    try:
        resp = requests.get(f"{SERVER_BASE}/account/now-playing", headers=_auth_headers(), timeout=10)
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception:
        return None


def get_leaderboard():
    try:
        resp = requests.get(f"{SERVER_BASE}/leaderboard", timeout=10)
        if resp.status_code != 200:
            return []
        return resp.json().get("entries", [])
    except Exception:
        return []


def _do_push():
    push_settings()


def schedule_push():
    global _push_timer
    if not is_logged_in():
        return
    with _lock:
        if _push_timer and _push_timer.is_alive():
            return
        _push_timer = threading.Timer(_PUSH_DEBOUNCE_SECONDS, _do_push)
        _push_timer.daemon = True
        _push_timer.start()


# --- Anonymous crash + usage reporting (opt-in via diagnostics_opt_in) ---

def _diagnostics_enabled():
    return bool(SETTINGS.get("diagnostics_opt_in", False))


def report_crash(message):
    if not _diagnostics_enabled():
        return
    try:
        requests.post(
            f"{SERVER_BASE}/report/crash",
            json={
                "install_id": get_install_id(),
                "app_version": _app_version(),
                "platform": PLATFORM,
                "message": message,
            },
            timeout=4,
        )
    except Exception:
        pass


def _send_ping():
    if not _diagnostics_enabled():
        return
    try:
        requests.post(
            f"{SERVER_BASE}/report/ping",
            json={
                "install_id": get_install_id(),
                "app_version": _app_version(),
                "platform": PLATFORM,
            },
            timeout=10,
        )
    except Exception:
        pass


def send_ping_async():
    threading.Thread(target=_send_ping, daemon=True).start()
