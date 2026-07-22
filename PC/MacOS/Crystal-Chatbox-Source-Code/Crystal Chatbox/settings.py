import json
import os
import shutil
import sys
import tempfile
import threading
from copy import deepcopy
from datetime import datetime


if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
    DATA_DIR = os.path.join(BASE_DIR, "Crystal Chatbox Data")
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = BASE_DIR

os.makedirs(DATA_DIR, exist_ok=True)

SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
settings_lock = threading.Lock()
SCHEMA_VERSION = 2

SENSITIVE_KEYS = {
    "spotify_client_secret",
    "spotify_refresh_token",
    "heart_rate_pulsoid_token",
    "heart_rate_hyperate_id",
    "heart_rate_custom_api",
}

DEFAULT_AVATAR_PROVIDER_URLS = [
    "https://vrcx.avtr.zip/",
    "https://vrcx.vrcdb.com/avatars/Avatar/VRCX",
    "https://paw-api.amelia.fun/vrcx_search",
    "https://avatar.worldbalancer.com/vrcx_search.php",
    "https://avtr.nekosunevr.co.uk/vrcx_search",
    "https://api.avtrdb.com/v3/avatar/search/vrcx",
]

MOJIBAKE_FIXES = {
    "â°": "⏰",
    "ðŸ’¬": "💬",
    "ðŸŽ¶": "🎶",
    "ðŸ’»": "💻",
    "â¤ï¸": "❤️",
    "ðŸŒ¤ï¸": "🌤️",
    "ðŸ“Š": "📊",
    "ðŸ§ ": "🧠",
    "ðŸ’¾": "💾",
    "ðŸŽ®": "🎮",
    "ðŸ“¡": "📡",
    "ðŸ’¤": "💤",
}

DEFAULTS = {
    "schema_version": SCHEMA_VERSION,
    "setup_completed": False,
    "active_profile": "Default",
    "active_preset_id": "default-status",
    "quest_ip": "",
    "quest_port": 9000,
    "spotify_client_id": "",
    "spotify_client_secret": "",
    "spotify_refresh_token": "",
    "spotify_update_interval": 2,
    "now_playing_method": "spotify_api",
    "lastfm_username": "",
    "custom_texts": ["Crystal Client out now in meta store!", "Join the Discord to support us!"],
    "refresh_interval": 3,
    "osc_send_interval": 3,
    "dashboard_update_interval": 3,
    "per_message_intervals": {},
    "music_progress": True,
    "progress_style": "bar",
    "timezone": "local",
    "layout_order": ["time", "custom", "vrchat_live", "song", "window", "heartrate", "weather", "system_stats", "afk"],
    "layout_spacers": {},
    "theme": "dark",
    "random_order": False,
    "weighted_messages": {},
    "show_module_icons": True,
    "streamer_mode": False,
    "compact_mode": False,
    "error_log_enabled": True,
    "message_queue_preview_count": 3,
    "chatbox_visible": False,
    "show_time": True,
    "show_custom": True,
    "show_music": True,
    "show_window": False,
    "show_heartrate": False,
    "window_tracking_enabled": False,
    "window_tracking_interval": 2,
    "window_tracking_mode": "both",
    "window_prefix": "",
    "window_title_max_length": 50,
    "window_name_aliases": {},
    "weather_temp_unit": "F",
    "heart_rate_enabled": False,
    "heart_rate_source": "pulsoid",
    "heart_rate_pulsoid_token": "",
    "heart_rate_hyperate_id": "",
    "heart_rate_custom_api": "",
    "heart_rate_update_interval": 5,
    "heart_rate_osc_enabled": False,
    "heart_rate_osc_min_bpm": 40,
    "heart_rate_osc_max_bpm": 200,
    "hr_show_trend": True,
    "hr_show_stats": False,
    "time_emoji": "⏰",
    "custom_emoji": "💬",
    "song_emoji": "🎶",
    "window_emoji": "💻",
    "heartrate_emoji": "❤️",
    "custom_background": "",
    "custom_button_color": "",
    "weather_enabled": False,
    "weather_location": "auto",
    "weather_update_interval": 600,
    "show_weather": False,
    "weather_emoji": "🌤️",
    "vrchat_live_enabled": True,
    "vrchat_live_log_dir": "",
    "show_vrchat_live": False,
    "vrchat_live_manual_location": "",
    "vrchat_live_template": "{world} ({player_count}/{capacity}) | {instance} | {last_event}",
    "vr_battery_enabled": False,
    "vr_battery_interval": 20,
    "show_vr_battery": False,
    "vr_battery_include_controllers": True,
    "vr_battery_include_trackers": False,
    "vr_battery_low_threshold": 20,
    "volume_enabled": False,
    "volume_interval": 10,
    "show_volume": False,
    "volume_emoji": "🔊",
    "device_status_enabled": False,
    "device_status_interval": 60,
    "show_device_storage": False,
    "device_storage_emoji": "💾",
    "text_effect": "none",
    "slim_chatbox": False,
    "chatbox_frame": "none",
    "chatbox_frame_emoji": "✨",
    "chatbox_frame_style": "none",
    "chatbox_template_enabled": False,
    "chatbox_template_preset": "classic",
    "chatbox_template": "{time}\n{custom}\n{song}\n{progress}\n{window}\n{heartrate}\n{weather}\n{system}\n{afk}",
    "chatbox_separator": "\n",
    "chatbox_blank_line_mode": "hide",
    "chatbox_overflow_mode": "page",
    "chatbox_page_indicator": True,
    "typed_message_duration": 5,
    "typing_indicator_enabled": True,
    "system_stats_enabled": False,
    "system_stats_show_cpu": True,
    "system_stats_show_ram": True,
    "system_stats_show_gpu": False,
    "system_stats_show_network": False,
    "system_stats_show_battery": False,
    "system_stats_update_interval": 5,
    "system_stats_separator": " | ",
    "system_stats_show_labels": True,
    "system_stats_decimals": 0,
    "system_stats_show_ram_details": False,
    "system_stats_network_units": "bits",
    "system_stats_template": "{system_emoji} {cpu_emoji} CPU {cpu} | {ram_emoji} RAM {ram}",
    "system_stats_emoji": "📊",
    "system_stats_cpu_emoji": "🧠",
    "system_stats_ram_emoji": "💾",
    "system_stats_gpu_emoji": "🎮",
    "system_stats_network_emoji": "📡",
    "system_stats_battery_emoji": "🔋",
    "soundpad_enabled": True,
    "soundpad_volume": 85,
    "soundpad_announce": False,
    "soundpad_caption_template": "Playing: {name}",
    "tts_enabled": True,
    "tts_rate": 1.0,
    "tts_pitch": 1.0,
    "tts_volume": 1.0,
    "afk_enabled": False,
    "afk_timeout": 300,
    "afk_message": "AFK",
    "afk_show_duration": True,
    "afk_emoji": "💤",
    "osc_router_listen_ip": "127.0.0.1",
    "osc_router_listen_port": 9010,
    "fbt_tracker_source": "camera",
    "fbt_tracker_output": "osc",
    "fbt_quest_bridge_listen_ip": "0.0.0.0",
    "fbt_quest_bridge_listen_port": 7777,
    "fbt_quest_bridge_timeout_ms": 250.0,
    "fbt_steamvr_bridge_dir": r"C:\Users\Public\CrystalClient\steamvr_bridge",
    "fbt_mode": "vrchat_trackers",
    "fbt_camera_source": "local",
    "fbt_phone_camera_url": "",
    "fbt_camera": 0,
    "fbt_smoothing": 0.40,
    "fbt_position_scale": 1.0,
    "fbt_height_m": 1.65,
    "fbt_floor_offset_m": 0.0,
    "fbt_hips_offset_m": 0.0,
    "fbt_feet_y_offset_m": -0.02,
    "fbt_lower_body_y_offset_m": 0.0,
    "fbt_x_offset_m": 0.0,
    "fbt_z_offset_m": 0.0,
    "fbt_send_rate": 60.0,
    "fbt_preview_fps": 30.0,
    "fbt_foot_yaw_blend": 0.78,
    "fbt_foot_yaw_offset_deg": 0.0,
    "fbt_mirror": False,
    "fbt_show_overlay": True,
    "fbt_send_head_align": False,
    "fbt_send_chest_tracker": False,
    "fbt_send_knee_trackers": True,
    "fbt_send_elbow_trackers": False,
    "fbt_estimation_enabled": True,
    "fbt_estimation_strength": 0.82,
    "fbt_occlusion_confidence_threshold": 0.45,
    "fbt_occlusion_velocity_damping": 0.86,
    "fbt_secondary_enabled": False,
    "fbt_secondary_source": "phone",
    "fbt_secondary_phone_camera_url": "",
    "fbt_secondary_camera": 1,
    "fbt_secondary_blend": 0.35,
    "fbt_secondary_target": "lower_body",
    "fbt_secondary_rotation": "90cw",
    "fbt_secondary_mount_preset": "right",
    "fbt_secondary_yaw_deg": 0.0,
    "fbt_secondary_pitch_deg": 0.0,
    "vrcx_plus_avatar_provider_enabled": True,
    "vrcx_plus_avatar_provider_url": DEFAULT_AVATAR_PROVIDER_URLS[0],
    "vrcx_plus_avatar_provider_urls": list(DEFAULT_AVATAR_PROVIDER_URLS),
    "favorite_messages": [],
    "saved_templates": [
        {
            "id": "friendly-greeting",
            "name": "Friendly greeting",
            "description": "Short message for starting conversations.",
            "template": "Hello, come chat!",
            "tags": ["chatbox", "social"],
        },
        {
            "id": "music-status",
            "name": "Music status",
            "description": "Shows current song when Spotify is enabled.",
            "template": "{song}",
            "tags": ["music", "spotify"],
        },
    ],
    "presets": [],
    "automation_rules": [],
    "world_preset_rules": [],
    "world_preset_auto_switch_enabled": False,
    "reaction_rules": [],
    "reaction_display_seconds": 6,
    "avatar_change_announce_enabled": False,
    "avatar_change_message": "Just switched avatars! ✨",
    "mute_indicator_enabled": False,
    "mute_indicator_text": "🔇 Muted",
    "global_hotkeys_enabled": False,
    "global_hotkeys": [],
    "osc_reactions_port": 9001,
    "app_window": {"width": 1200, "height": 820, "x": None, "y": None},
    "notifications_enabled": True,
    "diagnostics_opt_in": False,
}


def _now_stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _atomic_write_json(path, data):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_settings_", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def create_backup(label="manual"):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    if not os.path.exists(SETTINGS_FILE):
        return None
    safe_label = "".join(ch for ch in str(label or "manual") if ch.isalnum() or ch in ("-", "_"))[:40] or "manual"
    dest = os.path.join(BACKUP_DIR, f"settings_{safe_label}_{_now_stamp()}.json")
    shutil.copy2(SETTINGS_FILE, dest)
    return dest


def _redact_value(key, value):
    if key in SENSITIVE_KEYS and value:
        return "••••••••"
    return value


def redact_sensitive(data):
    if isinstance(data, dict):
        return {k: redact_sensitive(_redact_value(k, v)) for k, v in data.items()}
    if isinstance(data, list):
        return [redact_sensitive(v) for v in data]
    return data


def _coerce_int(value, default, low, high):
    try:
        out = int(value)
    except Exception:
        out = int(default)
    return max(low, min(high, out))


def _coerce_float(value, default, low, high):
    try:
        out = float(value)
    except Exception:
        out = float(default)
    return max(low, min(high, out))


def _fix_mojibake(value):
    return MOJIBAKE_FIXES.get(value, value)


def _default_presets_from_settings(data):
    custom_texts = data.get("custom_texts") or DEFAULTS["custom_texts"]
    template = custom_texts[0] if custom_texts else "Hello, come chat!"
    now = datetime.now().isoformat(timespec="seconds")
    return [
        {
            "id": "default-status",
            "name": "Default status",
            "description": "Your normal rotating chatbox setup.",
            "message_template": template,
            "refresh_interval": data.get("osc_send_interval", 3),
            "display_duration": data.get("typed_message_duration", 5),
            "formatting": {
                "text_effect": data.get("text_effect", "none"),
                "frame": data.get("chatbox_frame", "none"),
                "slim": data.get("slim_chatbox", False),
            },
            "enabled_variables": {
                "time": data.get("show_time", True),
                "custom": data.get("show_custom", True),
                "music": data.get("show_music", True),
                "vrchat_live": data.get("show_vrchat_live", False),
                "window": data.get("show_window", False),
                "heartrate": data.get("show_heartrate", False),
                "weather": data.get("show_weather", False),
                "system": data.get("system_stats_enabled", False),
                "afk": data.get("afk_enabled", False),
            },
            "integrations": [],
            "automation_rules": [],
            "appearance": {},
            "priority": 50,
            "profile": data.get("active_profile", "Default"),
            "created_at": now,
            "updated_at": now,
        }
    ]


def _default_automations_from_settings(data):
    if not data.get("afk_enabled", False):
        return []
    return [
        {
            "id": "afk-message",
            "name": "Away message",
            "description": "Shows an away message when you are idle.",
            "enabled": True,
            "trigger": "idle",
            "message_template": "{afk}",
            "interval_seconds": max(10, int(data.get("osc_send_interval", 3))),
            "priority": 80,
            "last_run": 0,
        }
    ]


def migrate_settings(data):
    if not isinstance(data, dict):
        data = {}
    migrated = deepcopy(DEFAULTS)
    migrated.update(data)
    for key in (
        "time_emoji",
        "custom_emoji",
        "song_emoji",
        "window_emoji",
        "heartrate_emoji",
        "weather_emoji",
        "system_stats_emoji",
        "system_stats_cpu_emoji",
        "system_stats_ram_emoji",
        "system_stats_gpu_emoji",
        "system_stats_network_emoji",
        "system_stats_battery_emoji",
        "afk_emoji",
        "volume_emoji",
        "device_storage_emoji",
    ):
        migrated[key] = _fix_mojibake(migrated.get(key, DEFAULTS[key]))

    migrated["schema_version"] = SCHEMA_VERSION
    migrated["quest_ip"] = str(migrated.get("quest_ip", ""))[:255]
    migrated["quest_port"] = _coerce_int(migrated.get("quest_port"), 9000, 1, 65535)
    migrated["osc_send_interval"] = _coerce_int(migrated.get("osc_send_interval"), 3, 1, 3600)
    migrated["dashboard_update_interval"] = _coerce_int(migrated.get("dashboard_update_interval"), 3, 1, 60)
    migrated["typed_message_duration"] = _coerce_int(migrated.get("typed_message_duration"), 5, 1, 60)
    migrated["spotify_update_interval"] = _coerce_int(migrated.get("spotify_update_interval"), 2, 1, 60)
    migrated["spotify_client_id"] = str(migrated.get("spotify_client_id", ""))[:255]
    migrated["spotify_client_secret"] = str(migrated.get("spotify_client_secret", ""))[:255]
    migrated["lastfm_username"] = str(migrated.get("lastfm_username", ""))[:255]
    migrated["now_playing_method"] = migrated.get("now_playing_method") if migrated.get("now_playing_method") in ("lastfm", "spotify_api") else "spotify_api"
    migrated["weather_update_interval"] = _coerce_int(migrated.get("weather_update_interval"), 600, 60, 86400)
    migrated["vrchat_live_log_dir"] = str(migrated.get("vrchat_live_log_dir", ""))[:500]
    migrated["vrchat_live_manual_location"] = str(migrated.get("vrchat_live_manual_location", ""))[:500]
    migrated["vrchat_live_template"] = str(migrated.get("vrchat_live_template", DEFAULTS["vrchat_live_template"]))[:500]
    migrated["heart_rate_update_interval"] = _coerce_int(migrated.get("heart_rate_update_interval"), 5, 1, 120)
    migrated["system_stats_update_interval"] = _coerce_int(migrated.get("system_stats_update_interval"), 5, 2, 60)
    migrated["volume_interval"] = _coerce_int(migrated.get("volume_interval"), 10, 2, 60)
    migrated["device_status_interval"] = _coerce_int(migrated.get("device_status_interval"), 60, 15, 600)
    migrated["soundpad_volume"] = _coerce_int(migrated.get("soundpad_volume"), 85, 0, 100)
    migrated["tts_rate"] = _coerce_float(migrated.get("tts_rate"), 1.0, 0.5, 2.0)
    migrated["tts_pitch"] = _coerce_float(migrated.get("tts_pitch"), 1.0, 0.5, 2.0)
    migrated["tts_volume"] = _coerce_float(migrated.get("tts_volume"), 1.0, 0.0, 1.0)
    if migrated.get("chatbox_overflow_mode") not in {"smart", "hard", "off", "page"}:
        migrated["chatbox_overflow_mode"] = "smart"
    if migrated.get("theme") not in {"dark", "light"}:
        migrated["theme"] = "dark"
    migrated["custom_texts"] = [str(v)[:500] for v in (migrated.get("custom_texts") or DEFAULTS["custom_texts"])]
    if not isinstance(migrated.get("presets"), list) or not migrated["presets"]:
        migrated["presets"] = _default_presets_from_settings(migrated)
    if not isinstance(migrated.get("automation_rules"), list):
        migrated["automation_rules"] = _default_automations_from_settings(migrated)
    if not isinstance(migrated.get("favorite_messages"), list):
        migrated["favorite_messages"] = []
    if not isinstance(migrated.get("saved_templates"), list):
        migrated["saved_templates"] = deepcopy(DEFAULTS["saved_templates"])
    if not isinstance(migrated.get("app_window"), dict):
        migrated["app_window"] = deepcopy(DEFAULTS["app_window"])
    if not isinstance(migrated.get("window_name_aliases"), dict):
        migrated["window_name_aliases"] = {}
    else:
        migrated["window_name_aliases"] = {
            str(k)[:80]: str(v)[:40] for k, v in migrated["window_name_aliases"].items() if str(v).strip()
        }
    if not isinstance(migrated.get("world_preset_rules"), list):
        migrated["world_preset_rules"] = []
    if not isinstance(migrated.get("layout_spacers"), dict):
        migrated["layout_spacers"] = {}
    else:
        migrated["layout_spacers"] = {
            str(k): str(v)[:20] for k, v in migrated["layout_spacers"].items() if str(k).startswith("spacer_")
        }
    if not isinstance(migrated.get("reaction_rules"), list):
        migrated["reaction_rules"] = []
    if not isinstance(migrated.get("global_hotkeys"), list):
        migrated["global_hotkeys"] = []
    provider_urls = migrated.get("vrcx_plus_avatar_provider_urls", [])
    if isinstance(provider_urls, str):
        provider_urls = [provider_urls]
    elif not isinstance(provider_urls, list):
        provider_urls = []
    fallback_provider = str(migrated.get("vrcx_plus_avatar_provider_url", "") or "").strip()
    merged_provider_urls = []
    for provider_url in list(DEFAULT_AVATAR_PROVIDER_URLS) + provider_urls + [fallback_provider]:
        provider_url = str(provider_url or "").strip()
        if provider_url and provider_url not in merged_provider_urls:
            merged_provider_urls.append(provider_url)
    migrated["vrcx_plus_avatar_provider_urls"] = merged_provider_urls
    migrated["vrcx_plus_avatar_provider_url"] = merged_provider_urls[0]
    return migrated


def _load_settings_file():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            corrupt_path = os.path.join(BACKUP_DIR, f"settings_corrupt_{_now_stamp()}.json")
            try:
                shutil.copy2(SETTINGS_FILE, corrupt_path)
            except Exception:
                pass
    return deepcopy(DEFAULTS)


SETTINGS = migrate_settings(_load_settings_file())


def save_settings(settings=None, backup=False, label="save"):
    with settings_lock:
        target = SETTINGS if settings is None else settings
        migrated = migrate_settings(target)
        if backup:
            try:
                create_backup(label)
            except Exception:
                pass
        _atomic_write_json(SETTINGS_FILE, migrated)
        SETTINGS.clear()
        SETTINGS.update(migrated)
    return True


def update_settings(updates, backup=False, label="update"):
    if not isinstance(updates, dict):
        raise ValueError("Settings update must be an object.")
    with settings_lock:
        next_settings = deepcopy(SETTINGS)
        next_settings.update(updates)
    return save_settings(next_settings, backup=backup, label=label)


def public_settings():
    with settings_lock:
        return redact_sensitive(deepcopy(SETTINGS))


def reload_settings():
    global SETTINGS
    with settings_lock:
        try:
            loaded_settings = migrate_settings(_load_settings_file())
            SETTINGS.clear()
            SETTINGS.update(loaded_settings)
            print("[Settings] Settings reloaded from file")
            return True
        except Exception as e:
            print(f"[Settings] Failed to reload settings: {e}")
            return False


save_settings(SETTINGS)
