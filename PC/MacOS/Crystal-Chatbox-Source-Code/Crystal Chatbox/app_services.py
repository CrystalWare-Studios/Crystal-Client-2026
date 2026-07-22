import json
import os
import time
import uuid
import zipfile
from copy import deepcopy
from datetime import datetime

from settings import (
    BACKUP_DIR,
    DATA_DIR,
    DEFAULTS,
    SENSITIVE_KEYS,
    SETTINGS,
    SETTINGS_FILE,
    create_backup,
    public_settings,
    redact_sensitive,
    save_settings,
)
import message_history
import profiles_manager


APP_LOG_FILE = os.path.join(DATA_DIR, "app_events.jsonl")
EXPORT_DIR = os.path.join(DATA_DIR, "exports")

_MAX_LOG_BYTES = 2 * 1024 * 1024
_TRIM_TO_BYTES = 1 * 1024 * 1024

PROFILE_SETTING_KEYS = [
    key for key in DEFAULTS
    if key not in SENSITIVE_KEYS
    and key not in {
        "schema_version",
        "setup_completed",
        "spotify_client_id",
        "spotify_redirect_uri",
        "spotify_client_secret",
        "heart_rate_pulsoid_token",
        "heart_rate_hyperate_id",
        "heart_rate_custom_api",
    }
]

SEARCH_INDEX = [
    {"section": "Home", "target": "home", "title": "Dashboard", "keywords": "status connection current message quick actions errors warnings"},
    {"section": "Chatbox", "target": "chatbox", "title": "Message editor", "keywords": "message send clear preview character duration resend variables templates favorites history"},
    {"section": "Presets", "target": "presets", "title": "Presets", "keywords": "preset templates duplicate export import priority profile"},
    {"section": "Automations", "target": "automations", "title": "Automations", "keywords": "rotating timed away idle random scheduled priority conflict"},
    {"section": "Integrations", "target": "integrations", "title": "Integrations", "keywords": "OSC VRChat Spotify music weather heart rate soundpad VRCX body tracking router live instance joins leaves players world"},
    {"section": "Appearance", "target": "appearance", "title": "Appearance", "keywords": "theme slim frame text effect compact streamer colors"},
    {"section": "Profiles", "target": "profiles", "title": "Profiles", "keywords": "profile streaming gaming work away switch save load"},
    {"section": "Logs", "target": "logs", "title": "Logs and diagnostics", "keywords": "logs errors warnings export diagnostics report troubleshooting"},
    {"section": "Settings", "target": "settings", "title": "Settings", "keywords": "OSC address port startup backup restore reset import export"},
    {"section": "Help", "target": "help", "title": "Setup help", "keywords": "wizard first launch vrchat osc setup connection test"},
]


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def make_id(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _trim_log_file_if_needed():
    try:
        if os.path.getsize(APP_LOG_FILE) <= _MAX_LOG_BYTES:
            return
        with open(APP_LOG_FILE, "rb") as f:
            f.seek(-_TRIM_TO_BYTES, os.SEEK_END)
            data = f.read()
        newline_index = data.find(b"\n")
        if newline_index != -1:
            data = data[newline_index + 1:]
        with open(APP_LOG_FILE, "wb") as f:
            f.write(data)
    except Exception:
        pass


def add_event(severity="info", component="app", message="", details=None):
    os.makedirs(DATA_DIR, exist_ok=True)
    entry = {
        "id": make_id("log"),
        "timestamp": now_iso(),
        "severity": str(severity or "info").lower(),
        "component": str(component or "app"),
        "message": str(message or ""),
        "details": redact_sensitive(details or {}),
    }
    with open(APP_LOG_FILE, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
    _trim_log_file_if_needed()
    return entry


def read_events(limit=300, severities=None, components=None):
    if not os.path.exists(APP_LOG_FILE):
        return []
    severities = {s.lower() for s in severities or [] if s}
    components = {s.lower() for s in components or [] if s}
    limit = max(1, int(limit or 300))

    tail_bytes = max(16384, limit * 512)
    try:
        with open(APP_LOG_FILE, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            truncated = size > tail_bytes
            f.seek(max(0, size - tail_bytes))
            raw = f.read().decode("utf-8", errors="replace")
    except Exception:
        raw, truncated = "", False

    lines = raw.splitlines()
    if truncated and lines:
        lines = lines[1:]

    entries = []
    for line in lines:
        try:
            entry = json.loads(line)
        except Exception:
            continue
        if severities and str(entry.get("severity", "")).lower() not in severities:
            continue
        if components and str(entry.get("component", "")).lower() not in components:
            continue
        entries.append(entry)
    return entries[-limit:]


def clear_events():
    if os.path.exists(APP_LOG_FILE):
        open(APP_LOG_FILE, "w", encoding="utf-8").close()
    add_event("info", "logs", "Logs cleared")


def validate_osc(ip, port):
    errors = {}
    ip_text = str(ip or "").strip()
    if not ip_text:
        errors["quest_ip"] = "Use 127.0.0.1 for desktop VRChat, or your headset IP for Quest."
    try:
        port_int = int(port)
        if port_int < 1 or port_int > 65535:
            raise ValueError
    except Exception:
        errors["quest_port"] = "Port must be a number between 1 and 65535."
        port_int = 9000
    return ip_text, port_int, errors


def normalize_preset(raw):
    raw = raw or {}
    preset_id = str(raw.get("id") or make_id("preset"))
    name = str(raw.get("name") or "Untitled preset").strip()[:80] or "Untitled preset"
    template = str(raw.get("message_template") or raw.get("template") or "").strip()
    if not template:
        template = "Hello, come chat!"
    try:
        refresh = int(raw.get("refresh_interval", SETTINGS.get("osc_send_interval", 3)))
    except Exception:
        refresh = 3
    try:
        duration = int(raw.get("display_duration", SETTINGS.get("typed_message_duration", 5)))
    except Exception:
        duration = 5
    return {
        "id": preset_id,
        "name": name,
        "description": str(raw.get("description") or "")[:240],
        "message_template": template[:1000],
        "refresh_interval": max(1, min(refresh, 3600)),
        "display_duration": max(1, min(duration, 60)),
        "formatting": raw.get("formatting") if isinstance(raw.get("formatting"), dict) else {
            "text_effect": SETTINGS.get("text_effect", "none"),
            "frame": SETTINGS.get("chatbox_frame", "none"),
            "slim": SETTINGS.get("slim_chatbox", False),
        },
        "enabled_variables": raw.get("enabled_variables") if isinstance(raw.get("enabled_variables"), dict) else {},
        "integrations": raw.get("integrations") if isinstance(raw.get("integrations"), list) else [],
        "automation_rules": raw.get("automation_rules") if isinstance(raw.get("automation_rules"), list) else [],
        "appearance": raw.get("appearance") if isinstance(raw.get("appearance"), dict) else {},
        "priority": max(0, min(int(raw.get("priority", 50) or 50), 100)),
        "profile": str(raw.get("profile") or SETTINGS.get("active_profile", "Default"))[:80],
        "created_at": raw.get("created_at") or now_iso(),
        "updated_at": now_iso(),
    }


def list_presets():
    return [normalize_preset(p) for p in SETTINGS.get("presets", [])]


def save_presets(presets):
    next_settings = deepcopy(SETTINGS)
    next_settings["presets"] = [normalize_preset(p) for p in presets]
    if next_settings["presets"] and next_settings.get("active_preset_id") not in {p["id"] for p in next_settings["presets"]}:
        next_settings["active_preset_id"] = next_settings["presets"][0]["id"]
    save_settings(next_settings, backup=True, label="presets")


def upsert_preset(raw):
    preset = normalize_preset(raw)
    presets = list_presets()
    for index, existing in enumerate(presets):
        if existing["id"] == preset["id"]:
            preset["created_at"] = existing.get("created_at", preset["created_at"])
            presets[index] = preset
            save_presets(presets)
            add_event("info", "presets", f"Preset saved: {preset['name']}")
            return preset
    presets.append(preset)
    save_presets(presets)
    add_event("info", "presets", f"Preset created: {preset['name']}")
    return preset


def delete_preset(preset_id):
    presets = list_presets()
    kept = [p for p in presets if p["id"] != preset_id]
    if len(kept) == len(presets):
        return False
    save_presets(kept)
    add_event("warning", "presets", "Preset deleted", {"preset_id": preset_id})
    return True


def duplicate_preset(preset_id):
    for preset in list_presets():
        if preset["id"] == preset_id:
            clone = deepcopy(preset)
            clone["id"] = make_id("preset")
            clone["name"] = f"{clone['name']} copy"[:80]
            clone["created_at"] = now_iso()
            clone["updated_at"] = now_iso()
            return upsert_preset(clone)
    return None


def apply_preset(preset_id):
    presets = list_presets()
    preset = next((p for p in presets if p["id"] == preset_id), None)
    if not preset:
        return None
    formatting = preset.get("formatting", {})
    next_settings = deepcopy(SETTINGS)
    next_settings["active_preset_id"] = preset["id"]
    next_settings["custom_texts"] = [preset["message_template"]]
    next_settings["osc_send_interval"] = preset["refresh_interval"]
    next_settings["typed_message_duration"] = preset["display_duration"]
    next_settings["text_effect"] = formatting.get("text_effect", next_settings.get("text_effect", "none"))
    next_settings["chatbox_frame"] = formatting.get("frame", next_settings.get("chatbox_frame", "none"))
    next_settings["slim_chatbox"] = bool(formatting.get("slim", next_settings.get("slim_chatbox", False)))
    enabled = preset.get("enabled_variables") or {}
    mapping = {
        "time": "show_time",
        "custom": "show_custom",
        "music": "show_music",
        "window": "show_window",
        "heartrate": "show_heartrate",
        "weather": "show_weather",
        "system": "system_stats_enabled",
        "afk": "afk_enabled",
    }
    for key, setting_key in mapping.items():
        if key in enabled:
            next_settings[setting_key] = bool(enabled[key])
    save_settings(next_settings, backup=True, label="apply_preset")
    add_event("info", "presets", f"Preset applied: {preset['name']}")
    return preset


def capture_profile_settings():
    return {key: deepcopy(SETTINGS.get(key, DEFAULTS.get(key))) for key in PROFILE_SETTING_KEYS if key in SETTINGS or key in DEFAULTS}


def list_profiles_full():
    profiles = profiles_manager.load_profiles()
    if not profiles:
        profiles = [{
            "name": "Default",
            "description": "Default Crystal Client profile.",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "settings": capture_profile_settings(),
        }]
        profiles_manager.save_profiles(profiles)
    return profiles


def save_profile(name, description=""):
    name = str(name or "").strip()[:80]
    if not name:
        raise ValueError("Profile name is required.")
    profile = {
        "name": name,
        "description": str(description or "")[:240],
        "updated_at": now_iso(),
        "settings": capture_profile_settings(),
    }
    existing = profiles_manager.get_profile(name)
    if existing:
        created = existing.get("created_at") or now_iso()
        profile["created_at"] = created
        profiles_manager.update_profile(name, profile["settings"], description=profile["description"], metadata=profile)
    else:
        profile["created_at"] = now_iso()
        profiles_manager.create_profile(name, profile["settings"], description=profile["description"], metadata=profile)
    next_settings = deepcopy(SETTINGS)
    next_settings["active_profile"] = name
    save_settings(next_settings, backup=True, label="profile_save")
    add_event("info", "profiles", f"Profile saved: {name}")
    return profile


def apply_profile(name):
    profile = profiles_manager.get_profile(str(name or ""))
    if not profile:
        return None
    next_settings = deepcopy(SETTINGS)
    next_settings.update(profile.get("settings", {}))
    next_settings["active_profile"] = profile.get("name", name)
    save_settings(next_settings, backup=True, label="profile_apply")
    add_event("info", "profiles", f"Profile loaded: {profile.get('name', name)}")
    return profile


def delete_profile(name):
    ok = profiles_manager.delete_profile(str(name or ""))
    if ok:
        if SETTINGS.get("active_profile") == str(name or ""):
            next_settings = deepcopy(SETTINGS)
            next_settings["active_profile"] = "Default"
            save_settings(next_settings, backup=True, label="profile_delete")
        add_event("warning", "profiles", f"Profile deleted: {name}")
    return ok


def normalize_automation(raw):
    raw = raw or {}
    try:
        interval = int(raw.get("interval_seconds", 60))
    except Exception:
        interval = 60
    try:
        priority = int(raw.get("priority", 50))
    except Exception:
        priority = 50
    return {
        "id": str(raw.get("id") or make_id("automation")),
        "name": str(raw.get("name") or "Untitled automation").strip()[:80] or "Untitled automation",
        "description": str(raw.get("description") or "")[:240],
        "enabled": bool(raw.get("enabled", True)),
        "trigger": str(raw.get("trigger") or "timed")[:40],
        "message_template": str(raw.get("message_template") or "")[:1000],
        "interval_seconds": max(5, min(interval, 86400)),
        "priority": max(0, min(priority, 100)),
        "profile": str(raw.get("profile") or SETTINGS.get("active_profile", "Default"))[:80],
        "last_run": float(raw.get("last_run", 0) or 0),
        "created_at": raw.get("created_at") or now_iso(),
        "updated_at": now_iso(),
    }


def list_automations():
    return [normalize_automation(rule) for rule in SETTINGS.get("automation_rules", [])]


def save_automations(rules):
    next_settings = deepcopy(SETTINGS)
    next_settings["automation_rules"] = [normalize_automation(rule) for rule in rules]
    save_settings(next_settings, backup=True, label="automations")


def upsert_automation(raw):
    rule = normalize_automation(raw)
    rules = list_automations()
    for index, existing in enumerate(rules):
        if existing["id"] == rule["id"]:
            rule["created_at"] = existing.get("created_at", rule["created_at"])
            rules[index] = rule
            save_automations(rules)
            add_event("info", "automations", f"Automation saved: {rule['name']}")
            return rule
    rules.append(rule)
    save_automations(rules)
    add_event("info", "automations", f"Automation created: {rule['name']}")
    return rule


def delete_automation(rule_id):
    rules = list_automations()
    kept = [rule for rule in rules if rule["id"] != rule_id]
    if len(kept) == len(rules):
        return False
    save_automations(kept)
    add_event("warning", "automations", "Automation deleted", {"automation_id": rule_id})
    return True


def automation_summary():
    rules = list_automations()
    enabled = [rule for rule in rules if rule.get("enabled")]
    return {
        "total": len(rules),
        "enabled": len(enabled),
        "top_priority": max([rule.get("priority", 0) for rule in enabled], default=0),
        "rules": sorted(rules, key=lambda rule: (-rule.get("priority", 0), rule.get("name", ""))),
    }


def search(query):
    q = str(query or "").strip().lower()
    if not q:
        return SEARCH_INDEX
    results = []
    for item in SEARCH_INDEX:
        haystack = " ".join(str(item.get(k, "")) for k in ("section", "title", "keywords")).lower()
        if q in haystack:
            results.append(item)
    return results


def export_bundle(redacted=True):
    os.makedirs(EXPORT_DIR, exist_ok=True)
    payload = {
        "app": "Crystal Client",
        "exported_at": now_iso(),
        "schema_version": 1,
        "settings": public_settings() if redacted else deepcopy(SETTINGS),
        "presets": list_presets(),
        "profiles": list_profiles_full(),
        "automations": list_automations(),
        "templates": deepcopy(SETTINGS.get("saved_templates", [])),
    }
    path = os.path.join(EXPORT_DIR, f"crystal_chatbox_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    add_event("info", "backup", "Export created", {"path": path, "redacted": redacted})
    return path


def import_bundle(payload):
    if not isinstance(payload, dict):
        raise ValueError("Import file must contain a JSON object.")
    create_backup("before_import")
    next_settings = deepcopy(SETTINGS)
    if isinstance(payload.get("settings"), dict):
        imported_settings = payload["settings"]
        for key, value in imported_settings.items():
            if value == "••••••••":
                continue
            next_settings[key] = value
    if isinstance(payload.get("presets"), list):
        next_settings["presets"] = [normalize_preset(p) for p in payload["presets"]]
    if isinstance(payload.get("automations"), list):
        next_settings["automation_rules"] = [normalize_automation(rule) for rule in payload["automations"]]
    if isinstance(payload.get("templates"), list):
        next_settings["saved_templates"] = payload["templates"]
    save_settings(next_settings, backup=True, label="import")
    if isinstance(payload.get("profiles"), list):
        profiles_manager.save_profiles(payload["profiles"])
    add_event("info", "backup", "Import completed")
    return True


def create_diagnostics_report(extra=None):
    os.makedirs(EXPORT_DIR, exist_ok=True)
    report = {
        "app": "Crystal Client",
        "created_at": now_iso(),
        "settings": public_settings(),
        "presets": list_presets(),
        "profiles": list_profiles_full(),
        "automations": list_automations(),
        "recent_logs": read_events(200),
        "message_stats": message_history.get_message_stats(),
        "files": {
            "settings": os.path.exists(SETTINGS_FILE),
            "backups_dir": os.path.exists(BACKUP_DIR),
            "app_logs": os.path.exists(APP_LOG_FILE),
        },
        "extra": redact_sensitive(extra or {}),
    }
    json_path = os.path.join(EXPORT_DIR, f"crystal_diagnostics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(json_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    zip_path = json_path.replace(".json", ".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(json_path, os.path.basename(json_path))
        if os.path.exists(APP_LOG_FILE):
            zf.write(APP_LOG_FILE, "app_events.jsonl")
    add_event("info", "diagnostics", "Diagnostics report created", {"path": zip_path})
    return zip_path
