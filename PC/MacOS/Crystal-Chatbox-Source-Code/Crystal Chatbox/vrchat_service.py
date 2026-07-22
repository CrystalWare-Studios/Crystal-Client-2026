import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime
from urllib.parse import urlparse

import requests

from settings import DATA_DIR

VRCHAT_BASE = "https://api.vrchat.cloud/api/1"
SESSION_FILE = os.path.join(DATA_DIR, "vrchat_session.json")
USER_AGENT = "CrystalClient/1.0 (VRCX+)"

_lock = threading.RLock()
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
_pending_2fa = False
_pending_2fa_methods = []
_last_login_username = ""
_last_login_password = ""
_avatar_cache = {}
_avatar_cache_ttl_seconds = 6 * 60 * 60
_world_cache = {}
_instance_cache = {}
_world_cache_ttl_seconds = 30 * 60
_instance_cache_ttl_seconds = 60
_status_cache = {}
_status_cache_at = 0.0
_status_cache_ttl_seconds = 25
_status_stale_ttl_seconds = 10 * 60


def _now_iso():
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _save_session():
    payload = {
        "cookies": requests.utils.dict_from_cookiejar(_session.cookies),
        "saved_at": _now_iso()
    }
    with open(SESSION_FILE, "wb") as f:
        f.write(json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"))


def _load_session():
    if not os.path.exists(SESSION_FILE):
        return
    try:
        with open(SESSION_FILE, "rb") as f:
            payload = json.loads(f.read().decode("utf-8"))
        cookies = payload.get("cookies", {})
        _session.cookies = requests.utils.cookiejar_from_dict(cookies)
    except Exception:
        pass


def _request(method, path, **kwargs):
    url = f"{VRCHAT_BASE}{path}"
    return _session.request(method, url, timeout=20, **kwargs)


def _auth_required_message(status_code=401):
    methods = [str(method).strip() for method in _pending_2fa_methods if str(method).strip()]
    if _pending_2fa:
        method_label = ", ".join(methods) if methods else "totp/emailOtp"
        return f"VRChat 2FA required ({method_label}). Verify 2FA before retrying."
    return f"VRChat authorization required ({status_code}). Log in and complete 2FA in VRCX+."


def _store_login_credentials(username, password):
    global _last_login_username, _last_login_password
    _last_login_username = str(username or "").strip()
    _last_login_password = str(password or "")


def _clear_login_credentials():
    global _last_login_username, _last_login_password
    _last_login_username = ""
    _last_login_password = ""


def _clean_error_text(value):
    text = str(value or "").strip()
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        text = text[1:-1].strip()
    return text


def _first_non_empty(*values):
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
            continue
        return value
    return ""


def _extract_error_message(payload):
    if isinstance(payload, dict):
        error_value = payload.get("error")
        if isinstance(error_value, dict):
            nested = (
                error_value.get("message")
                or error_value.get("error")
                or error_value.get("detail")
                or error_value.get("reason")
            )
            nested_text = _clean_error_text(nested)
            if nested_text:
                return nested_text
        elif error_value is not None:
            error_text = _clean_error_text(error_value)
            if error_text:
                return error_text

        for key in ("message", "detail", "reason"):
            text = _clean_error_text(payload.get(key))
            if text:
                return text

    if isinstance(payload, str):
        return _clean_error_text(payload)
    return ""


def _is_2fa_required_error(message):
    text = str(message or "").lower()
    return (
        "two-factor" in text
        or "two factor" in text
        or "requires 2fa" in text
        or "2fa required" in text
    )


def _apply_2fa_hint_from_message(message):
    global _pending_2fa, _pending_2fa_methods
    if _is_2fa_required_error(message):
        _pending_2fa = True
        if not _pending_2fa_methods:
            _pending_2fa_methods = ["totp"]


def _user_payload_looks_authenticated(payload):
    if not isinstance(payload, dict):
        return False

    if _extract_2fa_methods(payload):
        return False
    if _is_2fa_required_error(_extract_error_message(payload)):
        return False

    user_id = str(payload.get("id") or "").strip()
    if user_id.startswith("usr_"):
        return True
    if str(payload.get("username") or "").strip():
        return True
    if str(payload.get("displayName") or "").strip():
        return True
    return False


def _extract_2fa_methods(payload):
    methods = []
    requires = payload.get("requiresTwoFactorAuth")
    methods_alt = payload.get("requiresTwoFactorAuthMethods")

    if isinstance(requires, list):
        methods = requires
    elif isinstance(methods_alt, list):
        methods = methods_alt
    elif isinstance(requires, str):
        methods = [requires]
    elif requires is True:
        methods = ["totp"]

    cleaned = []
    for method in methods:
        if not method:
            continue
        method_s = str(method).strip()
        if method_s and method_s not in cleaned:
            cleaned.append(method_s)
    return cleaned


def _append_platform_tokens(tokens, value):
    if value is None:
        return
    if isinstance(value, bool):
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _append_platform_tokens(tokens, item)
        return
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(nested, bool):
                if nested:
                    tokens.append(str(key))
            elif isinstance(nested, (int, float)):
                if nested:
                    tokens.append(str(key))
            else:
                _append_platform_tokens(tokens, nested)
        return

    text = str(value or "").strip()
    if not text or "://" in text:
        return
    for chunk in text.replace("|", ",").replace(";", ",").replace("/", ",").split(","):
        cleaned = chunk.strip()
        if cleaned:
            tokens.append(cleaned)


def _detect_avatar_platforms(item):
    if not isinstance(item, dict):
        return []

    tokens = []
    platform_keys = (
        "platform",
        "platforms",
        "supportedPlatforms",
        "supported_platforms",
        "releasePlatforms",
        "release_platforms",
        "targetPlatform",
        "targetPlatforms",
        "target_platforms",
        "compatibility",
        "devices",
        "deviceSupport",
        "tags",
        "labels"
    )
    for key in platform_keys:
        _append_platform_tokens(tokens, item.get(key))

    unity_packages = item.get("unityPackages")
    if unity_packages is None:
        unity_packages = item.get("unity_packages")
    _append_platform_tokens(tokens, unity_packages)

    pc = False
    quest = False
    phone = False

    for token in tokens:
        compact = "".join(ch for ch in str(token or "").strip().lower() if ch.isalnum())
        if not compact:
            continue

        if (
            compact in {
                "pc",
                "windows",
                "win",
                "desktop",
                "standalone",
                "standalonewindows",
                "windowsstandalone",
                "desktopvr",
                "steamvr"
            }
            or compact.startswith("standalonewindows")
            or compact.startswith("windows")
        ):
            pc = True

        if (
            compact in {"quest", "android", "androidquest", "questandroid", "standaloneandroid", "androidvr"}
            or "quest" in compact
            or compact.startswith("androidquest")
        ):
            quest = True

        if (
            compact in {"phone", "mobile", "ios", "iphone", "ipad", "androidmobile", "mobileandroid", "androidphone"}
            or "phone" in compact
            or "mobile" in compact
            or compact.startswith("ios")
        ):
            phone = True

    platforms = []
    if pc:
        platforms.append("PC")
    if quest:
        platforms.append("Quest")
    if phone:
        platforms.append("Phone")
    return platforms


def _normalize_avatar_result(item):
    if not isinstance(item, dict):
        return {}

    out = dict(item)
    avatar_id = _first_non_empty(
        item.get("id"),
        item.get("avatarId"),
        item.get("avatar_id"),
        item.get("avatarID"),
        item.get("external_id"),
        item.get("assetId")
    )
    name = _first_non_empty(
        item.get("name"),
        item.get("avatarName"),
        item.get("avatar_name"),
        item.get("displayName"),
        item.get("title")
    )
    author_name = _first_non_empty(
        item.get("authorName"),
        item.get("author"),
        item.get("author_name"),
        item.get("creatorName"),
        item.get("creator"),
        item.get("uploaderName")
    )
    status = _first_non_empty(
        item.get("releaseStatus"),
        item.get("release_status"),
        item.get("status"),
        item.get("visibility")
    )
    thumbnail = _first_non_empty(
        item.get("thumbnailImageUrl"),
        item.get("thumbnail"),
        item.get("thumbnail_url"),
        item.get("thumbnailURL"),
        item.get("previewImageUrl"),
        item.get("icon"),
        item.get("imageUrl"),
        item.get("image")
    )
    image = _first_non_empty(
        item.get("imageUrl"),
        item.get("image"),
        item.get("image_url"),
        item.get("imageURL"),
        item.get("assetUrl"),
        item.get("url"),
        thumbnail
    )
    description = _first_non_empty(
        item.get("description"),
        item.get("desc"),
        item.get("bio"),
        item.get("summary")
    )
    platforms = _detect_avatar_platforms(item)

    if avatar_id and not out.get("id"):
        out["id"] = str(avatar_id)
    if name and not out.get("name"):
        out["name"] = str(name)
    if author_name and not out.get("authorName"):
        out["authorName"] = str(author_name)
    if status and not out.get("releaseStatus"):
        out["releaseStatus"] = str(status)
    if thumbnail and not out.get("thumbnailImageUrl"):
        out["thumbnailImageUrl"] = str(thumbnail)
    if image and not out.get("imageUrl"):
        out["imageUrl"] = str(image)
    if description and not out.get("description"):
        out["description"] = str(description)
    if platforms:
        out["platforms"] = list(platforms)
        out["platformsText"] = ", ".join(platforms)
    return out


def _extract_external_avatar_results(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("results", "avatars", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = _extract_external_avatar_results(value)
                if nested:
                    return nested
    return []


def _extract_user_results(payload):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in (
            "results",
            "users",
            "players",
            "friends",
            "recent",
            "recentPlayers",
            "recent_players",
            "recentlySeen",
            "recently_seen",
            "data"
        ):
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = _extract_user_results(value)
                if nested:
                    return nested
    return []


def _normalize_user_result(item):
    if not isinstance(item, dict):
        return {}

    out = dict(item)
    user_id = _first_non_empty(
        item.get("id"),
        item.get("userId"),
        item.get("user_id"),
        item.get("targetUserId"),
        item.get("ownerId")
    )
    display_name = _first_non_empty(
        item.get("displayName"),
        item.get("username"),
        item.get("name"),
        item.get("userDisplayName")
    )
    status = _first_non_empty(item.get("status"), item.get("state"), item.get("presence"))
    status_description = _first_non_empty(
        item.get("statusDescription"),
        item.get("status_description"),
        item.get("bio"),
        item.get("note")
    )
    location = _first_non_empty(
        item.get("location"),
        item.get("worldId"),
        item.get("world_id"),
        item.get("instanceId"),
        item.get("instance_id")
    )
    avatar_id = _first_non_empty(
        item.get("currentAvatarId"),
        item.get("currentAvatar"),
        item.get("avatarId"),
        item.get("avatar_id")
    )
    avatar_name = _first_non_empty(
        item.get("currentAvatarName"),
        item.get("avatarName"),
        item.get("avatar_name")
    )
    avatar_thumb = _first_non_empty(
        item.get("currentAvatarThumbnailImageUrl"),
        item.get("avatarThumbnailImageUrl"),
        item.get("thumbnailImageUrl"),
        item.get("thumbnail_url"),
        item.get("thumbnail")
    )
    avatar_image = _first_non_empty(
        item.get("currentAvatarImageUrl"),
        item.get("avatarImageUrl"),
        item.get("imageUrl"),
        item.get("image"),
        avatar_thumb
    )
    last_login = _first_non_empty(
        item.get("last_login"),
        item.get("lastLogin"),
        item.get("last_seen"),
        item.get("lastSeen"),
        item.get("updated_at"),
        item.get("created_at")
    )

    if user_id and not out.get("id"):
        out["id"] = str(user_id)
    if display_name and not out.get("displayName"):
        out["displayName"] = str(display_name)
    if status and not out.get("status"):
        out["status"] = str(status)
    if status_description and not out.get("statusDescription"):
        out["statusDescription"] = str(status_description)
    if location and not out.get("location"):
        out["location"] = str(location)
    if avatar_id and not out.get("currentAvatarId"):
        out["currentAvatarId"] = str(avatar_id)
    if avatar_name and not out.get("currentAvatarName"):
        out["currentAvatarName"] = str(avatar_name)
    if avatar_image and not out.get("currentAvatarImageUrl"):
        out["currentAvatarImageUrl"] = str(avatar_image)
    if avatar_thumb and not out.get("currentAvatarThumbnailImageUrl"):
        out["currentAvatarThumbnailImageUrl"] = str(avatar_thumb)
    if last_login and not out.get("last_login"):
        out["last_login"] = str(last_login)
    return out


def _provider_label(provider_url):
    text = str(provider_url or "").strip()
    if not text:
        return "provider"
    try:
        parsed = urlparse(text)
        host = str(parsed.netloc or "").strip()
        if host:
            return host
    except Exception:
        pass
    return text


def _external_avatar_dedupe_key(avatar):
    if not isinstance(avatar, dict):
        return ""
    avatar_id = str(avatar.get("id") or "").strip().lower()
    if avatar_id:
        return f"id:{avatar_id}"
    name = str(avatar.get("name") or "").strip().lower()
    author = str(avatar.get("authorName") or "").strip().lower()
    if name or author:
        return f"name:{name}|author:{author}"
    image = str(avatar.get("thumbnailImageUrl") or avatar.get("imageUrl") or "").strip().lower()
    if image:
        return f"image:{image}"
    return ""


def init():
    with _lock:
        _load_session()


def _cache_status_payload(payload):
    global _status_cache, _status_cache_at
    cached = deepcopy(payload if isinstance(payload, dict) else {})
    cached["checked_at"] = _now_iso()
    _status_cache = cached
    _status_cache_at = time.time()
    return deepcopy(cached)


def _fresh_cached_status(force=False):
    if force or not _status_cache:
        return None
    if (time.time() - _status_cache_at) >= _status_cache_ttl_seconds:
        return None
    cached = deepcopy(_status_cache)
    cached["cached"] = True
    return cached


def _stale_connected_status(error):
    if not _status_cache or not _status_cache.get("logged_in"):
        return None
    age = time.time() - _status_cache_at
    if age >= _status_stale_ttl_seconds:
        return None
    cached = deepcopy(_status_cache)
    cached["ok"] = True
    cached["logged_in"] = True
    cached["requires_2fa"] = False
    cached["cached"] = True
    cached["stale"] = True
    cached["warning"] = str(error or "Using the last good VRChat account check.")
    cached.pop("error", None)
    return cached


def _clear_status_cache():
    global _status_cache, _status_cache_at
    _status_cache = {}
    _status_cache_at = 0.0


def status(force=False):
    global _pending_2fa, _pending_2fa_methods
    with _lock:
        cached = _fresh_cached_status(force=force)
        if cached:
            return cached

        try:
            response = _request("GET", "/auth/user")
            payload = {}
            try:
                payload = response.json()
            except Exception:
                payload = {}

            error = _extract_error_message(payload)
            methods = _extract_2fa_methods(payload) if isinstance(payload, dict) else []

            if methods:
                _pending_2fa = True
                _pending_2fa_methods = methods
            _apply_2fa_hint_from_message(error)

            if response.status_code == 200:
                if _user_payload_looks_authenticated(payload):
                    _pending_2fa = False
                    _pending_2fa_methods = []
                    return _cache_status_payload(
                        {"ok": True, "logged_in": True, "user": payload, "requires_2fa": False, "methods": []}
                    )
                if _pending_2fa:
                    method_list = list(_pending_2fa_methods) if _pending_2fa_methods else ["totp"]
                    pending_error = _auth_required_message(401)
                    if error and _is_2fa_required_error(error):
                        pending_error = error
                    return {
                        "ok": True,
                        "logged_in": False,
                        "user": None,
                        "requires_2fa": True,
                        "methods": method_list,
                        "error": pending_error
                    }
                unknown_error = error or "VRChat auth state is incomplete. Re-login may be required."
                stale = _stale_connected_status(unknown_error)
                if stale:
                    return stale
                return {
                    "ok": True,
                    "logged_in": False,
                    "user": None,
                    "requires_2fa": False,
                    "methods": [],
                    "error": unknown_error
                }

            if not error and response.status_code == 401:
                error = _auth_required_message(response.status_code)
            if not error:
                error = f"VRChat auth check failed ({response.status_code})"
            stale = _stale_connected_status(error)
            if stale:
                return stale
            return {
                "ok": True,
                "logged_in": False,
                "user": None,
                "requires_2fa": bool(_pending_2fa),
                "methods": list(_pending_2fa_methods),
                "error": error
            }
        except Exception as e:
            stale = _stale_connected_status(str(e))
            if stale:
                return stale
            return {
                "ok": False,
                "error": str(e),
                "logged_in": False,
                "user": None,
                "requires_2fa": bool(_pending_2fa),
                "methods": list(_pending_2fa_methods)
            }


def current_user_location(force_refresh=False):
    with _lock:
        status_payload = status(force=force_refresh)
        payload = status_payload.get("user") if isinstance(status_payload, dict) else None
        if not status_payload.get("logged_in") or not isinstance(payload, dict):
            return {
                "ok": False,
                "error": status_payload.get("error")
                or status_payload.get("warning")
                or "Log in under VRChat Account to use account-based live instance detection.",
            }


        presence = payload.get("presence") if isinstance(payload.get("presence"), dict) else {}

        world_field = str(
            payload.get("worldId") or payload.get("world_id") or presence.get("world") or ""
        ).strip()
        instance_field = str(
            payload.get("instanceId") or payload.get("instance_id") or presence.get("instance") or ""
        ).strip()
        location = str(payload.get("location") or "").strip()
        traveling_to = str(
            payload.get("travelingToLocation") or presence.get("travelingToWorld") or ""
        ).strip()
        if (not location or location.lower() in {"offline", "private"}) and traveling_to:
            location = traveling_to
        if (not location or location.lower() in {"offline", "private"}) and world_field.startswith("wrld_"):
            location = f"{world_field}:{instance_field}" if instance_field else world_field

        world_id = ""
        instance_id = ""
        if location and ":" in location:
            world_id, instance_id = location.split(":", 1)
        elif location.startswith("wrld_"):
            world_id = location
        elif world_field.startswith("wrld_"):
            world_id = world_field
            instance_id = instance_field

        if not world_id:
            return {
                "ok": False,
                "error": "Your VRChat account has no current location. Open VRChat and join a world to use account-based live instance detection.",
            }


        return {
            "ok": True,
            "user": payload,
            "location": location,
            "world_id": world_id,
            "instance_id": instance_id,
            "cached": bool(status_payload.get("cached")),
            "stale": bool(status_payload.get("stale")),
        }


def login(username, password):
    global _pending_2fa, _pending_2fa_methods
    with _lock:
        try:
            _store_login_credentials(username, password)
            response = _request("GET", "/auth/user", auth=(username, password))
            payload = {}
            try:
                payload = response.json()
            except Exception:
                payload = {}

            methods = _extract_2fa_methods(payload)
            if not methods:
                login_error = _extract_error_message(payload)
                if _is_2fa_required_error(login_error):
                    methods = list(_pending_2fa_methods) if _pending_2fa_methods else ["totp"]
            if methods:
                _pending_2fa = True
                _pending_2fa_methods = methods
                _clear_status_cache()
                _save_session()
                email_sent = False
                email_error = ""
                if any(str(m).lower() == "emailotp" for m in methods):
                    sent_result = request_email_otp()
                    email_sent = bool(sent_result.get("ok"))
                    email_error = sent_result.get("error", "")
                return {
                    "ok": True,
                    "logged_in": False,
                    "requires_2fa": True,
                    "methods": methods,
                    "email_otp_sent": email_sent,
                    "email_otp_error": email_error
                }

            if response.status_code == 200 and _user_payload_looks_authenticated(payload):
                _pending_2fa = False
                _pending_2fa_methods = []
                _clear_login_credentials()
                _save_session()
                return _cache_status_payload({"ok": True, "logged_in": True, "user": payload})

            _pending_2fa = False
            _pending_2fa_methods = []
            _clear_status_cache()
            error = _extract_error_message(payload)
            _apply_2fa_hint_from_message(error)
            if _pending_2fa:
                return {
                    "ok": True,
                    "logged_in": False,
                    "requires_2fa": True,
                    "methods": list(_pending_2fa_methods),
                    "email_otp_sent": False,
                    "email_otp_error": ""
                }
            if not error:
                error = f"Login failed ({response.status_code})"
            return {"ok": False, "error": error, "requires_2fa": False, "methods": []}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def _retrigger_email_otp_by_relogin():
    global _pending_2fa, _pending_2fa_methods
    username = str(_last_login_username or "").strip()
    password = str(_last_login_password or "")
    if not username or not password:
        return {
            "ok": False,
            "error": "Email OTP resend requires re-login credentials. Re-enter username/password and click Login again."
        }
    try:
        _session.cookies.clear()
        response = _request("GET", "/auth/user", auth=(username, password))
        payload = {}
        try:
            payload = response.json()
        except Exception:
            payload = {}

        methods = _extract_2fa_methods(payload)
        if methods:
            _pending_2fa = True
            _pending_2fa_methods = methods
            if any(str(m).lower() == "emailotp" for m in methods):
                _save_session()
                return {"ok": True}
            return {"ok": False, "error": "Email OTP is not available for this login challenge. Use TOTP instead."}

        error = _extract_error_message(payload)
        _apply_2fa_hint_from_message(error)
        if _pending_2fa:
            methods_lower = [str(m).lower() for m in _pending_2fa_methods]
            if "emailotp" in methods_lower:
                _save_session()
                return {"ok": True}
            return {"ok": False, "error": "VRChat requires 2FA, but Email OTP is unavailable. Use TOTP."}

        if response.status_code == 200 and _user_payload_looks_authenticated(payload):
            return {"ok": False, "error": "Login is already completed. Email OTP resend is not needed."}

        return {"ok": False, "error": error or f"Email OTP resend failed ({response.status_code})"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def verify_2fa(code, method):
    global _pending_2fa, _pending_2fa_methods
    with _lock:
        if not _pending_2fa:
            return {"ok": False, "error": "2FA not pending"}
        method = (method or "").strip().lower()
        code_value = str(code).strip()
        if method == "emailotp":
            endpoint = "/auth/twofactorauth/emailotp/verify"
        elif method == "otp":
            endpoint = "/auth/twofactorauth/otp/verify"
            compact = code_value.replace("-", "").replace(" ", "")
            if compact.isdigit() and len(compact) == 8:
                code_value = f"{compact[:4]}-{compact[4:]}"
        else:
            endpoint = "/auth/twofactorauth/totp/verify"
            method = "totp"
        try:
            response = _request("POST", endpoint, json={"code": code_value})
            payload = {}
            try:
                payload = response.json()
            except Exception:
                payload = {}
            if response.status_code != 200:
                error = _extract_error_message(payload)
                if not error:
                    error = f"2FA failed ({response.status_code})"
                return {"ok": False, "error": error}

            _pending_2fa = False
            _pending_2fa_methods = []
            status_payload = status(force=True)
            if status_payload.get("logged_in"):
                _save_session()
                _clear_login_credentials()
                return {"ok": True, "logged_in": True, "user": status_payload.get("user")}
            return {"ok": False, "error": "2FA verify succeeded but login not established"}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def request_email_otp():
    with _lock:
        if not _pending_2fa:
            status_payload = status()
            if status_payload.get("logged_in"):
                return {"ok": False, "error": "Email OTP is only available during 2FA challenge; you are already logged in."}
            return {"ok": False, "error": "Email OTP unavailable. Start login first so 2FA is pending."}

        methods_lower = [str(method).strip().lower() for method in _pending_2fa_methods if str(method).strip()]
        if methods_lower and "emailotp" not in methods_lower:
            return {"ok": False, "error": "This 2FA challenge does not offer Email OTP. Use TOTP verification."}

        try:
            endpoints = ["/auth/twofactorauth/emailotp", "/auth/twofactorauth/emailotp/send"]
            last_error = "Email OTP request failed"
            should_retry_by_login = False
            for endpoint in endpoints:
                response = _request("POST", endpoint)
                payload = {}
                try:
                    payload = response.json()
                except Exception:
                    payload = {}
                if response.status_code in (200, 204):
                    return {"ok": True}
                err = _extract_error_message(payload) or f"Email OTP request failed ({response.status_code})"
                last_error = str(err)
                if (
                    response.status_code in (404, 405, 501)
                    or "not implemented" in last_error.lower()
                ):
                    should_retry_by_login = True

            if should_retry_by_login:
                retry = _retrigger_email_otp_by_relogin()
                if retry.get("ok"):
                    return {"ok": True}
                if retry.get("error"):
                    last_error = str(retry.get("error"))
            return {"ok": False, "error": last_error}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def logout():
    global _pending_2fa, _pending_2fa_methods
    with _lock:
        try:
            _request("PUT", "/logout")
        except Exception:
            pass
        _session.cookies.clear()
        _pending_2fa = False
        _pending_2fa_methods = []
        _clear_login_credentials()
        _clear_status_cache()
        try:
            if os.path.exists(SESSION_FILE):
                os.remove(SESSION_FILE)
        except Exception:
            pass
        return {"ok": True}


def avatar_search(query, n=40, offset=0):
    with _lock:
        params = {
            "search": str(query or ""),
            "n": max(1, min(int(n), 100)),
            "offset": max(0, int(offset))
        }
        try:
            response = _request("GET", "/avatars", params=params)
            payload = []
            try:
                payload = response.json()
            except Exception:
                payload = []
            if response.status_code != 200:
                error = _extract_error_message(payload)
                _apply_2fa_hint_from_message(error)
                if not error and response.status_code == 401:
                    error = _auth_required_message(response.status_code)
                if not error:
                    error = f"Avatar search failed ({response.status_code})"
                return {"ok": False, "error": error, "results": []}
            if not isinstance(payload, list):
                return {"ok": True, "results": []}
            normalized = [_normalize_avatar_result(item) for item in payload if isinstance(item, dict)]
            return {"ok": True, "results": normalized}
        except Exception as e:
            return {"ok": False, "error": str(e), "results": []}


def get_avatar(avatar_id, force_refresh=False):
    avatar_id = str(avatar_id or "").strip()
    if not avatar_id:
        return {"ok": False, "error": "Avatar id required"}

    with _lock:
        now_ts = time.time()
        cached = _avatar_cache.get(avatar_id)
        if (
            cached
            and not force_refresh
            and (now_ts - float(cached.get("fetched_at", 0.0))) < _avatar_cache_ttl_seconds
        ):
            return {"ok": True, "avatar": deepcopy(cached.get("avatar", {})), "cached": True}

        try:
            response = _request("GET", f"/avatars/{avatar_id}")
            payload = {}
            try:
                payload = response.json()
            except Exception:
                payload = {}

            if response.status_code != 200:
                error = _extract_error_message(payload)
                _apply_2fa_hint_from_message(error)
                if not error and response.status_code == 401:
                    error = _auth_required_message(response.status_code)
                if not error:
                    error = f"Avatar fetch failed ({response.status_code})"
                return {"ok": False, "error": error}

            if not isinstance(payload, dict):
                return {"ok": False, "error": "Avatar fetch returned invalid payload"}

            avatar = _normalize_avatar_result(payload)
            _avatar_cache[avatar_id] = {
                "avatar": deepcopy(avatar),
                "fetched_at": now_ts
            }
            return {"ok": True, "avatar": avatar}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def get_world(world_id, force_refresh=False):
    world_id = str(world_id or "").strip()
    if not world_id:
        return {"ok": False, "error": "World id required"}

    with _lock:
        now_ts = time.time()
        cached = _world_cache.get(world_id)
        if (
            cached
            and not force_refresh
            and (now_ts - float(cached.get("fetched_at", 0.0))) < _world_cache_ttl_seconds
        ):
            return {"ok": True, "world": deepcopy(cached.get("world", {})), "cached": True}

        try:
            response = _request("GET", f"/worlds/{world_id}")
            payload = {}
            try:
                payload = response.json()
            except Exception:
                payload = {}

            if response.status_code != 200:
                error = _extract_error_message(payload)
                _apply_2fa_hint_from_message(error)
                if not error and response.status_code == 401:
                    error = _auth_required_message(response.status_code)
                if not error:
                    error = f"World fetch failed ({response.status_code})"
                return {"ok": False, "error": error}

            if not isinstance(payload, dict):
                return {"ok": False, "error": "World fetch returned invalid payload"}

            _world_cache[world_id] = {"world": deepcopy(payload), "fetched_at": now_ts}
            return {"ok": True, "world": payload}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def get_world_instance(world_id, instance_id, force_refresh=False):
    world_id = str(world_id or "").strip()
    instance_id = str(instance_id or "").strip()
    if not world_id or not instance_id:
        return {"ok": False, "error": "World id and instance id required"}

    cache_key = f"{world_id}:{instance_id}"
    with _lock:
        now_ts = time.time()
        cached = _instance_cache.get(cache_key)
        if (
            cached
            and not force_refresh
            and (now_ts - float(cached.get("fetched_at", 0.0))) < _instance_cache_ttl_seconds
        ):
            return {"ok": True, "instance": deepcopy(cached.get("instance", {})), "cached": True}

        paths = (f"/worlds/{world_id}/{instance_id}", f"/instances/{world_id}:{instance_id}")
        failures = []
        for path in paths:
            try:
                response = _request("GET", path)
                payload = {}
                try:
                    payload = response.json()
                except Exception:
                    payload = {}

                if response.status_code == 200 and isinstance(payload, dict):
                    _instance_cache[cache_key] = {"instance": deepcopy(payload), "fetched_at": now_ts}
                    return {"ok": True, "instance": payload}

                error = _extract_error_message(payload) or str(response.status_code)
                failures.append(f"{path}: {error}")
                if response.status_code == 401:
                    _apply_2fa_hint_from_message(error)
                    return {"ok": False, "error": _auth_required_message(response.status_code)}
            except Exception as e:
                failures.append(f"{path}: {e}")

        return {"ok": False, "error": f"Instance fetch failed ({'; '.join(failures[:2])})"}


def select_avatar(avatar_id):
    avatar_id = str(avatar_id or "").strip()
    if not avatar_id:
        return {"ok": False, "error": "Avatar id required"}
    with _lock:
        try:
            response = _request("PUT", f"/avatars/{avatar_id}/select")
            payload = {}
            try:
                payload = response.json()
            except Exception:
                payload = {}
            if response.status_code not in (200, 204):
                error = _extract_error_message(payload)
                _apply_2fa_hint_from_message(error)
                if not error and response.status_code == 401:
                    error = _auth_required_message(response.status_code)
                if not error:
                    error = f"Avatar select failed ({response.status_code})"
                return {"ok": False, "error": error}
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def get_friends(n=100, offline=False):
    with _lock:
        try:
            try:
                requested_total = int(n)
            except Exception:
                requested_total = 100
            requested_total = max(1, min(requested_total, 500))


            page_size = min(50, requested_total)
            offset = 0
            pages_fetched = 0
            max_pages = 200
            friends = []
            seen_ids = set()
            offline_flag = str(bool(offline)).lower()

            while len(friends) < requested_total and pages_fetched < max_pages:
                pages_fetched += 1
                remaining = requested_total - len(friends)
                current_n = max(1, min(page_size, remaining))
                params = {"n": current_n, "offset": offset, "offline": offline_flag}
                response = _request("GET", "/auth/user/friends", params=params)
                payload = []
                try:
                    payload = response.json()
                except Exception:
                    payload = []

                if response.status_code != 200:
                    error = _extract_error_message(payload)
                    _apply_2fa_hint_from_message(error)
                    if not error and response.status_code == 401:
                        error = _auth_required_message(response.status_code)
                    if not error:
                        error = f"Friend fetch failed ({response.status_code})"
                    return {"ok": False, "error": error, "friends": []}

                if not isinstance(payload, list):
                    break
                if not payload:
                    break

                before_len = len(friends)
                for friend in payload:
                    if not isinstance(friend, dict):
                        continue
                    friend_id = str(friend.get("id") or "").strip()
                    if friend_id:
                        if friend_id in seen_ids:
                            continue
                        seen_ids.add(friend_id)
                    friends.append(friend)
                    if len(friends) >= requested_total:
                        break

                page_count = len(payload)
                if page_count < current_n:
                    break


                if len(friends) == before_len:
                    offset += current_n
                else:
                    offset += page_count

            return {"ok": True, "friends": friends[:requested_total]}
        except Exception as e:
            return {"ok": False, "error": str(e), "friends": []}


def get_user_profile(user_id):
    uid = str(user_id or "").strip()
    if not uid:
        return {"ok": False, "error": "User id required"}

    with _lock:
        try:
            response = _request("GET", f"/users/{uid}")
            payload = {}
            try:
                payload = response.json()
            except Exception:
                payload = {}

            if response.status_code != 200:
                error = _extract_error_message(payload)
                _apply_2fa_hint_from_message(error)
                if not error and response.status_code == 401:
                    error = _auth_required_message(response.status_code)
                if not error:
                    error = f"User fetch failed ({response.status_code})"
                return {"ok": False, "error": error}

            if not isinstance(payload, dict):
                return {"ok": False, "error": "User fetch returned invalid payload"}

            user = _normalize_user_result(payload)
            return {"ok": True, "user": user}
        except Exception as e:
            return {"ok": False, "error": str(e)}


def get_recent_players(n=100):
    with _lock:
        try:
            requested_total = int(n)
        except Exception:
            requested_total = 100
        requested_total = max(1, min(requested_total, 500))

        endpoints = [
            "/auth/user/recently-seen",
            "/auth/user/recent",
            "/auth/user/recentlySeen",
            "/auth/user/players/recent",
            "/auth/user/recently-seen-users",
            "/auth/user/recently-met"
        ]
        params = {"n": requested_total, "offset": 0}
        failures = []

        with _lock:
            for path in endpoints:
                try:
                    response = _request("GET", path, params=params)
                    payload = {}
                    try:
                        payload = response.json()
                    except Exception:
                        payload = {}

                    if response.status_code == 200:
                        if isinstance(payload, list):
                            rows = payload
                        else:
                            rows = _extract_user_results(payload)
                        normalized = []
                        seen_ids = set()
                        for item in rows:
                            if not isinstance(item, dict):
                                continue
                            user = _normalize_user_result(item)
                            user_id = str(user.get("id") or "").strip()
                            if not user_id or user_id in seen_ids:
                                continue
                            seen_ids.add(user_id)
                            normalized.append(user)
                            if len(normalized) >= requested_total:
                                break
                        return {"ok": True, "players": normalized, "endpoint": path}

                    if response.status_code == 401:
                        error = _extract_error_message(payload)
                        _apply_2fa_hint_from_message(error)
                        if not error:
                            error = _auth_required_message(response.status_code)
                        return {"ok": False, "error": error, "players": []}

                    if response.status_code in (404, 405, 501):
                        failures.append(f"{path}:{response.status_code}")
                        continue

                    err = _extract_error_message(payload) or f"Recent player fetch failed ({response.status_code})"
                    failures.append(f"{path}:{err}")
                except Exception as e:
                    failures.append(f"{path}:{e}")

        if failures:
            return {"ok": False, "error": f"No recent-player endpoint succeeded ({'; '.join(failures[:3])})", "players": []}
        return {"ok": False, "error": "No recent-player endpoint succeeded", "players": []}


def external_avatar_search(provider_url, query, n=40):
    url = str(provider_url or "").strip()
    if not url:
        return {"ok": False, "error": "Provider URL not set", "results": []}
    try:
        response = requests.get(
            url,
            params={"q": query, "query": query, "search": query, "n": max(1, min(int(n), 100))},
            timeout=25,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
        )
        payload = {}
        try:
            payload = response.json()
        except Exception:
            payload = {}
        if response.status_code != 200:
            return {"ok": False, "error": f"Provider search failed ({response.status_code})", "results": []}
        results_raw = _extract_external_avatar_results(payload)
        normalized = []
        for item in results_raw:
            if not isinstance(item, dict):
                continue
            avatar = _normalize_avatar_result(item)
            if not avatar:
                continue
            if not avatar.get("provider_url"):
                avatar["provider_url"] = url
            if not avatar.get("provider"):
                avatar["provider"] = _provider_label(url)
            normalized.append(avatar)
        return {"ok": True, "results": normalized, "provider_url": url}
    except Exception as e:
        return {"ok": False, "error": str(e), "results": []}


def external_avatar_search_many(provider_urls, query, n=40):
    urls = []
    seen_urls = set()
    for raw_url in provider_urls or []:
        url = str(raw_url or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        urls.append(url)

    if not urls:
        return {"ok": False, "error": "Provider URL not set", "results": [], "providers": [], "errors": []}

    try:
        requested_total = int(n)
    except Exception:
        requested_total = 40
    requested_total = max(1, min(requested_total, 100))
    per_provider_n = max(25, requested_total)

    merged = []
    seen_keys = set()
    provider_counts = {}
    provider_error_lookup = {}
    max_workers = max(1, min(len(urls), 6))
    future_to_url = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for url in urls:
            future = pool.submit(external_avatar_search, url, query, per_provider_n)
            future_to_url[future] = url

        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                payload = future.result()
            except Exception as e:
                payload = {"ok": False, "error": str(e), "results": []}

            if not payload.get("ok"):
                provider_error_lookup[url] = str(payload.get("error") or "Provider search failed")
                continue

            added = 0
            for item in payload.get("results", []):
                if not isinstance(item, dict):
                    continue
                avatar = _normalize_avatar_result(item)
                if not avatar:
                    continue
                if not avatar.get("provider_url"):
                    avatar["provider_url"] = url
                if not avatar.get("provider"):
                    avatar["provider"] = _provider_label(url)
                key = _external_avatar_dedupe_key(avatar)
                if key and key in seen_keys:
                    continue
                if key:
                    seen_keys.add(key)
                merged.append(avatar)
                added += 1

            provider_counts[url] = added

    provider_stats = []
    provider_errors = []
    for url in urls:
        label = _provider_label(url)
        if url in provider_error_lookup:
            provider_errors.append({"url": url, "provider": label, "error": provider_error_lookup[url]})
        provider_stats.append(
            {
                "url": url,
                "provider": label,
                "count": int(provider_counts.get(url, 0)),
                "ok": url not in provider_error_lookup
            }
        )

    if not merged:
        if provider_errors:
            first = provider_errors[0]
            return {
                "ok": False,
                "error": f"All provider searches failed ({len(provider_errors)}) - {first.get('error')}",
                "results": [],
                "providers": provider_stats,
                "errors": provider_errors
            }
        return {"ok": False, "error": "No provider results", "results": [], "providers": provider_stats, "errors": provider_errors}

    return {
        "ok": True,
        "results": merged[:requested_total],
        "providers": provider_stats,
        "errors": provider_errors
    }
