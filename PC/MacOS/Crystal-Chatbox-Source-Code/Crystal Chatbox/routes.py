import threading
import time
import json
import os
import sys
import random
import logging
import logging.handlers
import signal
import socket
import subprocess
import shutil
import urllib.request
import base64
import secrets
import requests
from collections import deque
from copy import deepcopy
from datetime import datetime
from urllib.parse import parse_qs, unquote, urlencode, urlparse
import pytz

from flask import Flask, render_template, request, jsonify, redirect, send_file
from pythonosc.udp_client import SimpleUDPClient

from settings import DEFAULTS, DEFAULT_AVATAR_PROVIDER_URLS, SETTINGS, SETTINGS_FILE, create_backup, public_settings, save_settings, update_settings
import app_services
import spotify
import window_tracker
import heart_rate_monitor
import github_updater
import openai_client
import weather_service
import profiles_manager
import text_effects
import chatbox_frames
import system_stats
import afk_detector
import quick_phrases
import message_history
import vrchat_service
import vrchat_live
import vr_battery
import steamvr_launch
import volume_monitor
import device_status
import soundpad
import session_insights
import osc_reactions
import global_hotkeys


IS_ANDROID = "ANDROID_ARGUMENT" in os.environ

SPOTIFY_AUTH_URL = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_SCOPES = "user-read-currently-playing user-read-playback-state"
_spotify_oauth_state = {"value": ""}


if getattr(sys, 'frozen', False):

    BASE_DIR = os.path.dirname(sys.executable)
    DATA_DIR = os.path.join(BASE_DIR, "Crystal Chatbox Data")
elif "ANDROID_ARGUMENT" in os.environ:

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = os.environ.get("ANDROID_PRIVATE", BASE_DIR)
else:

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = BASE_DIR


os.makedirs(DATA_DIR, exist_ok=True)

ERROR_LOG_FILE = os.path.join(DATA_DIR, "vrchat_errors.log")

_error_log_handler = logging.handlers.RotatingFileHandler(
    ERROR_LOG_FILE, maxBytes=1 * 1024 * 1024, backupCount=1, encoding="utf-8"
)
_error_log_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logging.getLogger().addHandler(_error_log_handler)
logging.getLogger().setLevel(logging.ERROR)

chatbox_visible = SETTINGS.get("chatbox_visible", False)
show_time = SETTINGS.get("show_time", True)
show_custom = SETTINGS.get("show_custom", True)
show_music = SETTINGS.get("show_music", True)
show_window = SETTINGS.get("show_window", False)
show_heartrate = SETTINGS.get("show_heartrate", False)
show_weather = SETTINGS.get("show_weather", False)

settings_changed = False
if SETTINGS.get("window_tracking_enabled", False):
    if not show_window:
        show_window = True
        SETTINGS["show_window"] = True
        settings_changed = True
if SETTINGS.get("heart_rate_enabled", False):
    if not show_heartrate:
        show_heartrate = True
        SETTINGS["show_heartrate"] = True
        settings_changed = True

print(f"[Startup] show_weather={show_weather}, weather_enabled={SETTINGS.get('weather_enabled', False)}, LAYOUT_ORDER will be: {SETTINGS.get('layout_order', [])}")

if settings_changed:
    try:
        with open(SETTINGS_FILE, "wb") as f:
            f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
    except Exception as e:
        print(f"[Startup] Failed to sync settings: {e}")

auto_send_paused = False
connection_status = "disconnected"
last_successful_send = None
last_osc_send_time = 0
last_hr_osc_send_time = 0
last_hr_osc_bpm = 0
last_hr_osc_connected = False
last_hr_osc_beat_time = 0
last_hr_osc_offline_send = 0

typing_state_lock = threading.Lock()
typing_state = {
    "is_typing": False,
    "typed_message": "",
    "display_until": 0,
    "show_indicator": False,
    "message_sent": False
}

current_time_text = ""
current_custom_text = SETTINGS.get("custom_texts", ["Custom Message Test"])[0]
last_message_sent = ""
text_cycle_index = 0
next_custom_in = SETTINGS.get("osc_send_interval", 3)
per_message_timers = {}
message_queue = []
automation_runtime = {}

CUSTOM_TEXTS = SETTINGS.get("custom_texts", [])
OSC_SEND_INTERVAL = SETTINGS.get("osc_send_interval", 3)
DASHBOARD_UPDATE_INTERVAL = SETTINGS.get("dashboard_update_interval", 1)
TIMEZONE = SETTINGS.get("timezone", "local")
MUSIC_PROGRESS = SETTINGS.get("music_progress", True)
PROGRESS_STYLE = SETTINGS.get("progress_style", "bar")
LAYOUT_ORDER = SETTINGS.get("layout_order", ["time","custom","vrchat_live","song","window","heartrate","weather","system_stats","afk"])

current_custom_text = CUSTOM_TEXTS[0] if CUSTOM_TEXTS else "Custom Message Test"
current_time_text = datetime.now().strftime("%I:%M %p").lstrip("0")

def log_error(message, exception=None):
    if SETTINGS.get("error_log_enabled", True):
        if exception:
            logging.error(f"{message}: {str(exception)}")
        else:
            logging.error(message)
    try:
        app_services.add_event(
            "error",
            "runtime",
            message,
            {"error": str(exception)} if exception else {},
        )
    except Exception:
        pass

def _safe_print(*args, **kwargs):
    try:
        print(*args, **kwargs)
    except Exception:
        try:
            safe_args = [str(a).encode("ascii", "replace").decode("ascii") for a in args]
            print(*safe_args, **kwargs)
        except Exception:
            pass


def make_client():
    ip = SETTINGS.get("quest_ip", "") or "127.0.0.1"
    port = int(SETTINGS.get("quest_port", 9000))
    return SimpleUDPClient(ip, port)

client = make_client()


def _persist_settings(backup=False, label="settings"):
    save_settings(SETTINGS, backup=backup, label=label)


def _sync_runtime_from_settings():
    global chatbox_visible, show_time, show_custom, show_music, show_window, show_heartrate, show_weather
    global CUSTOM_TEXTS, OSC_SEND_INTERVAL, DASHBOARD_UPDATE_INTERVAL, TIMEZONE, MUSIC_PROGRESS, PROGRESS_STYLE
    global LAYOUT_ORDER, current_custom_text, client

    chatbox_visible = SETTINGS.get("chatbox_visible", False)
    show_time = SETTINGS.get("show_time", True)
    show_custom = SETTINGS.get("show_custom", True)
    show_music = SETTINGS.get("show_music", True)
    show_window = SETTINGS.get("show_window", False)
    show_heartrate = SETTINGS.get("show_heartrate", False)
    show_weather = SETTINGS.get("show_weather", False)
    CUSTOM_TEXTS = SETTINGS.get("custom_texts", [])
    OSC_SEND_INTERVAL = SETTINGS.get("osc_send_interval", 3)
    DASHBOARD_UPDATE_INTERVAL = SETTINGS.get("dashboard_update_interval", 3)
    TIMEZONE = SETTINGS.get("timezone", "local")
    MUSIC_PROGRESS = SETTINGS.get("music_progress", True)
    PROGRESS_STYLE = SETTINGS.get("progress_style", "bar")
    LAYOUT_ORDER = SETTINGS.get("layout_order", ["time", "custom", "vrchat_live", "song", "window", "heartrate", "weather", "system_stats", "afk"])
    if CUSTOM_TEXTS and not current_custom_text:
        current_custom_text = CUSTOM_TEXTS[0]
    client = make_client()
    try:
        vrchat_live.configure(
            enabled=SETTINGS.get("vrchat_live_enabled", True),
            log_dir=SETTINGS.get("vrchat_live_log_dir", ""),
        )
    except Exception as exc:
        log_error("Failed to update VRChat live monitor settings", exc)
    try:
        global_hotkeys.configure(SETTINGS.get("global_hotkeys_enabled", False), SETTINGS.get("global_hotkeys", []))
    except Exception as exc:
        log_error("Failed to update global hotkeys", exc)


def _json_error(message, status=400, details=None):
    app_services.add_event("warning" if status < 500 else "error", "api", message, details or {})
    return jsonify({"ok": False, "error": message, "details": details or {}}), status

def _vrcx_plus_split_provider_text(value):
    text = str(value or "").replace("\r", "\n")
    tokens = []
    for line in text.split("\n"):
        for chunk in line.replace(";", ",").split(","):
            cleaned = str(chunk or "").strip()
            if cleaned:
                tokens.append(cleaned)
    return tokens


def _vrcx_plus_normalize_provider_urls(raw_urls, fallback_url=""):
    urls = []
    seen = set()

    def add_url(value):
        url = str(value or "").strip()
        if not url or url in seen:
            return
        seen.add(url)
        urls.append(url)

    if isinstance(raw_urls, str):
        for candidate in _vrcx_plus_split_provider_text(raw_urls):
            add_url(candidate)
    elif isinstance(raw_urls, (list, tuple, set)):
        for item in raw_urls:
            if isinstance(item, str):
                parsed = _vrcx_plus_split_provider_text(item)
                if parsed:
                    for candidate in parsed:
                        add_url(candidate)
                    continue
            add_url(item)

    if not urls:
        add_url(fallback_url)
    return urls


def _vrcx_plus_provider_settings():
    enabled = bool(SETTINGS.get("vrcx_plus_avatar_provider_enabled", False))
    fallback_url = str(SETTINGS.get("vrcx_plus_avatar_provider_url", "")).strip()
    saved_urls = _vrcx_plus_normalize_provider_urls(
        SETTINGS.get("vrcx_plus_avatar_provider_urls", []),
        fallback_url=fallback_url
    )
    urls = _vrcx_plus_normalize_provider_urls(
        list(DEFAULT_AVATAR_PROVIDER_URLS) + saved_urls,
        fallback_url=fallback_url
    )
    return {
        "enabled": enabled,
        "url": urls[0] if urls else "",
        "urls": urls,
        "count": len(urls)
    }


CHATBOX_TEMPLATE_PRESETS = [
    {
        "id": "classic",
        "name": "Classic Stack",
        "template": "{time}\n{custom}\n{song}\n{progress}\n{window}\n{heartrate}\n{weather}\n{system}\n{afk}",
        "separator": "\n"
    },
    {
        "id": "compact",
        "name": "Compact Line",
        "template": "{time} | {custom} | {song} {progress} | {heartrate} | {weather}",
        "separator": " | "
    },
    {
        "id": "status",
        "name": "Status Card",
        "template": "Status: {custom}\nNow: {song}\nVitals: {heartrate} {system}\n{weather}",
        "separator": "\n"
    },
    {
        "id": "stream",
        "name": "Streamer Clean",
        "template": "{custom}\n{song}\n{window}",
        "separator": "\n"
    }
]


CHATBOX_TEMPLATE_VARIABLES = [
    "time",
    "date",
    "custom",
    "vrchat",
    "world",
    "instance",
    "player_count",
    "capacity",
    "last_join",
    "last_leave",
    "last_event",
    "song",
    "progress",
    "window",
    "heartrate",
    "weather",
    "system",
    "afk"
]


def _get_chatbox_template_settings():
    preset_ids = {preset["id"] for preset in CHATBOX_TEMPLATE_PRESETS}
    preset = SETTINGS.get("chatbox_template_preset", "classic")
    if preset not in preset_ids:
        preset = "classic"
    separator = str(SETTINGS.get("chatbox_separator", "\n"))
    if separator == "\\n":
        separator = "\n"
    return {
        "enabled": bool(SETTINGS.get("chatbox_template_enabled", False)),
        "preset": preset,
        "template": str(SETTINGS.get("chatbox_template", CHATBOX_TEMPLATE_PRESETS[0]["template"])),
        "separator": separator,
        "blank_line_mode": SETTINGS.get("chatbox_blank_line_mode", "hide"),
        "overflow_mode": SETTINGS.get("chatbox_overflow_mode", "smart"),
        "page_indicator": bool(SETTINGS.get("chatbox_page_indicator", True)),
        "presets": CHATBOX_TEMPLATE_PRESETS,
        "variables": CHATBOX_TEMPLATE_VARIABLES
    }


def _save_chatbox_template_settings(data):
    presets = {preset["id"]: preset for preset in CHATBOX_TEMPLATE_PRESETS}
    preset_id = str(data.get("preset", SETTINGS.get("chatbox_template_preset", "classic"))).strip()
    if preset_id not in presets:
        preset_id = "custom"

    template = str(data.get("template", SETTINGS.get("chatbox_template", ""))).replace("\r\n", "\n").replace("\r", "\n")
    if not template.strip() and preset_id in presets:
        template = presets[preset_id]["template"]
    template = template[:1200]

    separator = str(data.get("separator", SETTINGS.get("chatbox_separator", "\n")))
    separator = separator.replace("\\n", "\n")[:12]

    blank_line_mode = str(data.get("blank_line_mode", SETTINGS.get("chatbox_blank_line_mode", "hide"))).strip()
    if blank_line_mode not in {"hide", "keep"}:
        blank_line_mode = "hide"

    overflow_mode = str(data.get("overflow_mode", SETTINGS.get("chatbox_overflow_mode", "smart"))).strip()
    if overflow_mode not in {"smart", "hard", "off", "page", "scroll"}:
        overflow_mode = "smart"

    SETTINGS.update({
        "chatbox_template_enabled": bool(data.get("enabled", False)),
        "chatbox_template_preset": preset_id,
        "chatbox_template": template,
        "chatbox_separator": separator,
        "chatbox_blank_line_mode": blank_line_mode,
        "chatbox_overflow_mode": overflow_mode,
        "chatbox_page_indicator": bool(data.get("page_indicator", SETTINGS.get("chatbox_page_indicator", True)))
    })
    with open(SETTINGS_FILE, "wb") as f:
        f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
    return _get_chatbox_template_settings()


def _replace_chatbox_template_variables(template, values):
    rendered = str(template or "")
    for key in CHATBOX_TEMPLATE_VARIABLES:
        rendered = rendered.replace("{" + key + "}", str(values.get(key, "") or ""))
    return rendered


def _line_has_visible_value(line, values):
    if not line.strip():
        return False
    probe = _replace_chatbox_template_variables(line, values)
    cleaned = probe.replace("|", "").replace("-", "").replace(":", "").replace("/", "").strip()
    return bool(cleaned)


def _render_chatbox_template(values):
    settings = _get_chatbox_template_settings()
    template = settings["template"]
    if not template.strip():
        return ""

    rendered_lines = []
    for line in template.split("\n"):
        if settings["blank_line_mode"] == "hide" and not _line_has_visible_value(line, values):
            continue
        rendered = _replace_chatbox_template_variables(line, values).strip()
        if settings["blank_line_mode"] == "hide":
            rendered = " ".join(rendered.split())
            rendered = rendered.strip(" |-/")
        rendered_lines.append(rendered)

    return "\n".join([line for line in rendered_lines if line or settings["blank_line_mode"] == "keep"]).strip()


_page_cycle_state = {"key": None, "index": 0}


def _advance_page_cycle(state_key, page_count, advance_page):
    if _page_cycle_state.get("key") != state_key:
        _page_cycle_state["key"] = state_key
        _page_cycle_state["index"] = 0

    index = _page_cycle_state["index"] % page_count
    if advance_page:
        _page_cycle_state["index"] = (index + 1) % page_count
    return index


def _get_paged_message(message, max_len, advance_page, content_key=None):
    show_indicator = SETTINGS.get("chatbox_page_indicator", True)
    indicator_reserve = 9 if show_indicator else 0
    content_budget = max(max_len - indicator_reserve, 20)
    pages = chatbox_frames.paginate_text(message, content_budget)

    identity = content_key if content_key is not None else message
    state_key = f"plain:{identity}\x00{content_budget}"
    index = _advance_page_cycle(state_key, len(pages), advance_page)
    page = pages[index]

    if show_indicator and len(pages) > 1:
        page = f"{page} ({index + 1}/{len(pages)})"

    return page


def _get_paged_chunk(pages, content_key, advance_page):
    if not pages:
        pages = [""]
    state_key = f"frame:{content_key}\x00{len(pages)}"
    index = _advance_page_cycle(state_key, len(pages), advance_page)
    return pages[index]


_marquee_offsets = {}
MARQUEE_SEPARATOR = "     "
_MARQUEE_STATE_LIMIT = 64


def _get_marquee_window(text, width, advance_page, content_key=None):
    text = " ".join(text.split("\n")).strip()
    if not text or width <= 0:
        return text
    if len(text) <= width:
        return text

    loop_text = text + MARQUEE_SEPARATOR
    total_len = len(loop_text)
    identity = content_key if content_key is not None else text
    state_key = f"marquee:{identity}"

    offset = _marquee_offsets.get(state_key, 0) % total_len
    doubled = loop_text + loop_text
    window = chatbox_frames.safe_cut(doubled[offset:], width)

    if advance_page:
        step = max(2, width // 4)
        _marquee_offsets[state_key] = (offset + step) % total_len
        if len(_marquee_offsets) > _MARQUEE_STATE_LIMIT:
            _marquee_offsets.pop(next(iter(_marquee_offsets)))

    return window


def _apply_chatbox_overflow(message, advance_page=False, content_key=None):
    mode = SETTINGS.get("chatbox_overflow_mode", "smart")
    if mode == "off":
        return message

    max_len = VRCHAT_CHAR_LIMIT
    if SETTINGS.get("slim_chatbox", False):
        max_len = VRCHAT_CHAR_LIMIT - SLIM_SUFFIX_LENGTH

    if not message or len(message) <= max_len:
        return message
    if mode == "hard":
        return chatbox_frames.safe_cut(message, max_len)
    if mode == "page":
        return _get_paged_message(message, max_len, advance_page, content_key=content_key)
    if mode == "scroll":
        lines = message.split("\n")
        if len(lines) == 1:
            return _get_marquee_window(message, max_len, advance_page, content_key=content_key)
        result_lines = []
        for index, line in enumerate(lines):
            window = _get_marquee_window(line, max_len, advance_page, content_key=f"row:{index}")
            result_lines.append(window)
        combined = "\n".join(result_lines)
        return combined if len(combined) <= max_len else smart_truncate_message(combined)
    return smart_truncate_message(message)


def replace_variables(text):
    if not text:
        return text
    
    result = text
    
    tz_setting = SETTINGS.get("timezone", "local")
    if tz_setting == "local":
        now = datetime.now()
    else:
        now = datetime.now(pytz.timezone(str(tz_setting)))
    time_str = now.strftime("%I:%M %p").lstrip("0")
    
    sstate = spotify.get_spotify_state()
    song_str = sstate.get("song_text", "No song playing")
    live_state = _get_vrchat_live_state()
    live_values = _build_vrchat_live_values(live_state)
    live_values["vrchat"] = _format_vrchat_live_line(live_state)
    
    result = result.replace("{time}", time_str)
    result = result.replace("{date}", now.strftime("%Y-%m-%d"))
    result = result.replace("{song}", song_str)
    result = _replace_braced_variables(result, live_values)
    
    return result

def get_next_custom_message():
    global text_cycle_index, CUSTOM_TEXTS
    
    if not CUSTOM_TEXTS:
        return ""
    
    if SETTINGS.get("random_order", False):
        weighted_messages = SETTINGS.get("weighted_messages", {})
        
        if weighted_messages:
            weights = []
            for idx in range(len(CUSTOM_TEXTS)):
                weight = weighted_messages.get(str(idx), 1)
                weights.append(weight)
            
            text_cycle_index = random.choices(range(len(CUSTOM_TEXTS)), weights=weights, k=1)[0]
        else:
            text_cycle_index = random.randint(0, len(CUSTOM_TEXTS) - 1)
    else:
        text_cycle_index = (text_cycle_index + 1) % len(CUSTOM_TEXTS)
    
    return CUSTOM_TEXTS[text_cycle_index]

def update_message_queue():
    global message_queue, CUSTOM_TEXTS
    
    queue_count = SETTINGS.get("message_queue_preview_count", 3)
    message_queue = []
    
    if not CUSTOM_TEXTS:
        return
    
    temp_index = text_cycle_index
    for i in range(queue_count):
        if SETTINGS.get("random_order", False):
            message_queue.append("Random")
        else:
            next_idx = (temp_index + i) % len(CUSTOM_TEXTS)
            msg = CUSTOM_TEXTS[next_idx]
            message_queue.append(msg[:30] + "..." if len(msg) > 30 else msg)

def _is_spacer_key(value):
    return isinstance(value, str) and value.startswith("spacer_")


def _bounded_int_setting(key, default, minimum, maximum):
    try:
        value = int(SETTINGS.get(key, default))
    except Exception:
        value = default
    return max(minimum, min(value, maximum))

def _format_stat_value(value, suffix="%"):
    decimals = _bounded_int_setting("system_stats_decimals", 0, 0, 2)
    try:
        number = float(value or 0)
    except Exception:
        number = 0
    if decimals <= 0:
        text = str(int(round(number)))
    else:
        text = f"{number:.{decimals}f}"
    return f"{text}{suffix}"

def _format_network_stat(value):
    units = str(SETTINGS.get("system_stats_network_units", "bits")).lower()
    if units == "bytes":
        kb_per_second = float(value or 0)
        if kb_per_second >= 1024:
            return f"{round(kb_per_second / 1024, 1)}MB/s"
        return f"{round(kb_per_second, 1)}KB/s"
    return system_stats.format_network_speed(value)

SYSTEM_STATS_TEMPLATE_VARIABLES = [
    "system_emoji",
    "cpu_emoji",
    "ram_emoji",
    "gpu_emoji",
    "network_emoji",
    "battery_emoji",
    "cpu",
    "cpu_raw",
    "ram",
    "ram_raw",
    "ram_used",
    "ram_total",
    "gpu",
    "gpu_raw",
    "download",
    "upload",
    "down",
    "up",
    "battery",
]

DEFAULT_SYSTEM_STATS_TEMPLATE = "{system_emoji} {cpu_emoji} CPU {cpu} | {ram_emoji} RAM {ram}"

def _replace_braced_variables(template, values):
    result = str(template or "")
    for key, value in values.items():
        result = result.replace("{" + key + "}", str(value or ""))
    return result


def _public_vrchat_live_state(state):
    out = deepcopy(state if isinstance(state, dict) else {})
    if SETTINGS.get("streamer_mode", False):
        out["location"] = ""
        out["instance_id"] = ""
    return out


def _extract_vrchat_location(value):
    text = str(value or "").strip()
    if not text:
        return "", "", ""
    candidates = [text]
    try:
        parsed = urlparse(text)
        query = parse_qs(parsed.query)
        for key in ("worldId", "location", "instanceId"):
            candidates.extend(query.get(key, []))
        if parsed.fragment:
            candidates.append(parsed.fragment)
    except Exception:
        pass
    for candidate in candidates:
        decoded = unquote(str(candidate or "").strip())
        if "wrld_" not in decoded:
            continue
        start = decoded.find("wrld_")
        location = decoded[start:].split("&", 1)[0].split("?", 1)[0].strip()
        if ":" in location:
            world_id, instance_id = location.split(":", 1)
        else:
            world_id, instance_id = location, ""
        if world_id.startswith("wrld_"):
            return location, world_id, instance_id
    return "", "", ""


def _get_vrchat_live_state(force_refresh=False):
    state = vrchat_live.get_state()
    world_id = str(state.get("world_id") or "").strip()
    instance_id = str(state.get("instance_id") or "").strip()
    world = None
    instance = None


    if not world_id:
        manual_location, manual_world_id, manual_instance_id = _extract_vrchat_location(SETTINGS.get("vrchat_live_manual_location", ""))
        if manual_world_id and vrchat_live.apply_account_location(
            location=manual_location,
            world_id=manual_world_id,
            instance_id=manual_instance_id,
            source="Manual location",
        ):
            state = vrchat_live.get_state()
            world_id = str(state.get("world_id") or "").strip()
            instance_id = str(state.get("instance_id") or "").strip()

    if not world_id:
        account_location = vrchat_service.current_user_location(force_refresh=force_refresh)
        if account_location.get("ok") and account_location.get("world_id"):
            source = "VRChat account (cached)" if account_location.get("stale") else "VRChat account"
            if vrchat_live.apply_account_location(
                location=account_location.get("location", ""),
                world_id=account_location.get("world_id", ""),
                instance_id=account_location.get("instance_id", ""),
                source=source,
            ):
                state = vrchat_live.get_state()
                world_id = str(state.get("world_id") or "").strip()
                instance_id = str(state.get("instance_id") or "").strip()
        elif force_refresh and not world_id:
            state = dict(state)
            account_error = account_location.get("error") or "Log in under VRChat Account to use account-based live instance detection."
            existing_error = str(state.get("last_error") or "").strip()
            state["last_error"] = f"{existing_error} Account fallback: {account_error}".strip()

    if world_id and (force_refresh or not state.get("world_name")):
        result = vrchat_service.get_world(world_id, force_refresh=force_refresh)
        if result.get("ok"):
            world = result.get("world") or {}

    if world_id and instance_id and (force_refresh or not state.get("capacity") or not state.get("player_count")):
        result = vrchat_service.get_world_instance(world_id, instance_id, force_refresh=force_refresh)
        if result.get("ok"):
            instance = result.get("instance") or {}

    if world or instance:
        vrchat_live.apply_world_details(world=world, instance=instance)
        state = vrchat_live.get_state()

    return _public_vrchat_live_state(state)


def _get_steamvr_launch_state():
    supported = steamvr_launch.is_supported()
    return {
        "supported": supported,
        "enabled": bool(SETTINGS.get("steamvr_auto_launch_enabled", False)),
        "registered": steamvr_launch.is_registered() if supported else False,
        "auto_launch_confirmed": steamvr_launch.is_auto_launch_enabled() if supported else False,
    }


def _build_vrchat_live_values(state=None):
    live = state if isinstance(state, dict) else _get_vrchat_live_state()
    world = str(live.get("world_name") or live.get("world_id") or "").strip()
    instance = str(live.get("instance_privacy") or live.get("instance_short") or "").strip()
    player_count = int(live.get("player_count") or 0)
    capacity = int(live.get("capacity") or 0)
    last_join = str(live.get("last_join") or "").strip()
    last_leave = str(live.get("last_leave") or "").strip()
    last_event = str(live.get("last_event") or "").strip()
    return {
        "world": world,
        "instance": instance,
        "player_count": player_count,
        "capacity": capacity if capacity > 0 else "?",
        "last_join": last_join,
        "last_leave": last_leave,
        "last_event": last_event,
    }


def _format_vrchat_live_line(state=None):
    live = state if isinstance(state, dict) else _get_vrchat_live_state()
    if not live.get("enabled", True):
        return ""
    values = _build_vrchat_live_values(live)
    if not values["world"] and not values["last_event"]:
        return ""
    template = str(SETTINGS.get("vrchat_live_template", "{world} ({player_count}/{capacity}) | {instance} | {last_event}") or "")
    rendered = _replace_braced_variables(template, values)
    return "\n".join(" ".join(line.split()) for line in rendered.splitlines() if line.strip()).strip(" |-/")


def _automation_override(template_values):
    now = time.time()
    active_profile = SETTINGS.get("active_profile", "Default")
    active_rules = []
    for rule in SETTINGS.get("automation_rules", []):
        if not isinstance(rule, dict) or not rule.get("enabled", False):
            continue
        rule_profile = str(rule.get("profile") or active_profile)
        if rule_profile and rule_profile != active_profile:
            continue
        trigger = str(rule.get("trigger") or "timed").lower()
        if trigger == "idle" and not afk_detector.is_afk():
            continue
        interval = max(5, int(rule.get("interval_seconds", 60) or 60))
        last_run = float(automation_runtime.get(rule.get("id"), 0.0))
        if trigger in {"timed", "always", "idle"} and (now - last_run) >= interval:
            active_rules.append(rule)
    if not active_rules:
        return ""
    active_rules.sort(key=lambda item: (-int(item.get("priority", 50) or 50), str(item.get("name", ""))))
    selected = active_rules[0]
    automation_runtime[selected.get("id")] = now
    rendered = _replace_chatbox_template_variables(selected.get("message_template", ""), template_values).strip()
    if rendered:
        app_services.add_event(
            "info",
            "automations",
            f"Automation produced message: {selected.get('name', 'Automation')}",
            {"automation_id": selected.get("id"), "priority": selected.get("priority", 50)},
        )
    return rendered


_last_world_preset_check = {"world_id": None}


def _check_world_preset_switch(live_state, force=False):
    if not SETTINGS.get("world_preset_auto_switch_enabled", False):
        return
    world_id = str((live_state or {}).get("world_id") or "").strip()
    if not world_id:
        return
    if not force and world_id == _last_world_preset_check["world_id"]:
        return
    _last_world_preset_check["world_id"] = world_id
    for rule in SETTINGS.get("world_preset_rules", []):
        if not isinstance(rule, dict):
            continue
        if str(rule.get("world_id", "")).strip() != world_id:
            continue
        preset_id = rule.get("preset_id")
        if not preset_id or preset_id == SETTINGS.get("active_preset_id"):
            return
        applied = app_services.apply_preset(preset_id)
        if applied:
            _sync_runtime_from_settings()
            app_services.add_event(
                "info", "presets",
                f"Auto-applied preset '{applied['name']}' for world '{rule.get('world_name') or world_id}'",
                {"world_id": world_id, "preset_id": preset_id},
            )
        return


def _format_system_stats_line(stats=None, include_main_icon=True):
    stats = stats or system_stats.get_system_stats()
    if not stats or not stats.get("available", False):
        return ""

    show_icons = SETTINGS.get("show_module_icons", True)
    decimals = _bounded_int_setting("system_stats_decimals", 0, 0, 2)
    show_cpu = SETTINGS.get("system_stats_show_cpu", True)
    show_ram = SETTINGS.get("system_stats_show_ram", True)
    show_gpu = SETTINGS.get("system_stats_show_gpu", False)
    show_network = SETTINGS.get("system_stats_show_network", False)
    show_battery = SETTINGS.get("system_stats_show_battery", False)
    template = str(SETTINGS.get("system_stats_template", DEFAULT_SYSTEM_STATS_TEMPLATE) or DEFAULT_SYSTEM_STATS_TEMPLATE)

    def raw_number(value):
        try:
            number = float(value or 0)
        except Exception:
            number = 0
        if decimals <= 0:
            return str(int(round(number)))
        return f"{number:.{decimals}f}"

    values = {
        "system_emoji": SETTINGS.get("system_stats_emoji", "📊") if show_icons and include_main_icon else "",
        "cpu_emoji": SETTINGS.get("system_stats_cpu_emoji", "🧠") if show_icons and show_cpu else "",
        "ram_emoji": SETTINGS.get("system_stats_ram_emoji", "💾") if show_icons and show_ram else "",
        "gpu_emoji": SETTINGS.get("system_stats_gpu_emoji", "🎮") if show_icons and show_gpu else "",
        "network_emoji": SETTINGS.get("system_stats_network_emoji", "📡") if show_icons and show_network else "",
        "battery_emoji": SETTINGS.get("system_stats_battery_emoji", "🔋") if show_icons and show_battery else "",
        "cpu": _format_stat_value(stats.get("cpu_percent", 0)) if show_cpu else "",
        "cpu_raw": raw_number(stats.get("cpu_percent", 0)) if show_cpu else "",
        "ram": _format_stat_value(stats.get("ram_percent", 0)) if show_ram else "",
        "ram_raw": raw_number(stats.get("ram_percent", 0)) if show_ram else "",
        "ram_used": f"{stats.get('ram_used_gb', 0)}GB" if show_ram else "",
        "ram_total": f"{stats.get('ram_total_gb', 0)}GB" if show_ram else "",
        "gpu": _format_stat_value(stats.get("gpu_percent", 0)) if show_gpu and stats.get("gpu_available", False) else ("N/A" if show_gpu else ""),
        "gpu_raw": raw_number(stats.get("gpu_percent", 0)) if show_gpu and stats.get("gpu_available", False) else ("N/A" if show_gpu else ""),
        "download": _format_network_stat(stats.get("network_recv_speed", 0)) if show_network else "",
        "upload": _format_network_stat(stats.get("network_sent_speed", 0)) if show_network else "",
        "down": _format_network_stat(stats.get("network_recv_speed", 0)) if show_network else "",
        "up": _format_network_stat(stats.get("network_sent_speed", 0)) if show_network else "",
        "battery": (
            _format_stat_value(stats.get("battery_percent", 0)) + (" (charging)" if stats.get("battery_plugged") else "")
            if show_battery and stats.get("battery_available", False)
            else ("N/A" if show_battery else "")
        ),
    }

    rendered = _replace_braced_variables(template, values)
    return "\n".join(" ".join(line.split()) for line in rendered.splitlines() if line.strip())

def get_current_preview(advance_page=False):
    global current_time_text, current_custom_text
    
    if show_time:
        tz_setting = SETTINGS.get("timezone", "local")
        if tz_setting == "local":
            now = datetime.now()
        else:
            now = datetime.now(pytz.timezone(str(tz_setting)))
        current_time_text = now.strftime("%I:%M %p").lstrip("0")
    else:
        current_time_text = ""

    sstate = spotify.get_spotify_state()
    session_insights.note_song(sstate.get("song_text"))
    song_line = ""
    progress_line = ""
    if show_music and sstate.get("song_text"):
        pos = int(sstate.get("song_pos", 0))
        dur = int(sstate.get("song_dur", 0))
        show_icons = SETTINGS.get("show_module_icons", True)
        song_emoji = SETTINGS.get("song_emoji", "🎶")
        icon = f"{song_emoji} " if show_icons and song_emoji else ""
        if dur > 0:
            elapsed_min, elapsed_sec = divmod(pos, 60)
            total_min, total_sec = divmod(dur, 60)
            song_line = f"{icon}{sstate['song_text']} [{elapsed_min}:{elapsed_sec:02d} / {total_min}:{total_sec:02d}]"
        else:
            song_line = f"{icon}{sstate['song_text']}"
        if SETTINGS.get("music_progress", True) and dur > 0:
            style = SETTINGS.get("progress_style", "bar")
            progress_percent = int((pos / dur) * 100) if dur > 0 else 0
            if style == "bar":
                filled = int(progress_percent / 10)
                empty = 10 - filled
                progress_line = "█" * filled + "░" * empty
            elif style == "dots":
                filled = int(progress_percent / 10)
                empty = 10 - filled
                progress_line = "●" * filled + "○" * empty
            elif style == "percentage":
                progress_line = f"{progress_percent}%"

    wstate = window_tracker.get_window_state()
    window_line = ""
    if show_window and wstate.get("app_name"):
        show_icons = SETTINGS.get("show_module_icons", True)
        window_emoji = SETTINGS.get("window_emoji", "💻")
        icon = f"{window_emoji} " if show_icons and window_emoji else ""
        window_prefix = SETTINGS.get("window_prefix", "Currently on:")
        if window_prefix:
            window_line = f"{icon}{window_prefix} {wstate['app_name']}"
        else:
            window_line = f"{icon}{wstate['app_name']}"

    hrstate = heart_rate_monitor.get_heart_rate_state()
    heartrate_line = ""
    if show_heartrate and hrstate.get("is_connected") and hrstate.get("bpm", 0) > 0:
        show_icons = SETTINGS.get("show_module_icons", True)
        heartrate_emoji = SETTINGS.get("heartrate_emoji", "❤️")
        icon = f"{heartrate_emoji} " if show_icons and heartrate_emoji else ""
        heartrate_line = f"{icon}{hrstate['bpm']} BPM"

    weather_line = ""
    if show_weather:
        temp_unit = SETTINGS.get("weather_temp_unit", "F")
        weather_text = weather_service.get_weather_text(temp_unit)
        if weather_text:
            weather_emoji = SETTINGS.get("weather_emoji", "🌤️")
            if SETTINGS.get("show_module_icons", True) and weather_emoji:
                weather_without_icon = weather_text.split(" ", 1)[1] if " " in weather_text else weather_text
                weather_line = f"{weather_emoji} {weather_without_icon}"
            else:
                weather_line = weather_text.split(" ", 1)[1] if " " in weather_text else weather_text

    live_state = _get_vrchat_live_state()
    _check_world_preset_switch(live_state)
    vrchat_live_line = ""
    if SETTINGS.get("show_vrchat_live", False):
        vrchat_live_line = _format_vrchat_live_line(live_state)

    system_stats_line = ""
    if SETTINGS.get("system_stats_enabled", False):
        stats = system_stats.get_system_stats()
        system_stats_line = _format_system_stats_line(stats)

    vr_battery_line = ""
    if SETTINGS.get("show_vr_battery", False):
        battery_text = vr_battery.get_battery_text(
            include_controllers=SETTINGS.get("vr_battery_include_controllers", True),
            include_trackers=SETTINGS.get("vr_battery_include_trackers", False),
            low_battery_threshold=SETTINGS.get("vr_battery_low_threshold", 20),
        )
        if battery_text:
            battery_emoji = SETTINGS.get("vr_battery_emoji", "🔋")
            if SETTINGS.get("show_module_icons", True) and battery_emoji:
                vr_battery_line = f"{battery_emoji} {battery_text}"
            else:
                vr_battery_line = battery_text

    volume_line = ""
    if SETTINGS.get("show_volume", False):
        volume_text = volume_monitor.get_volume_text()
        if volume_text:
            volume_emoji = SETTINGS.get("volume_emoji", "🔊")
            if SETTINGS.get("show_module_icons", True) and volume_emoji:
                volume_line = f"{volume_emoji} {volume_text}"
            else:
                volume_line = volume_text

    device_storage_line = ""
    if SETTINGS.get("show_device_storage", False):
        storage_text = device_status.get_storage_text()
        if storage_text:
            storage_emoji = SETTINGS.get("device_storage_emoji", "💾")
            if SETTINGS.get("show_module_icons", True) and storage_emoji:
                device_storage_line = f"{storage_emoji} {storage_text}"
            else:
                device_storage_line = storage_text

    mute_line = ""
    if SETTINGS.get("mute_indicator_enabled", False) and osc_reactions.is_muted():
        mute_line = SETTINGS.get("mute_indicator_text", "🔇 Muted")

    afk_line = ""
    if SETTINGS.get("afk_enabled", False):
        timeout = SETTINGS.get("afk_timeout", 300)
        afk_detector.check_afk(timeout)
        if afk_detector.is_afk():
            afk_msg = afk_detector.get_afk_message(
                SETTINGS.get("afk_message", ""),
                SETTINGS.get("afk_show_duration", True)
            )
            if afk_msg:
                show_icons = SETTINGS.get("show_module_icons", True)
                afk_emoji = SETTINGS.get("afk_emoji", "💤")
                icon = f"{afk_emoji} " if show_icons and afk_emoji else ""
                afk_line = f"{icon}{afk_msg}"

    show_icons = SETTINGS.get("show_module_icons", True)
    lines = []
    layout = SETTINGS.get("layout_order", ["time","custom","vrchat_live","song","window","heartrate","weather","system_stats","afk"])
    
    if SETTINGS.get("system_stats_enabled", False) and "system_stats" not in layout:
        layout = list(layout) + ["system_stats"]
    if SETTINGS.get("afk_enabled", False) and "afk" not in layout:
        layout = list(layout) + ["afk"]
    if SETTINGS.get("show_vrchat_live", False) and "vrchat_live" not in layout:
        layout = list(layout) + ["vrchat_live"]
    if SETTINGS.get("show_vr_battery", False) and "vr_battery" not in layout:
        layout = list(layout) + ["vr_battery"]
    if SETTINGS.get("show_volume", False) and "volume" not in layout:
        layout = list(layout) + ["volume"]
    if SETTINGS.get("show_device_storage", False) and "device_storage" not in layout:
        layout = list(layout) + ["device_storage"]
    if SETTINGS.get("mute_indicator_enabled", False) and "mute" not in layout:
        layout = list(layout) + ["mute"]

    tz_setting = SETTINGS.get("timezone", "local")
    now = datetime.now() if tz_setting == "local" else datetime.now(pytz.timezone(str(tz_setting)))
    processed_custom_text = replace_variables(current_custom_text) if current_custom_text else ""
    custom_emoji = SETTINGS.get("custom_emoji", "💬")
    custom_line = (
        f"{custom_emoji} {processed_custom_text}"
        if show_icons and custom_emoji and processed_custom_text
        else processed_custom_text
    )
    template_values = {
        "time": f"{SETTINGS.get('time_emoji', '⏰')} {current_time_text}" if show_icons and SETTINGS.get("time_emoji", "⏰") and current_time_text else current_time_text,
        "date": now.strftime("%Y-%m-%d"),
        "custom": custom_line if show_custom else "",
        "vrchat": vrchat_live_line,
        **_build_vrchat_live_values(live_state),
        "song": song_line,
        "progress": progress_line,
        "window": window_line,
        "heartrate": heartrate_line,
        "weather": weather_line,
        "system": system_stats_line,
        "vr_battery": vr_battery_line,
        "volume": volume_line,
        "device_storage": device_storage_line,
        "afk": afk_line,
        "mute": mute_line
    }

    if SETTINGS.get("chatbox_template_enabled", False):
        result = _render_chatbox_template(template_values)
    else:
        for part in layout:
            if part == "time" and current_time_text:
                lines.append(template_values["time"])
            elif part == "custom" and custom_line:
                lines.append(custom_line)
            elif part == "vrchat_live" and vrchat_live_line:
                lines.append(vrchat_live_line)
            elif part == "song" and song_line:
                lines.append(song_line)
                if progress_line:
                    lines.append(progress_line)
            elif part == "window" and window_line:
                lines.append(window_line)
            elif part == "heartrate" and heartrate_line:
                lines.append(heartrate_line)
            elif part == "weather" and weather_line:
                lines.append(weather_line)
            elif part == "system_stats" and system_stats_line:
                lines.append(system_stats_line)
            elif part == "vr_battery" and vr_battery_line:
                lines.append(vr_battery_line)
            elif part == "volume" and volume_line:
                lines.append(volume_line)
            elif part == "device_storage" and device_storage_line:
                lines.append(device_storage_line)
            elif part == "afk" and afk_line:
                lines.append(afk_line)
            elif part == "mute" and mute_line:
                lines.append(mute_line)
            elif _is_spacer_key(part):


                lines.append(SETTINGS.get("layout_spacers", {}).get(part, ""))

        separator = SETTINGS.get("chatbox_separator", "\n")
        result = str(separator).join(lines).strip()


    page_content_key = current_custom_text or ""

    automation_message = _automation_override(template_values)
    if automation_message:
        result = automation_message
        page_content_key = automation_message

    text_effect = SETTINGS.get("text_effect", "none")
    if text_effect and text_effect != "none":
        try:
            result = text_effects.apply_effect(result, text_effect)
        except Exception as e:
            log_error(f"Failed to apply text effect '{text_effect}'", e)

    max_len = VRCHAT_CHAR_LIMIT - (SLIM_SUFFIX_LENGTH if SETTINGS.get("slim_chatbox", False) else 0)
    frame_style = SETTINGS.get("chatbox_frame", "none")
    frame_emoji = SETTINGS.get("chatbox_frame_emoji", chatbox_frames.DEFAULT_FRAME_EMOJI)
    overflow_mode = SETTINGS.get("chatbox_overflow_mode", "smart")


    if overflow_mode == "page" and frame_style != "none" and len(result) > max_len:
        try:
            width, lines_per_page = chatbox_frames.plan_frame_capacity(frame_style, max_len, emoji=frame_emoji)
            wrapped = chatbox_frames.wrap_for_frame(result, width)
            pages = chatbox_frames.chunk_lines(wrapped, lines_per_page)
            page_text = _get_paged_chunk(pages, page_content_key, advance_page)
            result = chatbox_frames.apply_frame(page_text, frame_style, max_total_length=max_len, emoji=frame_emoji)
        except Exception as e:
            log_error(f"Failed to apply paged frame style '{frame_style}'", e)
            result = chatbox_frames.safe_cut(result, max_len)
        return result

    if overflow_mode == "scroll" and frame_style != "none":
        try:
            width, _lines = chatbox_frames.plan_frame_capacity(frame_style, max_len, emoji=frame_emoji)

            custom_marquee_source = None
            if show_custom and custom_line:
                all_custom = [t for t in CUSTOM_TEXTS if t and t.strip()]
                if len(all_custom) > 1:
                    joined = "     •     ".join(replace_variables(t) for t in all_custom)
                    custom_marquee_source = f"{custom_emoji} {joined}" if show_icons and custom_emoji else joined

            line_roles = {}
            for role, value in template_values.items():
                if value:
                    line_roles[value] = role
            if custom_line:
                line_roles[custom_line] = "custom"
            if progress_line:
                line_roles[progress_line] = "progress"

            advanced_lines = set()
            line_positions = {}

            def line_fit(line, w):
                source = custom_marquee_source if (custom_marquee_source and line == custom_line) else line
                if line in line_roles:
                    slot_key = line_roles[line]
                else:
                    if line not in line_positions:
                        line_positions[line] = len(line_positions)
                    slot_key = f"pos:{line_positions[line]}"
                should_advance = advance_page and slot_key not in advanced_lines
                window = _get_marquee_window(source, w, should_advance, content_key=f"role:{slot_key}")
                if should_advance:
                    advanced_lines.add(slot_key)
                return window.center(w)

            result = chatbox_frames.apply_frame(result, frame_style, max_total_length=max_len, emoji=frame_emoji, line_fit=line_fit)
            return result
        except Exception as e:
            log_error(f"Failed to apply scrolling frame style '{frame_style}'", e)
            result = chatbox_frames.safe_cut(result, max_len)
            return result

    if frame_style and frame_style != "none":
        try:
            result = chatbox_frames.apply_frame(result, frame_style, max_total_length=max_len, emoji=frame_emoji)
        except Exception as e:
            log_error(f"Failed to apply frame style '{frame_style}'", e)

    result = _apply_chatbox_overflow(result, advance_page=advance_page, content_key=page_content_key)

    return result

VRCHAT_CHAR_LIMIT = 144
SLIM_SUFFIX_LENGTH = 2

def smart_truncate_message(message):
    if not message:
        return message
    
    max_len = VRCHAT_CHAR_LIMIT
    if SETTINGS.get("slim_chatbox", False):
        max_len = VRCHAT_CHAR_LIMIT - SLIM_SUFFIX_LENGTH
    
    if len(message) <= max_len:
        return message
    
    lines = message.split('\n')
    
    if len(lines) == 1:
        return chatbox_frames.safe_cut(message, max_len - 3) + "..."

    result_lines = []
    current_length = 0

    for i, line in enumerate(lines):
        line_with_newline = line if i == 0 else '\n' + line
        new_length = current_length + len(line_with_newline)

        if new_length <= max_len:
            result_lines.append(line)
            current_length = new_length
        else:
            remaining = max_len - current_length
            if i == 0:
                result_lines.append(chatbox_frames.safe_cut(line, max_len - 3) + "...")
            elif remaining > 10:
                truncated = chatbox_frames.safe_cut(line, remaining - 4) + "..."
                result_lines.append(truncated)
            break

    if not result_lines:
        return chatbox_frames.safe_cut(message, max_len - 3) + "..."
    
    return '\n'.join(result_lines)

SLIM_CHATBOX_SUFFIX = "\x03\x1f"

def send_to_vrchat(message):
    global last_message_sent, connection_status, last_successful_send, last_osc_send_time
    
    current_time = time.time()
    if current_time - last_osc_send_time < 0.5:
        return False
    
    last_osc_send_time = current_time
    
    if message:
        try:
            if SETTINGS.get("slim_chatbox", False):
                if len(message) + SLIM_SUFFIX_LENGTH <= VRCHAT_CHAR_LIMIT:
                    message = message + SLIM_CHATBOX_SUFFIX
                else:
                    message = message[:VRCHAT_CHAR_LIMIT - SLIM_SUFFIX_LENGTH] + SLIM_CHATBOX_SUFFIX
            elif len(message) > VRCHAT_CHAR_LIMIT:
                message = message[:VRCHAT_CHAR_LIMIT]
            client.send_message("/chatbox/input", [message, True])
            last_message_sent = message
            connection_status = "connected"
            last_successful_send = datetime.now()
        except Exception as e:
            connection_status = "disconnected"
            log_error("Failed to send OSC message", e)
            _safe_print("[VRChat OSC ERROR]", e)
            return False
        _safe_print(f"[VRChat OSC SENT]\n{message}\n------------------")
        session_insights.note_message_sent()
        message_history.add_sent_message(message)
        return True
    return False


def _send_heart_rate_osc_params():
    global last_hr_osc_send_time, last_hr_osc_bpm, last_hr_osc_connected
    global last_hr_osc_beat_time, last_hr_osc_offline_send

    if not SETTINGS.get("heart_rate_osc_enabled", False):
        return

    now = time.time()
    hrstate = heart_rate_monitor.get_heart_rate_state()
    bpm = int(hrstate.get("bpm", 0) or 0)
    connected = bool(hrstate.get("is_connected", False))
    active = connected and bpm > 0

    try:
        min_bpm = int(SETTINGS.get("heart_rate_osc_min_bpm", 40))
        max_bpm = int(SETTINGS.get("heart_rate_osc_max_bpm", 200))
    except Exception:
        min_bpm, max_bpm = 40, 200
    if max_bpm <= min_bpm:
        min_bpm, max_bpm = 40, 200

    try:
        if active:
            should_send = bpm != last_hr_osc_bpm or (now - last_hr_osc_send_time) >= 5.0
            if should_send:
                percent = max(0.0, min(1.0, (bpm - min_bpm) / float(max_bpm - min_bpm)))
                stats = heart_rate_monitor.get_hr_stats()
                trend = stats.get("trend", "stable")
                trend_value = 1 if trend == "rising" else (-1 if trend == "falling" else 0)

                client.send_message("/avatar/parameters/isHRConnected", True)
                client.send_message("/avatar/parameters/isHRActive", True)
                client.send_message("/avatar/parameters/HR", bpm)
                client.send_message("/avatar/parameters/HRPercent", percent)
                client.send_message("/avatar/parameters/HRTrendIndicator", trend_value)
                client.send_message("/avatar/parameters/isHRBeat", True)

                last_hr_osc_bpm = bpm
                last_hr_osc_connected = True
                last_hr_osc_send_time = now
                last_hr_osc_beat_time = now

            if last_hr_osc_beat_time and (now - last_hr_osc_beat_time) >= 0.2:
                client.send_message("/avatar/parameters/isHRBeat", False)
                last_hr_osc_beat_time = 0
            return

        if last_hr_osc_connected or (now - last_hr_osc_offline_send) >= 10.0:
            client.send_message("/avatar/parameters/isHRConnected", connected)
            client.send_message("/avatar/parameters/isHRActive", False)
            client.send_message("/avatar/parameters/isHRBeat", False)
            client.send_message("/avatar/parameters/HR", 0)
            client.send_message("/avatar/parameters/HRPercent", 0.0)
            client.send_message("/avatar/parameters/HRTrendIndicator", 0)
            last_hr_osc_connected = False
            last_hr_osc_bpm = 0
            last_hr_osc_offline_send = now
    except Exception as e:
        log_error("Failed to send heart rate OSC parameters", e)


def _trigger_temporary_message(message, duration=None):
    message = str(message or "").strip()
    if not message:
        return False
    display_duration = duration if duration is not None else SETTINGS.get("typed_message_duration", 5)
    with typing_state_lock:
        typing_state["is_typing"] = False
        typing_state["typed_message"] = message
        typing_state["display_until"] = time.time() + display_duration
        typing_state["show_indicator"] = False
        typing_state["message_sent"] = False
    message_history.add_typed_message(message)
    return True


def test_osc_connection():
    global connection_status
    try:
        client.send_message("/chatbox/visible", 1)
        time.sleep(0.1)
        client.send_message("/chatbox/input", ["🔔 Connection Test", True])
        connection_status = "connected"
        return True
    except Exception as e:
        connection_status = "disconnected"
        log_error("OSC connection test failed", e)
        return False

def format_typed_message(text):
    if not text:
        return text

    result = text

    effect = SETTINGS.get("text_effect", "none")
    if effect != "none":
        result = text_effects.apply_effect(result, effect)

    max_len = VRCHAT_CHAR_LIMIT - (SLIM_SUFFIX_LENGTH if SETTINGS.get("slim_chatbox", False) else 0)

    frame_style = SETTINGS.get("chatbox_frame", "none")
    if frame_style != "none":
        result = chatbox_frames.apply_frame(result, frame_style, max_total_length=max_len, emoji=SETTINGS.get("chatbox_frame_emoji", chatbox_frames.DEFAULT_FRAME_EMOJI))
    elif len(result) > max_len:
        result = smart_truncate_message(result)

    if SETTINGS.get("slim_chatbox", False):
        result = result + "\x03\x1f"

    return result


SCROLL_SPEED_INTERVALS = {"slow": 2.0, "normal": 1.0, "fast": 0.5}


def _scroll_is_active():
    return (
        SETTINGS.get("chatbox_overflow_mode", "smart") == "scroll"
        and SETTINGS.get("chatbox_frame", "none") != "none"
    )


def start_scroll_ticker():
    def ticker():
        print("[Scroll Ticker] Thread started")
        while True:
            try:
                if not _scroll_is_active():
                    time.sleep(1)
                    continue
                if not chatbox_visible or auto_send_paused:
                    time.sleep(1)
                    continue

                speed = SETTINGS.get("chatbox_scroll_speed", "normal")
                interval = SCROLL_SPEED_INTERVALS.get(speed, 1.0)

                preview_msg = get_current_preview(advance_page=True)
                if preview_msg:
                    send_to_vrchat(preview_msg)
                time.sleep(interval)
            except Exception as e:
                log_error("Scroll ticker error", e)
                time.sleep(1)

    threading.Thread(target=ticker, daemon=True).start()


def start_vrc_updater():
    def updater():
        global current_time_text, current_custom_text, last_message_sent
        global text_cycle_index, next_custom_in, per_message_timers, client
        global typing_state
        print("[VRChat Updater] Thread started")
        print(f"[VRChat Updater] Initial CUSTOM_TEXTS count: {len(CUSTOM_TEXTS)}")
        print(f"[VRChat Updater] Initial show_custom: {show_custom}")

        osc_interval = max(1, int(SETTINGS.get("osc_send_interval", 3)))
        next_osc_send = osc_interval
        
        per_message_intervals = SETTINGS.get("per_message_intervals", {})
        for idx in range(len(CUSTOM_TEXTS)):
            key = str(idx)
            if key not in per_message_timers:
                per_message_timers[key] = per_message_intervals.get(key, osc_interval)

        last_quest_ip = SETTINGS.get("quest_ip", "")
        rotation_log_counter = 0
        last_typing_indicator = False

        while True:
            try:
                time.sleep(1)
                _send_heart_rate_osc_params()
                
                current_quest_ip = SETTINGS.get("quest_ip", "")
                if current_quest_ip != last_quest_ip:
                    print(f"[Auto-Reconnect] Quest or Desktop IP changed from {last_quest_ip} to {current_quest_ip}")
                    client = make_client()
                    last_quest_ip = current_quest_ip
                
                with typing_state_lock:
                    is_typing = typing_state["is_typing"]
                    typed_message = typing_state["typed_message"]
                    display_until = typing_state["display_until"]
                    show_indicator = typing_state["show_indicator"]
                
                current_time_val = time.time()
                
                with typing_state_lock:
                    message_sent_flag = typing_state.get("message_sent", False)
                
                if is_typing:
                    if show_indicator and not last_typing_indicator:
                        try:
                            client.send_message("/chatbox/typing", True)
                            last_typing_indicator = True
                            print("[VRChat] Typing indicator ON")
                        except Exception as e:
                            print(f"[VRChat] Failed to send typing indicator: {e}")
                    continue
                
                if typed_message and current_time_val < display_until:
                    if not message_sent_flag:
                        formatted_msg = format_typed_message(typed_message)
                        if chatbox_visible:
                            send_to_vrchat(formatted_msg)
                            print(f"[VRChat] Sent typed message: '{typed_message[:30]}...'")
                        
                        with typing_state_lock:
                            typing_state["message_sent"] = True
                        
                        if last_typing_indicator:
                            try:
                                client.send_message("/chatbox/typing", False)
                                last_typing_indicator = False
                                print("[VRChat] Typing indicator OFF")
                            except:
                                pass
                    continue
                
                if typed_message and current_time_val >= display_until:
                    with typing_state_lock:
                        typing_state["typed_message"] = ""
                        typing_state["display_until"] = 0
                        typing_state["message_sent"] = False
                    print("[VRChat] Typed message display ended, resuming rotation")
                
                if last_typing_indicator:
                    try:
                        client.send_message("/chatbox/typing", False)
                        last_typing_indicator = False
                    except:
                        pass
                
                next_osc_send -= 1
                next_custom_in = next_osc_send

                if next_osc_send <= 0:
                    if CUSTOM_TEXTS:
                        current_idx = str(text_cycle_index)
                        per_msg_interval = SETTINGS.get("per_message_intervals", {}).get(current_idx, osc_interval)
                        
                        current_custom_text = get_next_custom_message()
                        update_message_queue()
                        
                        rotation_log_counter += 1
                        if rotation_log_counter <= 5 or rotation_log_counter % 10 == 0:
                            print(f"[VRChat Updater] Message rotated to index {text_cycle_index}: '{current_custom_text[:30]}...' (show_custom={show_custom})")
                        
                        next_idx = str(text_cycle_index)
                        next_osc_send = SETTINGS.get("per_message_intervals", {}).get(next_idx, osc_interval)
                        
                        if not show_custom:
                            current_custom_text = ""
                    else:
                        current_custom_text = ""
                        osc_interval = max(1, int(SETTINGS.get("osc_send_interval", 3)))
                        next_osc_send = osc_interval

                    if _scroll_is_active():
                        if not chatbox_visible:
                            try:
                                client.send_message("/chatbox/visible", 0)
                            except:
                                pass
                    else:
                        preview_msg = get_current_preview(advance_page=True)

                        if chatbox_visible and not auto_send_paused and preview_msg:
                            send_to_vrchat(preview_msg)
                        elif chatbox_visible:
                            try:
                                client.send_message("/chatbox/visible", 1)
                            except:
                                pass
                        else:
                            try:
                                client.send_message("/chatbox/visible", 0)
                            except:
                                pass

            except Exception as e:
                log_error("VRC Updater error", e)
                print("[VRC Updater ERROR]", e)
                time.sleep(1)

    threading.Thread(target=updater, daemon=True).start()

def create_app():

    if getattr(sys, 'frozen', False):

        base_path = sys._MEIPASS
        template_folder = os.path.join(base_path, 'templates')
        static_folder = os.path.join(base_path, 'static')
    else:

        template_folder = "templates"
        static_folder = "static"
    
    app = Flask(__name__, template_folder=template_folder, static_folder=static_folder)
    vrchat_service.init()

    spotify.start_spotify_tracker(interval=SETTINGS.get("spotify_update_interval", 2))
    window_tracker.start_window_tracker(interval=SETTINGS.get("window_tracking_interval", 2))
    heart_rate_monitor.start_heart_rate_tracker(interval=SETTINGS.get("heart_rate_update_interval", 5))
    weather_service.start_weather_tracker(
        interval=SETTINGS.get("weather_update_interval", 600),
        location=SETTINGS.get("weather_location", "auto"),
        enabled=SETTINGS.get("weather_enabled", False)
    )
    vrchat_live.start_tracker(
        enabled=SETTINGS.get("vrchat_live_enabled", True),
        log_dir=SETTINGS.get("vrchat_live_log_dir", ""),
        interval=5.0 if IS_ANDROID else 0.5
    )

    vr_battery.start_tracker(
        enabled=SETTINGS.get("vr_battery_enabled", False),
        interval=SETTINGS.get("vr_battery_interval", 20)
    )

    volume_monitor.start_tracker(
        enabled=SETTINGS.get("volume_enabled", False),
        interval=SETTINGS.get("volume_interval", 10)
    )

    device_status.start_tracker(
        enabled=SETTINGS.get("device_status_enabled", False),
        interval=SETTINGS.get("device_status_interval", 60)
    )

    if SETTINGS.get("system_stats_enabled", False):
        system_stats.start_system_stats(
            update_interval=SETTINGS.get("system_stats_update_interval", 5),
            enable_gpu=SETTINGS.get("system_stats_show_gpu", False)
        )
    
    if SETTINGS.get("afk_enabled", False):
        afk_detector.set_afk_enabled(True)

    osc_reactions.set_reaction_callback(_trigger_temporary_message)
    osc_reactions.start_listener(port=SETTINGS.get("osc_reactions_port", 9001))

    global_hotkeys.set_send_callback(_trigger_temporary_message)
    global_hotkeys.configure(SETTINGS.get("global_hotkeys_enabled", False), SETTINGS.get("global_hotkeys", []))

    start_vrc_updater()
    start_scroll_ticker()

    if SETTINGS.get("steamvr_auto_launch_enabled", False):
        try:
            steamvr_launch.register(auto_launch=True)
        except Exception as e:
            log_error("Failed to refresh SteamVR auto-launch registration", e)

    @app.route("/")
    def index():
        COMMON_TIMEZONES = [
            "UTC", "US/Eastern", "US/Central", "US/Mountain", "US/Pacific",
            "Europe/London", "Europe/Paris", "Asia/Tokyo", "Asia/Shanghai",
            "Australia/Sydney"
        ]
        
        return render_template(
            "dashboard.html",
            platform="quest" if IS_ANDROID else "desktop",
            is_android=IS_ANDROID,
            spotify_source=spotify.get_spotify_state().get("source", "spotify_api"),
            quest_ip=SETTINGS.get("quest_ip",""),
            quest_port=SETTINGS.get("quest_port",9000),
            spotify_needs_restart=SETTINGS.get("spotify_needs_restart", False),
            customs_text="\n".join(SETTINGS.get("custom_texts", [])),
            osc_send_interval=SETTINGS.get("osc_send_interval", 3),
            dashboard_update_interval=SETTINGS.get("dashboard_update_interval", 3),
            music_progress=SETTINGS.get("music_progress", True),
            progress_style=SETTINGS.get("progress_style", "bar"),
            timezone=SETTINGS.get("timezone", "local"),
            timezones=COMMON_TIMEZONES,
            layout_order=SETTINGS.get("layout_order", ["time","custom","vrchat_live","song","window","heartrate","weather","system_stats","afk"]),
            per_message_intervals=SETTINGS.get("per_message_intervals", {}),
            theme=SETTINGS.get("theme", "dark"),
            random_order=SETTINGS.get("random_order", False),
            weighted_messages=SETTINGS.get("weighted_messages", {}),
            show_module_icons=SETTINGS.get("show_module_icons", True),
            streamer_mode=SETTINGS.get("streamer_mode", False),
            compact_mode=SETTINGS.get("compact_mode", False),
            window_tracking_enabled=SETTINGS.get("window_tracking_enabled", False),
            window_tracking_interval=SETTINGS.get("window_tracking_interval", 2),
            window_tracking_mode=SETTINGS.get("window_tracking_mode", "both"),
            heart_rate_enabled=SETTINGS.get("heart_rate_enabled", False),
            heart_rate_source=SETTINGS.get("heart_rate_source", "pulsoid"),
            heart_rate_pulsoid_token=SETTINGS.get("heart_rate_pulsoid_token", ""),
            heart_rate_hyperate_id=SETTINGS.get("heart_rate_hyperate_id", ""),
            heart_rate_custom_api=SETTINGS.get("heart_rate_custom_api", ""),
            heart_rate_update_interval=SETTINGS.get("heart_rate_update_interval", 5),
            heart_rate_osc_enabled=SETTINGS.get("heart_rate_osc_enabled", False),
            heart_rate_osc_min_bpm=SETTINGS.get("heart_rate_osc_min_bpm", 40),
            heart_rate_osc_max_bpm=SETTINGS.get("heart_rate_osc_max_bpm", 200),
            time_emoji=SETTINGS.get("time_emoji", "⏰"),
            custom_emoji=SETTINGS.get("custom_emoji", "💬"),
            song_emoji=SETTINGS.get("song_emoji", "🎶"),
            window_emoji=SETTINGS.get("window_emoji", "💻"),
            heartrate_emoji=SETTINGS.get("heartrate_emoji", "❤️"),
            weather_emoji=SETTINGS.get("weather_emoji", "🌤️"),
            system_stats_emoji=SETTINGS.get("system_stats_emoji", "📊"),
            afk_emoji=SETTINGS.get("afk_emoji", "💤"),
            system_stats_cpu_emoji=SETTINGS.get("system_stats_cpu_emoji", "🧠"),
            system_stats_ram_emoji=SETTINGS.get("system_stats_ram_emoji", "💾"),
            system_stats_gpu_emoji=SETTINGS.get("system_stats_gpu_emoji", "🎮"),
            system_stats_network_emoji=SETTINGS.get("system_stats_network_emoji", "📡"),
            custom_background=SETTINGS.get("custom_background", ""),
            custom_button_color=SETTINGS.get("custom_button_color", ""),
            slim_chatbox=SETTINGS.get("slim_chatbox", False),
            window_prefix=SETTINGS.get("window_prefix", ""),
            weather_location=SETTINGS.get("weather_location", "auto"),
            weather_temp_unit=SETTINGS.get("weather_temp_unit", "F"),
            typed_message_duration=SETTINGS.get("typed_message_duration", 5),
            typing_indicator_enabled=SETTINGS.get("typing_indicator_enabled", True),
            system_stats_enabled=SETTINGS.get("system_stats_enabled", False),
            show_cpu=SETTINGS.get("system_stats_show_cpu", True),
            show_ram=SETTINGS.get("system_stats_show_ram", True),
            show_gpu=SETTINGS.get("system_stats_show_gpu", False),
            show_network=SETTINGS.get("system_stats_show_network", False),
            system_stats_update_interval=SETTINGS.get("system_stats_update_interval", 5),
            system_stats_separator=SETTINGS.get("system_stats_separator", " | "),
            system_stats_show_labels=SETTINGS.get("system_stats_show_labels", True),
            system_stats_decimals=SETTINGS.get("system_stats_decimals", 0),
            system_stats_show_ram_details=SETTINGS.get("system_stats_show_ram_details", False),
            system_stats_network_units=SETTINGS.get("system_stats_network_units", "bits"),
            system_stats_template=SETTINGS.get("system_stats_template", DEFAULT_SYSTEM_STATS_TEMPLATE),
            system_stats_template_variables=SYSTEM_STATS_TEMPLATE_VARIABLES,
            soundpad_enabled=SETTINGS.get("soundpad_enabled", True),
            soundpad_volume=SETTINGS.get("soundpad_volume", 85),
            soundpad_announce=SETTINGS.get("soundpad_announce", False),
            soundpad_caption_template=SETTINGS.get("soundpad_caption_template", "Playing: {name}"),
            tts_enabled=SETTINGS.get("tts_enabled", True),
            tts_rate=SETTINGS.get("tts_rate", 1.0),
            tts_pitch=SETTINGS.get("tts_pitch", 1.0),
            tts_volume=SETTINGS.get("tts_volume", 1.0),
            afk_enabled=SETTINGS.get("afk_enabled", False),
            afk_timeout=SETTINGS.get("afk_timeout", 300),
            afk_message=SETTINGS.get("afk_message", ""),
            afk_show_duration=SETTINGS.get("afk_show_duration", True),
            hr_show_trend=SETTINGS.get("hr_show_trend", True),
            hr_show_stats=SETTINGS.get("hr_show_stats", False),
            chatbox_template_settings=_get_chatbox_template_settings(),
            quick_phrases=quick_phrases.get_phrases()
        )

    @app.route("/status")
    def status():
        global current_time_text, current_custom_text, last_message_sent
        global show_time, show_custom, show_music, show_window, show_heartrate, auto_send_paused
        global connection_status, last_successful_send, message_queue

        time_text = current_time_text if show_time else "OFF"
        custom_text = current_custom_text if show_custom else "OFF"
        
        wstate = window_tracker.get_window_state()
        window_text = wstate.get("app_name", "No window detected") if show_window else "OFF"
        
        hrstate = heart_rate_monitor.get_heart_rate_state()
        heartrate_text = "Not connected"
        hr_trend = ""
        if show_heartrate:
            if hrstate.get("is_connected") and hrstate.get("bpm", 0) > 0:
                bpm = hrstate['bpm']
                hr_stats = heart_rate_monitor.get_hr_stats()
                trend = hr_stats.get("trend", "stable")
                if trend == "rising":
                    hr_trend = " 📈"
                elif trend == "falling":
                    hr_trend = " 📉"
                heartrate_text = f"{bpm} BPM{hr_trend}"
            elif heart_rate_monitor.is_simulator_enabled():
                heartrate_text = "Simulator running..."
            else:
                heartrate_text = "Waiting for data..."

        sstate = spotify.get_spotify_state()
        song_text = "No song playing"
        progress_percent = 0
        album_art = ""

        song_has_duration = False
        if show_music and sstate.get("song_text"):
            try:
                pos = int(sstate.get("song_pos", 0))
                dur = int(sstate.get("song_dur", 0))
                if dur > 0:
                    elapsed_min, elapsed_sec = divmod(pos, 60)
                    total_min, total_sec = divmod(dur, 60)
                    song_text = f"{sstate['song_text']} [{elapsed_min}:{elapsed_sec:02d} / {total_min}:{total_sec:02d}]"
                    progress_percent = int((pos / dur) * 100)
                    song_has_duration = True
                else:
                    song_text = sstate["song_text"]
                album_art = sstate.get("album_art", "")
            except Exception:
                song_text = sstate.get("song_text", "No song playing")
                progress_percent = 0

        progress_str = ""
        if SETTINGS.get("music_progress", True) and show_music and sstate.get("song_text") and song_has_duration:
            style = SETTINGS.get("progress_style", "bar")
            if style == "bar":
                filled = int(progress_percent / 10)
                empty = 10 - filled
                progress_str = "█" * filled + "░" * empty
            elif style == "dots":
                filled = int(progress_percent / 10)
                empty = 10 - filled
                progress_str = "●" * filled + "○" * empty
            elif style == "percentage":
                progress_str = f"{progress_percent}%"

        live_state = _get_vrchat_live_state()
        preview_msg = get_current_preview()

        weather_text = "OFF"
        try:
            weath_state = weather_service.get_weather_state()
            if show_weather:
                if weath_state.get("temperature"):
                    weather_text = f"{weath_state.get('temperature')} - {weath_state.get('condition', 'N/A')}"
                else:
                    weather_text = "Loading..."
        except Exception as e:
            log_error("Failed to get weather state", e)
            weather_text = "Error"
        
        last_send_str = "Never"
        try:
            if last_successful_send:
                if isinstance(last_successful_send, datetime):
                    last_send_str = last_successful_send.strftime("%I:%M:%S %p")
                else:
                    last_send_str = str(last_successful_send)
        except Exception as e:
            log_error("Failed to format last_successful_send", e)
            last_send_str = "Error"
        
        return jsonify({
            "chatbox": chatbox_visible,
            "auto_send_paused": auto_send_paused,
            "time": time_text,
            "time_on": show_time,
            "custom": custom_text,
            "custom_on": show_custom,
            "song": song_text,
            "music_on": show_music,
            "music_progress": SETTINGS.get("music_progress", True),
            "progress_style": SETTINGS.get("progress_style", "bar"),
            "progress_percent": progress_percent,
            "progress_string": progress_str,
            "last_message": last_message_sent,
            "preview": preview_msg,
            "album_art": album_art,
            "next_custom": next_custom_in,
            "connection_status": connection_status,
            "last_successful_send": last_send_str,
            "message_queue": message_queue,
            "theme": SETTINGS.get("theme", "dark"),
            "streamer_mode": SETTINGS.get("streamer_mode", False),
            "compact_mode": SETTINGS.get("compact_mode", False),
            "custom_texts": SETTINGS.get("custom_texts", []),
            "per_message_intervals": SETTINGS.get("per_message_intervals", {}),
            "weighted_messages": SETTINGS.get("weighted_messages", {}),
            "random_order": SETTINGS.get("random_order", False),
            "show_module_icons": SETTINGS.get("show_module_icons", True),
            "window": window_text,
            "window_on": show_window,
            "window_tracking_enabled": SETTINGS.get("window_tracking_enabled", False),
            "heartrate": heartrate_text,
            "heartrate_on": show_heartrate,
            "heart_rate_enabled": SETTINGS.get("heart_rate_enabled", False),
            "heart_rate_osc_enabled": SETTINGS.get("heart_rate_osc_enabled", False),
            "weather": weather_text,
            "weather_on": show_weather,
            "weather_enabled": SETTINGS.get("weather_enabled", False),
            "vrchat_live": live_state,
            "vrchat_live_on": SETTINGS.get("show_vrchat_live", False),
            "text_effect": SETTINGS.get("text_effect", "none"),
            "slim_chatbox": SETTINGS.get("slim_chatbox", False),
            "chatbox_template_settings": _get_chatbox_template_settings(),
            "system_stats_enabled": SETTINGS.get("system_stats_enabled", False),
            "system_stats": system_stats.get_system_stats() if SETTINGS.get("system_stats_enabled", False) else {},
            "system_stats_text": _format_system_stats_line(include_main_icon=False) if SETTINGS.get("system_stats_enabled", False) else "",
            "afk_enabled": SETTINGS.get("afk_enabled", False),
            "is_afk": (afk_detector.check_afk(SETTINGS.get("afk_timeout", 300)) or afk_detector.is_afk()) if SETTINGS.get("afk_enabled", False) else False,
            "afk_message": afk_detector.get_afk_message(SETTINGS.get("afk_message", ""), SETTINGS.get("afk_show_duration", True)) if SETTINGS.get("afk_enabled", False) else "",
            "afk_countdown": afk_detector.get_time_until_afk(SETTINGS.get("afk_timeout", 300)) if SETTINGS.get("afk_enabled", False) else -1,
            "afk_countdown_formatted": afk_detector.format_countdown(afk_detector.get_time_until_afk(SETTINGS.get("afk_timeout", 300))) if SETTINGS.get("afk_enabled", False) else "",
            "hr_stats": heart_rate_monitor.get_hr_stats() if show_heartrate else {},
            "hr_simulator_enabled": heart_rate_monitor.is_simulator_enabled(),
            "hr_trend": hr_trend,
            "soundpad": {
                "enabled": SETTINGS.get("soundpad_enabled", True),
                "volume": SETTINGS.get("soundpad_volume", 85),
                "announce": SETTINGS.get("soundpad_announce", False),
                "caption_template": SETTINGS.get("soundpad_caption_template", "Playing: {name}"),
            },
            "tts": {
                "enabled": SETTINGS.get("tts_enabled", True),
                "rate": SETTINGS.get("tts_rate", 1.0),
                "pitch": SETTINGS.get("tts_pitch", 1.0),
                "volume": SETTINGS.get("tts_volume", 1.0),
            },
        })

    @app.route("/app/state", methods=["GET"])
    def app_state():
        try:
            status_payload = status().get_json() or {}
        except Exception as exc:
            log_error("Failed to build status payload", exc)
            status_payload = {}
        presets = app_services.list_presets()
        active_preset = next((p for p in presets if p["id"] == SETTINGS.get("active_preset_id")), presets[0] if presets else None)
        integrations = {
            "osc": {
                "enabled": True,
                "status": connection_status,
                "target": f"{SETTINGS.get('quest_ip') or '127.0.0.1'}:{SETTINGS.get('quest_port', 9000)}",
                "help": "VRChat must have OSC enabled. Quest VRChat uses 127.0.0.1:9000 when Crystal runs on the headset." if IS_ANDROID else "VRChat must have OSC enabled. Desktop VRChat usually uses 127.0.0.1:9000.",
            },
            "spotify": spotify.get_spotify_state(),
            "weather": weather_service.get_weather_state(),
            "heart_rate": heart_rate_monitor.get_heart_rate_state(),
            "window": window_tracker.get_window_state(),
            "system_stats": system_stats.get_system_stats(),
            "vrchat_account": vrchat_service.status(),
            "vrchat_live": _get_vrchat_live_state(),
            "vr_battery": vr_battery.get_state(),
            "steamvr_launch": _get_steamvr_launch_state(),
            "volume": volume_monitor.get_state(),
            "device_status": device_status.get_state(),
            "osc_reactions": osc_reactions.get_status(),
            "global_hotkeys": global_hotkeys.get_status(),
        }
        warnings = []
        if not SETTINGS.get("setup_completed", False):
            warnings.append({
                "severity": "info",
                "message": "Setup has not been completed yet.",
                "action": "Open the setup guide and send a test message.",
            })
        if connection_status != "connected":
            warnings.append({
                "severity": "warning",
                "message": "OSC has not confirmed a successful send in this session.",
                "action": "Use Test OSC after enabling OSC in VRChat.",
            })
        spotify_state = integrations.get("spotify", {})
        if spotify_state.get("last_error"):
            warnings.append({
                "severity": "warning",
                "message": "Spotify is unavailable right now.",
                "action": spotify_state.get("last_error"),
            })
        return jsonify({
            "ok": True,
            "platform": "quest" if IS_ANDROID else "desktop",
            "settings": public_settings(),
            "runtime": status_payload,
            "presets": presets,
            "active_preset": active_preset,
            "profiles": app_services.list_profiles_full(),
            "automations": app_services.automation_summary(),
            "integrations": integrations,
            "logs": app_services.read_events(8),
            "message_history": message_history.get_recent_messages(12),
            "typed_history": message_history.get_typed_history(12),
            "quick_phrases": quick_phrases.get_phrases(),
            "warnings": warnings,
            "search_index": app_services.SEARCH_INDEX,
            "insights": {
                **session_insights.get_insights(),
                "message_stats": message_history.get_message_stats(),
            },
        })

    @app.route("/app/settings", methods=["GET", "POST"])
    def app_settings():
        if request.method == "GET":
            return jsonify({"ok": True, "settings": public_settings()}), 200
        data = request.get_json(force=True) if request.is_json else {}
        if not isinstance(data, dict):
            return _json_error("Settings update must be a JSON object.")
        patch = data.get("settings", data)
        if not isinstance(patch, dict):
            return _json_error("Settings payload must be an object.")
        if (
            patch.get("chatbox_frame", "none") != "none"
            and SETTINGS.get("chatbox_frame", "none") == "none"
            and "chatbox_overflow_mode" not in patch
            and SETTINGS.get("chatbox_overflow_mode", "smart") == "smart"
        ):
            patch["chatbox_overflow_mode"] = "scroll"
        errors = {}
        if "quest_ip" in patch or "quest_port" in patch:
            ip, port, osc_errors = app_services.validate_osc(
                patch.get("quest_ip", SETTINGS.get("quest_ip")),
                patch.get("quest_port", SETTINGS.get("quest_port")),
            )
            errors.update(osc_errors)
            patch["quest_ip"] = ip
            patch["quest_port"] = port
        if "osc_send_interval" in patch:
            try:
                patch["osc_send_interval"] = max(1, min(int(patch["osc_send_interval"]), 3600))
            except Exception:
                errors["osc_send_interval"] = "Update interval must be a whole number."
        if "typed_message_duration" in patch:
            try:
                patch["typed_message_duration"] = max(1, min(int(patch["typed_message_duration"]), 60))
            except Exception:
                errors["typed_message_duration"] = "Message duration must be 1 to 60 seconds."
        if errors:
            return _json_error("Some settings need attention.", 400, errors)
        SETTINGS.update(patch)
        _persist_settings(backup=bool(data.get("backup", False)), label="app_settings")
        _sync_runtime_from_settings()
        app_services.add_event("info", "settings", "Settings saved", {"keys": sorted(patch.keys())})
        return jsonify({"ok": True, "settings": public_settings()}), 200

    @app.route("/app/setup", methods=["POST"])
    def app_setup():
        data = request.get_json(force=True) if request.is_json else {}
        ip, port, errors = app_services.validate_osc(data.get("quest_ip", SETTINGS.get("quest_ip")), data.get("quest_port", SETTINGS.get("quest_port")))
        message = str(data.get("message") or "").strip()
        profile = str(data.get("profile") or "Default").strip()[:80] or "Default"
        if not message:
            errors["message"] = "Enter a first chatbox message."
        if errors:
            return _json_error("Setup needs a few fixes.", 400, errors)
        SETTINGS.update({
            "quest_ip": ip,
            "quest_port": port,
            "custom_texts": [message],
            "chatbox_visible": bool(data.get("chatbox_visible", True)),
            "active_profile": profile,
            "setup_completed": True,
        })
        _persist_settings(backup=True, label="setup")
        _sync_runtime_from_settings()
        try:
            app_services.save_profile(profile, "Created during setup")
        except Exception as exc:
            log_error("Failed to save setup profile", exc)
        app_services.add_event("info", "setup", "Setup completed")
        return jsonify({"ok": True, "settings": public_settings()}), 200

    @app.route("/app/chatbox/preview", methods=["POST"])
    def app_chatbox_preview():
        data = request.get_json(force=True) if request.is_json else {}
        template = str(data.get("message") or data.get("template") or "")
        rendered = replace_variables(template)
        formatted = format_typed_message(rendered)
        limit = VRCHAT_CHAR_LIMIT - (SLIM_SUFFIX_LENGTH if SETTINGS.get("slim_chatbox", False) else 0)
        overflow = len(formatted) > VRCHAT_CHAR_LIMIT
        return jsonify({
            "ok": True,
            "template": template,
            "resolved": rendered,
            "formatted": formatted,
            "limit": limit,
            "length": len(formatted),
            "will_truncate": overflow,
            "final": smart_truncate_message(formatted) if overflow else formatted,
        })

    @app.route("/app/chatbox/clear", methods=["POST"])
    def app_chatbox_clear():
        global last_message_sent, connection_status
        try:
            client.send_message("/chatbox/input", ["", True])
            last_message_sent = ""
            app_services.add_event("info", "osc", "Chatbox cleared")
            return jsonify({"ok": True}), 200
        except Exception as exc:
            connection_status = "disconnected"
            log_error("Failed to clear chatbox", exc)
            return _json_error("Could not clear VRChat chatbox. Check OSC and try again.", 500)

    @app.route("/app/presets", methods=["GET", "POST"])
    def app_presets():
        if request.method == "GET":
            return jsonify({"ok": True, "presets": app_services.list_presets(), "active": SETTINGS.get("active_preset_id")})
        data = request.get_json(force=True) if request.is_json else {}
        try:
            preset = app_services.upsert_preset(data)
            return jsonify({"ok": True, "preset": preset, "presets": app_services.list_presets()}), 200
        except Exception as exc:
            return _json_error(str(exc), 400)

    @app.route("/app/presets/<preset_id>", methods=["PUT", "DELETE"])
    def app_preset_detail(preset_id):
        if request.method == "DELETE":
            if app_services.delete_preset(preset_id):
                return jsonify({"ok": True, "presets": app_services.list_presets()})
            return _json_error("Preset not found.", 404)
        data = request.get_json(force=True) if request.is_json else {}
        data["id"] = preset_id
        preset = app_services.upsert_preset(data)
        return jsonify({"ok": True, "preset": preset, "presets": app_services.list_presets()})

    @app.route("/app/presets/<preset_id>/duplicate", methods=["POST"])
    def app_preset_duplicate(preset_id):
        preset = app_services.duplicate_preset(preset_id)
        if not preset:
            return _json_error("Preset not found.", 404)
        return jsonify({"ok": True, "preset": preset, "presets": app_services.list_presets()})

    @app.route("/app/presets/<preset_id>/apply", methods=["POST"])
    def app_preset_apply(preset_id):
        preset = app_services.apply_preset(preset_id)
        if not preset:
            return _json_error("Preset not found.", 404)
        _sync_runtime_from_settings()
        return jsonify({"ok": True, "preset": preset, "settings": public_settings()})

    @app.route("/app/world-presets", methods=["GET", "POST"])
    def app_world_presets():
        if request.method == "GET":
            return jsonify({
                "ok": True,
                "enabled": SETTINGS.get("world_preset_auto_switch_enabled", False),
                "rules": SETTINGS.get("world_preset_rules", []),
            })
        data = request.get_json(force=True) if request.is_json else {}
        preset_ids = {p["id"] for p in app_services.list_presets()}
        rules = []
        for raw in data.get("rules", []):
            if not isinstance(raw, dict):
                continue
            world_id = str(raw.get("world_id", "")).strip()[:80]
            preset_id = str(raw.get("preset_id", "")).strip()
            if not world_id or not world_id.startswith("wrld_") or preset_id not in preset_ids:
                continue
            rules.append({
                "world_id": world_id,
                "world_name": str(raw.get("world_name", "")).strip()[:120],
                "preset_id": preset_id,
            })
        SETTINGS["world_preset_rules"] = rules
        SETTINGS["world_preset_auto_switch_enabled"] = bool(data.get("enabled", SETTINGS.get("world_preset_auto_switch_enabled", False)))
        _persist_settings(label="world_presets")

        _check_world_preset_switch(_get_vrchat_live_state(), force=True)
        return jsonify({
            "ok": True,
            "enabled": SETTINGS.get("world_preset_auto_switch_enabled", False),
            "rules": SETTINGS.get("world_preset_rules", []),
        })

    @app.route("/app/profiles", methods=["GET", "POST"])
    def app_profiles():
        if request.method == "GET":
            return jsonify({"ok": True, "profiles": app_services.list_profiles_full(), "active": SETTINGS.get("active_profile")})
        data = request.get_json(force=True) if request.is_json else {}
        try:
            profile = app_services.save_profile(data.get("name"), data.get("description", ""))
            return jsonify({"ok": True, "profile": profile, "profiles": app_services.list_profiles_full()})
        except Exception as exc:
            return _json_error(str(exc), 400)

    @app.route("/app/profiles/<profile_name>/apply", methods=["POST"])
    def app_profile_apply(profile_name):
        profile = app_services.apply_profile(profile_name)
        if not profile:
            return _json_error("Profile not found.", 404)
        _sync_runtime_from_settings()
        return jsonify({"ok": True, "profile": profile, "settings": public_settings()})

    @app.route("/app/profiles/<profile_name>", methods=["DELETE"])
    def app_profile_delete(profile_name):
        if app_services.delete_profile(profile_name):
            return jsonify({"ok": True, "profiles": app_services.list_profiles_full()})
        return _json_error("Profile not found or protected.", 404)

    @app.route("/app/automations", methods=["GET", "POST"])
    def app_automations():
        if request.method == "GET":
            return jsonify({"ok": True, "automations": app_services.list_automations()})
        data = request.get_json(force=True) if request.is_json else {}
        rule = app_services.upsert_automation(data)
        return jsonify({"ok": True, "automation": rule, "automations": app_services.list_automations()})

    @app.route("/app/automations/<rule_id>", methods=["PUT", "DELETE"])
    def app_automation_detail(rule_id):
        if request.method == "DELETE":
            if app_services.delete_automation(rule_id):
                return jsonify({"ok": True, "automations": app_services.list_automations()})
            return _json_error("Automation not found.", 404)
        data = request.get_json(force=True) if request.is_json else {}
        data["id"] = rule_id
        rule = app_services.upsert_automation(data)
        return jsonify({"ok": True, "automation": rule, "automations": app_services.list_automations()})

    @app.route("/app/logs", methods=["GET"])
    def app_logs():
        severities = request.args.get("severity", "")
        components = request.args.get("component", "")
        return jsonify({
            "ok": True,
            "logs": app_services.read_events(
                limit=int(request.args.get("limit", 300) or 300),
                severities=[s.strip() for s in severities.split(",") if s.strip()],
                components=[s.strip() for s in components.split(",") if s.strip()],
            )
        })

    @app.route("/app/logs/clear", methods=["POST"])
    def app_logs_clear():
        app_services.clear_events()
        return jsonify({"ok": True, "logs": app_services.read_events(50)})

    @app.route("/app/search", methods=["GET"])
    def app_search():
        return jsonify({"ok": True, "results": app_services.search(request.args.get("q", ""))})

    @app.route("/app/export", methods=["POST"])
    def app_export():
        data = request.get_json(silent=True) or {}
        path = app_services.export_bundle(redacted=bool(data.get("redacted", True)))
        return send_file(path, as_attachment=True, download_name=os.path.basename(path))

    @app.route("/app/import", methods=["POST"])
    def app_import():
        try:
            if request.files:
                file = next(iter(request.files.values()))
                payload = json.loads(file.read().decode("utf-8"))
            else:
                payload = request.get_json(force=True)
            app_services.import_bundle(payload)
            _sync_runtime_from_settings()
            return jsonify({"ok": True, "settings": public_settings()}), 200
        except Exception as exc:
            return _json_error(f"Import failed: {exc}", 400)

    @app.route("/app/diagnostics", methods=["POST"])
    def app_diagnostics():
        try:
            status_response = status()
            status_payload = status_response.get_json() if hasattr(status_response, "get_json") else {}
        except Exception as exc:
            log_error("Failed to include status in diagnostics", exc)
            status_payload = {"error": str(exc)}
        path = app_services.create_diagnostics_report({
            "status": status_payload,
        })
        return send_file(path, as_attachment=True, download_name=os.path.basename(path))

    @app.route("/vrcx-plus/state", methods=["GET"])
    def vrcx_plus_state():
        vrchat_status = vrchat_service.status()
        provider = _vrcx_plus_provider_settings()
        return jsonify(
            {
                "ok": True,
                "vrchat": vrchat_status,
                "provider": provider
            }
        )

    @app.route("/vrchat-live/state", methods=["GET"])
    def vrchat_live_state():
        return jsonify({"ok": True, "state": _get_vrchat_live_state()}), 200

    @app.route("/vrchat-live/refresh", methods=["POST"])
    def vrchat_live_refresh():
        vrchat_live.refresh_now()
        return jsonify({"ok": True, "state": _get_vrchat_live_state(force_refresh=True)}), 200

    @app.route("/vrchat-live/toggle", methods=["POST"])
    def vrchat_live_toggle():
        enabled = not bool(SETTINGS.get("vrchat_live_enabled", True))
        SETTINGS["vrchat_live_enabled"] = enabled
        _persist_settings(label="vrchat_live")
        vrchat_live.configure(enabled=enabled, log_dir=SETTINGS.get("vrchat_live_log_dir", ""))
        if enabled:
            vrchat_live.refresh_now()
        app_services.add_event("info", "vrchat-live", "VRChat live monitor enabled" if enabled else "VRChat live monitor disabled")
        return jsonify({"ok": True, "enabled": enabled, "state": _get_vrchat_live_state(force_refresh=True)}), 200

    @app.route("/vrchat-live/manual-location", methods=["POST"])
    def vrchat_live_manual_location():
        body = request.get_json(silent=True) or {}
        raw = str(body.get("location", "")).strip()
        location, world_id, instance_id = _extract_vrchat_location(raw)
        if raw and not world_id:
            return _json_error("Use a VRChat world link or a location like wrld_xxx:instance.", 400)
        SETTINGS["vrchat_live_manual_location"] = location
        _persist_settings(label="vrchat_live_manual_location")
        if world_id:
            vrchat_live.apply_account_location(location=location, world_id=world_id, instance_id=instance_id, source="Manual location")
        else:
            vrchat_live.clear_location()
            vrchat_live.refresh_now()
        return jsonify({"ok": True, "location": location, "state": _get_vrchat_live_state(force_refresh=True)}), 200

    @app.route("/vr-battery/state", methods=["GET"])
    def vr_battery_state():
        return jsonify({"ok": True, "state": vr_battery.get_state()}), 200

    @app.route("/vr-battery/refresh", methods=["POST"])
    def vr_battery_refresh():
        return jsonify({"ok": True, "state": vr_battery.refresh_now()}), 200

    @app.route("/vr-battery/toggle", methods=["POST"])
    def vr_battery_toggle():
        enabled = not bool(SETTINGS.get("vr_battery_enabled", False))
        SETTINGS["vr_battery_enabled"] = enabled
        SETTINGS["show_vr_battery"] = enabled
        _persist_settings(label="vr_battery")
        vr_battery.configure(enabled=enabled, interval=SETTINGS.get("vr_battery_interval", 20))
        if enabled:
            vr_battery.start_tracker(enabled=True, interval=SETTINGS.get("vr_battery_interval", 20))
            vr_battery.refresh_now()
        app_services.add_event("info", "vr-battery", "VR battery monitor enabled" if enabled else "VR battery monitor disabled")
        return jsonify({"ok": True, "enabled": enabled, "state": vr_battery.get_state()}), 200

    @app.route("/vr-battery/settings", methods=["POST"])
    def vr_battery_settings():
        body = request.get_json(silent=True) or {}
        SETTINGS["vr_battery_include_controllers"] = bool(
            body.get("include_controllers", SETTINGS.get("vr_battery_include_controllers", True))
        )
        SETTINGS["vr_battery_include_trackers"] = bool(
            body.get("include_trackers", SETTINGS.get("vr_battery_include_trackers", False))
        )
        try:
            SETTINGS["vr_battery_low_threshold"] = max(0, min(int(body.get("low_threshold", SETTINGS.get("vr_battery_low_threshold", 20))), 100))
        except Exception:
            pass
        try:
            interval = max(5, min(int(body.get("interval", SETTINGS.get("vr_battery_interval", 20))), 120))
            SETTINGS["vr_battery_interval"] = interval
            vr_battery.configure(enabled=SETTINGS.get("vr_battery_enabled", False), interval=interval)
        except Exception:
            pass
        _persist_settings(label="vr_battery_settings")
        return jsonify({"ok": True, "state": vr_battery.get_state()}), 200

    @app.route("/steamvr/state", methods=["GET"])
    def steamvr_state():
        return jsonify({"ok": True, **_get_steamvr_launch_state()}), 200

    @app.route("/steamvr/toggle-auto-launch", methods=["POST"])
    def steamvr_toggle_auto_launch():
        enabled = not bool(SETTINGS.get("steamvr_auto_launch_enabled", False))
        error = ""
        try:
            if enabled:
                steamvr_launch.register(auto_launch=True)
            else:
                steamvr_launch.unregister()
        except Exception as e:
            reason = str(e).strip() or type(e).__name__
            if "InterfaceNotFound" in reason or "105" in reason:
                error = "SteamVR isn't running (or needs updating). Open SteamVR fully first, then try again."
            else:
                error = f"Could not reach SteamVR ({reason}). Start SteamVR once, then try again."
            enabled = SETTINGS.get("steamvr_auto_launch_enabled", False)
        else:
            SETTINGS["steamvr_auto_launch_enabled"] = enabled
            _persist_settings(label="steamvr_auto_launch")
        registered = steamvr_launch.is_registered() if steamvr_launch.is_supported() else False
        return jsonify({"ok": not error, "enabled": enabled, "error": error, "registered": registered}), (200 if not error else 400)

    @app.route("/volume/state", methods=["GET"])
    def volume_state():
        return jsonify({"ok": True, "state": volume_monitor.get_state()}), 200

    @app.route("/volume/refresh", methods=["POST"])
    def volume_refresh():
        return jsonify({"ok": True, "state": volume_monitor.refresh_now()}), 200

    @app.route("/volume/toggle", methods=["POST"])
    def volume_toggle():
        enabled = not bool(SETTINGS.get("volume_enabled", False))
        SETTINGS["volume_enabled"] = enabled
        SETTINGS["show_volume"] = enabled
        _persist_settings(label="volume")
        volume_monitor.configure(enabled=enabled, interval=SETTINGS.get("volume_interval", 10))
        if enabled:
            volume_monitor.start_tracker(enabled=True, interval=SETTINGS.get("volume_interval", 10))
            volume_monitor.refresh_now()
        app_services.add_event("info", "volume", "Volume monitor enabled" if enabled else "Volume monitor disabled")
        return jsonify({"ok": True, "enabled": enabled, "state": volume_monitor.get_state()}), 200

    @app.route("/volume/settings", methods=["POST"])
    def volume_settings():
        body = request.get_json(silent=True) or {}
        if "emoji" in body:
            SETTINGS["volume_emoji"] = str(body.get("emoji") or "")[:8]
        try:
            interval = max(2, min(int(body.get("interval", SETTINGS.get("volume_interval", 10))), 60))
            SETTINGS["volume_interval"] = interval
            volume_monitor.configure(enabled=SETTINGS.get("volume_enabled", False), interval=interval)
        except Exception:
            pass
        _persist_settings(label="volume_settings")
        return jsonify({"ok": True, "state": volume_monitor.get_state()}), 200

    @app.route("/device-status/state", methods=["GET"])
    def device_status_state():
        return jsonify({"ok": True, "state": device_status.get_state()}), 200

    @app.route("/device-status/refresh", methods=["POST"])
    def device_status_refresh():
        return jsonify({"ok": True, "state": device_status.refresh_now()}), 200

    @app.route("/device-status/toggle", methods=["POST"])
    def device_status_toggle():
        enabled = not bool(SETTINGS.get("device_status_enabled", False))
        SETTINGS["device_status_enabled"] = enabled
        SETTINGS["show_device_storage"] = enabled
        _persist_settings(label="device_status")
        device_status.configure(enabled=enabled, interval=SETTINGS.get("device_status_interval", 60))
        if enabled:
            device_status.start_tracker(enabled=True, interval=SETTINGS.get("device_status_interval", 60))
            device_status.refresh_now()
        app_services.add_event("info", "device-status", "Device status monitor enabled" if enabled else "Device status monitor disabled")
        return jsonify({"ok": True, "enabled": enabled, "state": device_status.get_state()}), 200

    @app.route("/device-status/settings", methods=["POST"])
    def device_status_settings():
        body = request.get_json(silent=True) or {}
        try:
            interval = max(15, min(int(body.get("interval", SETTINGS.get("device_status_interval", 60))), 600))
            SETTINGS["device_status_interval"] = interval
            device_status.configure(enabled=SETTINGS.get("device_status_enabled", False), interval=interval)
        except Exception:
            pass
        _persist_settings(label="device_status_settings")
        return jsonify({"ok": True, "state": device_status.get_state()}), 200

    @app.route("/osc-reactions/status", methods=["GET"])
    def osc_reactions_status():
        return jsonify({"ok": True, "status": osc_reactions.get_status()})

    @app.route("/osc-reactions/rules", methods=["POST"])
    def save_reaction_rules():
        body = request.get_json(force=True) if request.is_json else {}
        rules = []
        for raw in body.get("rules", []):
            if not isinstance(raw, dict):
                continue
            address = str(raw.get("address", "")).strip()
            message = str(raw.get("message", "")).strip()[:300]
            if not address.startswith("/avatar/parameters/") or not message:
                continue
            try:
                cooldown = max(1, min(int(raw.get("cooldown_seconds", 10)), 300))
            except (TypeError, ValueError):
                cooldown = 10
            rules.append({
                "id": str(raw.get("id") or f"reaction_{len(rules)}_{int(time.time())}"),
                "enabled": bool(raw.get("enabled", True)),
                "name": str(raw.get("name", "")).strip()[:80] or address,
                "address": address,
                "trigger_value": str(raw.get("trigger_value", "")).strip()[:20],
                "message": message,
                "cooldown_seconds": cooldown,
            })
        SETTINGS["reaction_rules"] = rules
        if "display_seconds" in body:
            try:
                SETTINGS["reaction_display_seconds"] = max(2, min(int(body["display_seconds"]), 60))
            except (TypeError, ValueError):
                pass
        _persist_settings(label="reaction_rules")
        return jsonify({"ok": True, "rules": SETTINGS["reaction_rules"]}), 200

    @app.route("/osc-reactions/avatar-change", methods=["POST"])
    def save_avatar_change_settings():
        body = request.get_json(force=True) if request.is_json else {}
        SETTINGS["avatar_change_announce_enabled"] = bool(body.get("enabled", False))
        SETTINGS["avatar_change_message"] = str(body.get("message", "")).strip()[:200] or "Just switched avatars! ✨"
        _persist_settings(label="avatar_change_settings")
        return jsonify({"ok": True}), 200

    @app.route("/osc-reactions/mute-indicator", methods=["POST"])
    def save_mute_indicator_settings():
        body = request.get_json(force=True) if request.is_json else {}
        SETTINGS["mute_indicator_enabled"] = bool(body.get("enabled", False))
        SETTINGS["mute_indicator_text"] = str(body.get("text", "")).strip()[:60] or "🔇 Muted"
        _persist_settings(label="mute_indicator_settings")
        return jsonify({"ok": True}), 200

    @app.route("/global-hotkeys/status", methods=["GET"])
    def global_hotkeys_status():
        return jsonify({"ok": True, "status": global_hotkeys.get_status()})

    @app.route("/global-hotkeys", methods=["POST"])
    def save_global_hotkeys():
        body = request.get_json(force=True) if request.is_json else {}
        hotkeys = []
        for raw in body.get("hotkeys", []):
            if not isinstance(raw, dict):
                continue
            combo = str(raw.get("combo", "")).strip().lower()[:60]
            phrase = str(raw.get("phrase", "")).strip()[:300]
            if not combo or not phrase:
                continue
            hotkeys.append({"combo": combo, "phrase": phrase})
        SETTINGS["global_hotkeys"] = hotkeys
        SETTINGS["global_hotkeys_enabled"] = bool(body.get("enabled", SETTINGS.get("global_hotkeys_enabled", False)))
        _persist_settings(label="global_hotkeys")
        global_hotkeys.configure(SETTINGS["global_hotkeys_enabled"], SETTINGS["global_hotkeys"])
        return jsonify({"ok": True, "hotkeys": SETTINGS["global_hotkeys"], "status": global_hotkeys.get_status()}), 200

    @app.route("/vrcx-plus/vrchat/status", methods=["GET"])
    def vrcx_plus_vrchat_status():
        return jsonify(vrchat_service.status())

    @app.route("/vrcx-plus/vrchat/login", methods=["POST"])
    def vrcx_plus_vrchat_login():
        body = request.get_json() or {}
        username = str(body.get("username", "")).strip()
        password = str(body.get("password", "")).strip()
        if not username or not password:
            return jsonify({"ok": False, "error": "Username and password required"}), 400
        payload = vrchat_service.login(username, password)
        if not payload.get("ok"):
            return jsonify(payload), 400
        return jsonify(payload)

    @app.route("/vrcx-plus/vrchat/2fa", methods=["POST"])
    def vrcx_plus_vrchat_2fa():
        body = request.get_json() or {}
        code = str(body.get("code", "")).strip()
        method = str(body.get("method", "totp")).strip()
        if not code:
            return jsonify({"ok": False, "error": "2FA code required"}), 400
        payload = vrchat_service.verify_2fa(code, method)
        if not payload.get("ok"):
            return jsonify(payload), 400
        return jsonify(payload)

    @app.route("/vrcx-plus/vrchat/email-otp", methods=["POST"])
    def vrcx_plus_vrchat_email_otp():
        payload = vrchat_service.request_email_otp()
        if not payload.get("ok"):
            return jsonify(payload), 400
        return jsonify(payload)

    @app.route("/vrcx-plus/vrchat/logout", methods=["POST"])
    def vrcx_plus_vrchat_logout():
        return jsonify(vrchat_service.logout())

    @app.route("/vrcx-plus/vrchat/avatar-search", methods=["POST"])
    def vrcx_plus_vrchat_avatar_search():
        body = request.get_json() or {}
        query = str(body.get("query", "")).strip()
        n = int(body.get("n", 40))
        offset = int(body.get("offset", 0))
        if len(query) < 2:
            return jsonify({"ok": False, "error": "Query must be at least 2 chars", "results": []}), 400
        source = str(body.get("source", "auto")).strip().lower()
        provider = _vrcx_plus_provider_settings()
        provider_enabled = bool(provider.get("enabled"))
        provider_urls = list(provider.get("urls") or [])
        body_provider_urls = _vrcx_plus_normalize_provider_urls(
            body.get("urls", []),
            fallback_url=str(body.get("provider_url", "")).strip()
        )
        if body_provider_urls:
            provider_urls = body_provider_urls
        use_provider = source in {"provider", "external", "providers"} or (
            source == "auto" and provider_enabled and bool(provider_urls)
        )

        if use_provider:
            payload = vrchat_service.external_avatar_search_many(provider_urls, query, n=n)
            payload["source"] = "provider"
        else:
            payload = vrchat_service.avatar_search(query, n=n, offset=offset)
            payload["source"] = "vrchat"
        if not payload.get("ok"):
            return jsonify(payload), 400
        return jsonify(payload)

    @app.route("/vrcx-plus/vrchat/avatar-info", methods=["POST"])
    def vrcx_plus_vrchat_avatar_info():
        body = request.get_json() or {}
        avatar_id = str(body.get("avatar_id", "")).strip()
        if not avatar_id:
            return jsonify({"ok": False, "error": "avatar_id is required"}), 400
        payload = vrchat_service.get_avatar(avatar_id)
        if not payload.get("ok"):
            return jsonify(payload), 400
        return jsonify(payload)

    @app.route("/vrcx-plus/vrchat/provider", methods=["POST"])
    def vrcx_plus_vrchat_provider():
        body = request.get_json() or {}
        enabled = bool(body.get("enabled", False))
        fallback_url = str(body.get("url", "")).strip()
        raw_urls = body.get("urls", [])
        if isinstance(raw_urls, str) and not raw_urls.strip() and fallback_url:
            raw_urls = [fallback_url]
        user_urls = _vrcx_plus_normalize_provider_urls(raw_urls, fallback_url=fallback_url)
        urls = _vrcx_plus_normalize_provider_urls(
            list(DEFAULT_AVATAR_PROVIDER_URLS) + user_urls,
            fallback_url=fallback_url
        )
        SETTINGS["vrcx_plus_avatar_provider_enabled"] = enabled
        SETTINGS["vrcx_plus_avatar_provider_urls"] = urls
        SETTINGS["vrcx_plus_avatar_provider_url"] = urls[0] if urls else ""
        with open(SETTINGS_FILE, "wb") as f:
            f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        provider = _vrcx_plus_provider_settings()
        return jsonify(
            {
                "ok": True,
                "enabled": provider["enabled"],
                "url": provider["url"],
                "urls": provider["urls"],
                "count": provider["count"]
            }
        )

    @app.route("/vrcx-plus/vrchat/avatar-select", methods=["POST"])
    def vrcx_plus_vrchat_avatar_select():
        body = request.get_json() or {}
        avatar_id = str(body.get("avatar_id", "")).strip()
        if not avatar_id:
            return jsonify({"ok": False, "error": "avatar_id is required"}), 400
        payload = vrchat_service.select_avatar(avatar_id)
        if not payload.get("ok"):
            return jsonify(payload), 400
        return jsonify(payload)


    @app.route("/send", methods=["POST"])
    def send():
        global last_message_sent
        if request.is_json:
            data = request.get_json(force=True)
            msg = data.get("message", "").strip()
        else:
            msg = request.form.get("message", "").strip()
        if msg:
            if send_to_vrchat(msg):
                return jsonify({"ok": True}), 200
            else:
                return jsonify({"ok": False, "error": "OSC send failed"}), 500
        return jsonify({"ok": False, "error": "empty"}), 400

    @app.route("/send_now", methods=["POST"])
    def send_now():
        preview_msg = get_current_preview(advance_page=True)
        if preview_msg and send_to_vrchat(preview_msg):
            return jsonify({"ok": True}), 200
        return jsonify({"ok": False}), 400

    @app.route("/typing_state", methods=["POST"])
    def set_typing_state():
        global typing_state
        data = request.get_json(force=True) if request.is_json else {}
        is_typing = data.get("typing", False)
        
        with typing_state_lock:
            typing_state["is_typing"] = is_typing
            typing_state["show_indicator"] = SETTINGS.get("typing_indicator_enabled", True)
            if not is_typing:
                typing_state["show_indicator"] = False
        
        print(f"[Typing] State changed: is_typing={is_typing}")
        return jsonify({"ok": True}), 200

    @app.route("/send_typed_message", methods=["POST"])
    def send_typed_message():
        global typing_state
        data = request.get_json(force=True) if request.is_json else {}
        message = data.get("message", "").strip()
        
        if not message:
            return jsonify({"ok": False, "error": "empty message"}), 400
        
        display_duration = SETTINGS.get("typed_message_duration", 5)
        
        with typing_state_lock:
            typing_state["is_typing"] = False
            typing_state["typed_message"] = message
            typing_state["display_until"] = time.time() + display_duration
            typing_state["show_indicator"] = False
            typing_state["message_sent"] = False
        
        print(f"[Typing] Message sent: '{message[:30]}...' (display for {display_duration}s)")
        return jsonify({"ok": True}), 200

    @app.route("/cancel_typing", methods=["POST"])
    def cancel_typing():
        global typing_state
        
        with typing_state_lock:
            typing_state["is_typing"] = False
            typing_state["typed_message"] = ""
            typing_state["display_until"] = 0
            typing_state["show_indicator"] = False
            typing_state["message_sent"] = False
        
        print("[Typing] Cancelled")
        return jsonify({"ok": True}), 200

    @app.route("/system_stats")
    def get_system_stats():
        stats = system_stats.get_system_stats()
        return jsonify(stats)

    @app.route("/toggle_system_stats", methods=["POST"])
    def toggle_system_stats():
        enabled = SETTINGS.get("system_stats_enabled", False)
        SETTINGS["system_stats_enabled"] = not enabled
        if not enabled:
            system_stats.start_system_stats(
                update_interval=SETTINGS.get("system_stats_update_interval", 5),
                enable_gpu=SETTINGS.get("system_stats_show_gpu", False)
            )
        else:
            system_stats.stop_system_stats()
        _persist_settings(label="toggle_system_stats")
        return jsonify({"enabled": not enabled})

    @app.route("/afk_status")
    def get_afk_status():
        if SETTINGS.get("afk_enabled", False):
            timeout = SETTINGS.get("afk_timeout", 300)
            afk_detector.check_afk(timeout)
        
        state = afk_detector.get_afk_state()
        return jsonify({
            "is_afk": state["is_afk"],
            "afk_message": afk_detector.get_afk_message(
                SETTINGS.get("afk_message", ""),
                SETTINGS.get("afk_show_duration", True)
            ),
            "duration": afk_detector.get_afk_duration()
        })

    @app.route("/toggle_afk", methods=["POST"])
    def toggle_afk():
        enabled = SETTINGS.get("afk_enabled", False)
        SETTINGS["afk_enabled"] = not enabled
        afk_detector.set_afk_enabled(not enabled)
        with open(SETTINGS_FILE, "wb") as f:
            f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        return jsonify({"enabled": not enabled})

    @app.route("/afk_activity", methods=["POST"])
    def afk_activity():
        afk_detector.update_activity()
        return jsonify({"ok": True})

    @app.route("/toggle_hr_simulator", methods=["POST"])
    def toggle_hr_simulator():
        current = heart_rate_monitor.is_simulator_enabled()
        heart_rate_monitor.set_simulator_enabled(not current)
        if not current:
            SETTINGS["heart_rate_enabled"] = True
        return jsonify({"enabled": not current})

    @app.route("/quick_phrases")
    def get_quick_phrases():
        return jsonify(quick_phrases.get_phrases())

    @app.route("/send_quick_phrase", methods=["POST"])
    def send_quick_phrase():
        data = request.get_json(force=True) if request.is_json else {}
        phrase = data.get("phrase", "").strip()
        if not phrase:
            return jsonify({"ok": False, "error": "empty phrase"}), 400
        _trigger_temporary_message(phrase)
        print(f"[Quick Phrase] Sent: '{phrase[:30]}...'")
        return jsonify({"ok": True})

    @app.route("/add_quick_phrase", methods=["POST"])
    def add_quick_phrase():
        data = request.get_json(force=True) if request.is_json else {}
        text = data.get("text", "").strip()
        emoji = data.get("emoji", "")
        category = data.get("category", "custom")
        
        if not text:
            return jsonify({"ok": False, "error": "empty text"}), 400
        
        quick_phrases.add_phrase(text, emoji, category)
        return jsonify({"ok": True, "phrases": quick_phrases.get_phrases()})

    @app.route("/remove_quick_phrase", methods=["POST"])
    def remove_quick_phrase():
        data = request.get_json(force=True) if request.is_json else {}
        index = data.get("index", -1)
        
        if index < 0:
            return jsonify({"ok": False, "error": "invalid index"}), 400
        
        quick_phrases.remove_phrase(index)
        return jsonify({"ok": True, "phrases": quick_phrases.get_phrases()})

    @app.route("/message_history")
    def get_message_history():
        return jsonify({
            "recent": message_history.get_recent_messages(20),
            "typed": message_history.get_typed_history(10),
            "stats": message_history.get_message_stats()
        })

    @app.route("/soundpad/status", methods=["GET"])
    def soundpad_status():
        return jsonify({
            "ok": True,
            "enabled": SETTINGS.get("soundpad_enabled", True),
            "volume": SETTINGS.get("soundpad_volume", 85),
            "announce": SETTINGS.get("soundpad_announce", False),
            "caption_template": SETTINGS.get("soundpad_caption_template", "Playing: {name}"),
            "folder": soundpad.SOUNDPAD_DIR,
            "clips": soundpad.list_clips(),
            "tts": {
                "enabled": SETTINGS.get("tts_enabled", True),
                "rate": SETTINGS.get("tts_rate", 1.0),
                "pitch": SETTINGS.get("tts_pitch", 1.0),
                "volume": SETTINGS.get("tts_volume", 1.0),
            },
        })

    @app.route("/soundpad/file/<path:filename>", methods=["GET"])
    def soundpad_file(filename):
        path = soundpad.get_clip_path(filename)
        if not path:
            return jsonify({"ok": False, "error": "Clip not found"}), 404
        return send_file(path, as_attachment=False, download_name=os.path.basename(path))

    @app.route("/soundpad/upload", methods=["POST"])
    def soundpad_upload():
        files = request.files.getlist("files")
        if not files:
            single_file = request.files.get("file")
            files = [single_file] if single_file else []
        saved = soundpad.save_uploads(files)
        if not saved:
            return jsonify({"ok": False, "error": "No supported audio files uploaded"}), 400
        return jsonify({"ok": True, "saved": saved, "clips": soundpad.list_clips()})

    @app.route("/soundpad/delete", methods=["POST"])
    def soundpad_delete():
        data = request.get_json(force=True) if request.is_json else {}
        filename = str(data.get("filename", "")).strip()
        if not filename:
            return jsonify({"ok": False, "error": "filename required"}), 400
        if not soundpad.delete_clip(filename):
            return jsonify({"ok": False, "error": "Clip not found"}), 404
        return jsonify({"ok": True, "clips": soundpad.list_clips()})

    @app.route("/soundpad/settings", methods=["POST"])
    def soundpad_settings():
        data = request.get_json(force=True) if request.is_json else {}
        volume = data.get("volume", SETTINGS.get("soundpad_volume", 85))
        try:
            volume = int(volume)
        except Exception:
            volume = SETTINGS.get("soundpad_volume", 85)
        volume = max(0, min(100, volume))

        def _as_float(name, default, low, high):
            try:
                value = float(data.get(name, SETTINGS.get(name, default)))
            except Exception:
                value = float(default)
            return max(low, min(high, value))

        SETTINGS["soundpad_enabled"] = bool(data.get("enabled", SETTINGS.get("soundpad_enabled", True)))
        SETTINGS["soundpad_volume"] = volume
        SETTINGS["soundpad_announce"] = bool(data.get("announce", SETTINGS.get("soundpad_announce", False)))
        SETTINGS["soundpad_caption_template"] = str(data.get("caption_template", SETTINGS.get("soundpad_caption_template", "Playing: {name}")))[:120]
        SETTINGS["tts_enabled"] = bool(data.get("tts_enabled", SETTINGS.get("tts_enabled", True)))
        SETTINGS["tts_rate"] = _as_float("tts_rate", 1.0, 0.5, 2.0)
        SETTINGS["tts_pitch"] = _as_float("tts_pitch", 1.0, 0.0, 2.0)
        SETTINGS["tts_volume"] = _as_float("tts_volume", 1.0, 0.0, 1.0)
        with open(SETTINGS_FILE, "wb") as f:
            f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        return jsonify({"ok": True})

    @app.route("/soundpad/announce", methods=["POST"])
    def soundpad_announce():
        data = request.get_json(force=True) if request.is_json else {}
        filename = str(data.get("filename", "")).strip()
        name = str(data.get("name", "") or soundpad.display_name(filename)).strip()
        template = SETTINGS.get("soundpad_caption_template", "Playing: {name}") or "{name}"
        message = template.replace("{name}", name)[:144]
        if not SETTINGS.get("soundpad_announce", False):
            return jsonify({"ok": True, "sent": False})
        if send_to_vrchat(message):
            return jsonify({"ok": True, "sent": True, "message": message})
        return jsonify({"ok": False, "error": "OSC send failed"}), 500

    @app.route("/hr_stats")
    def get_hr_stats():
        stats = heart_rate_monitor.get_hr_stats()
        state = heart_rate_monitor.get_heart_rate_state()
        return jsonify({
            "current_bpm": state.get("bpm", 0),
            "is_connected": state.get("is_connected", False),
            "session_min": stats.get("session_min", 0),
            "session_max": stats.get("session_max", 0),
            "session_avg": stats.get("session_avg", 0),
            "trend": stats.get("trend", "stable"),
            "samples": stats.get("samples", 0)
        })

    @app.route("/reset_hr_stats", methods=["POST"])
    def reset_hr_stats():
        heart_rate_monitor.reset_hr_stats()
        return jsonify({"ok": True})

    @app.route("/test_connection", methods=["POST"])
    def test_connection():
        if test_osc_connection():
            return jsonify({"ok": True, "status": "connected"}), 200
        return jsonify({"ok": False, "status": "disconnected"}), 500

    @app.route("/ping_quest", methods=["POST"])
    def ping_quest():
        try:
            client.send_message("/chatbox/visible", 1)
            return jsonify({"ok": True}), 200
        except Exception as e:
            log_error("Ping Quest failed", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/toggle_chatbox", methods=["POST"])
    def toggle_chatbox():
        global chatbox_visible
        chatbox_visible = not chatbox_visible
        SETTINGS["chatbox_visible"] = chatbox_visible
        _persist_settings(label="toggle_chatbox")
        if chatbox_visible:
            try:
                client.send_message("/chatbox/visible", 1)
            except:
                pass
        else:
            try:
                client.send_message("/chatbox/visible", 0)
            except:
                pass
        return ("", 204)

    @app.route("/toggle_auto_send", methods=["POST"])
    def toggle_auto_send():
        global auto_send_paused
        auto_send_paused = not auto_send_paused
        return ("", 204)

    @app.route("/toggle_time", methods=["POST"])
    def toggle_time():
        global show_time
        show_time = not show_time
        SETTINGS["show_time"] = show_time
        with open(SETTINGS_FILE, "wb") as f:
            f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        return ("", 204)

    @app.route("/toggle_custom", methods=["POST"])
    def toggle_custom():
        global show_custom
        show_custom = not show_custom
        SETTINGS["show_custom"] = show_custom
        with open(SETTINGS_FILE, "wb") as f:
            f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        return ("", 204)

    @app.route("/toggle_music", methods=["POST"])
    def toggle_music():
        global show_music
        show_music = not show_music
        SETTINGS["show_music"] = show_music
        with open(SETTINGS_FILE, "wb") as f:
            f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        return ("", 204)

    @app.route("/toggle_music_progress", methods=["POST"])
    def toggle_music_progress():
        SETTINGS["music_progress"] = not SETTINGS.get("music_progress", True)
        with open(SETTINGS_FILE, "wb") as f:
            f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        return ("", 204)

    @app.route("/toggle_theme", methods=["POST"])
    def toggle_theme():
        current = SETTINGS.get("theme", "dark")
        SETTINGS["theme"] = "light" if current == "dark" else "dark"
        _persist_settings(label="toggle_theme")
        return jsonify({"theme": SETTINGS["theme"]}), 200

    @app.route("/toggle_random_order", methods=["POST"])
    def toggle_random_order():
        SETTINGS["random_order"] = not SETTINGS.get("random_order", False)
        with open(SETTINGS_FILE, "wb") as f:
            f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        return ("", 204)

    @app.route("/toggle_module_icons", methods=["POST"])
    def toggle_module_icons():
        SETTINGS["show_module_icons"] = not SETTINGS.get("show_module_icons", True)
        with open(SETTINGS_FILE, "wb") as f:
            f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        return ("", 204)

    @app.route("/toggle_streamer_mode", methods=["POST"])
    def toggle_streamer_mode():
        SETTINGS["streamer_mode"] = not SETTINGS.get("streamer_mode", False)
        _persist_settings(label="toggle_streamer_mode")
        return jsonify({"streamer_mode": SETTINGS["streamer_mode"]}), 200

    @app.route("/toggle_compact_mode", methods=["POST"])
    def toggle_compact_mode():
        SETTINGS["compact_mode"] = not SETTINGS.get("compact_mode", False)
        _persist_settings(label="toggle_compact_mode")
        return jsonify({"compact_mode": SETTINGS["compact_mode"]}), 200

    @app.route("/set_progress_style", methods=["POST"])
    def set_progress_style():
        data = request.get_json(force=True)
        style = data.get("style", "bar")
        if style in ["bar", "dots", "percentage"]:
            SETTINGS["progress_style"] = style
            with open(SETTINGS_FILE, "wb") as f:
                f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        return ("", 204)
    
    @app.route("/toggle_window", methods=["POST"])
    def toggle_window():
        global show_window
        show_window = not show_window
        SETTINGS["show_window"] = show_window
        with open(SETTINGS_FILE, "wb") as f:
            f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        return ("", 204)
    
    @app.route("/toggle_window_tracking", methods=["POST"])
    def toggle_window_tracking():
        global show_window
        SETTINGS["window_tracking_enabled"] = not SETTINGS.get("window_tracking_enabled", False)
        show_window = SETTINGS["window_tracking_enabled"]
        SETTINGS["show_window"] = show_window
        _persist_settings(label="toggle_window_tracking")
        return jsonify({"window_tracking_enabled": SETTINGS["window_tracking_enabled"]}), 200
    
    @app.route("/save_window_tracking_mode", methods=["POST"])
    def save_window_tracking_mode():
        data = request.get_json(force=True)
        mode = data.get("mode", "both")
        if mode in ["app", "browser", "both"]:
            SETTINGS["window_tracking_mode"] = mode
            with open(SETTINGS_FILE, "wb") as f:
                f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        return jsonify({"ok": True}), 200
    
    @app.route("/toggle_heartrate", methods=["POST"])
    def toggle_heartrate():
        global show_heartrate
        show_heartrate = not show_heartrate
        SETTINGS["show_heartrate"] = show_heartrate
        if show_heartrate and not SETTINGS.get("heart_rate_enabled", False):
            SETTINGS["heart_rate_enabled"] = True
            heart_rate_monitor.start_heart_rate_tracker(interval=SETTINGS.get("heart_rate_update_interval", 5))
        with open(SETTINGS_FILE, "wb") as f:
            f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        return ("", 204)
    
    @app.route("/toggle_heart_rate_enabled", methods=["POST"])
    def toggle_heart_rate_enabled():
        global show_heartrate
        SETTINGS["heart_rate_enabled"] = not SETTINGS.get("heart_rate_enabled", False)
        show_heartrate = SETTINGS["heart_rate_enabled"]
        SETTINGS["show_heartrate"] = show_heartrate
        _persist_settings(label="toggle_heart_rate")
        return jsonify({"heart_rate_enabled": SETTINGS["heart_rate_enabled"]}), 200
    
    @app.route("/save_heart_rate_settings", methods=["POST"])
    def save_heart_rate_settings():
        global show_heartrate
        data = request.get_json(force=True)
        source = str(data.get("source", SETTINGS.get("heart_rate_source", "pulsoid"))).strip().lower()
        if source not in {"pulsoid", "hyperate", "custom"}:
            source = "pulsoid"
        SETTINGS["heart_rate_source"] = source
        for field, key in (
            ("pulsoid_token", "heart_rate_pulsoid_token"),
            ("hyperate_id", "heart_rate_hyperate_id"),
            ("custom_api", "heart_rate_custom_api"),
        ):
            value = str(data.get(field, "") or "").strip()
            if value and "•" not in value and "â€¢" not in value:
                SETTINGS[key] = value
            elif data.get(f"clear_{field}"):
                SETTINGS[key] = ""
        try:
            SETTINGS["heart_rate_update_interval"] = max(1, min(60, int(data.get("update_interval", SETTINGS.get("heart_rate_update_interval", 5)))))
        except Exception:
            SETTINGS["heart_rate_update_interval"] = 5
        if "enabled" in data:
            SETTINGS["heart_rate_enabled"] = bool(data.get("enabled"))
            SETTINGS["show_heartrate"] = SETTINGS["heart_rate_enabled"]
            show_heartrate = SETTINGS["heart_rate_enabled"]
        SETTINGS["heart_rate_osc_enabled"] = bool(data.get("heart_rate_osc_enabled", SETTINGS.get("heart_rate_osc_enabled", False)))
        try:
            SETTINGS["heart_rate_osc_min_bpm"] = max(20, min(220, int(data.get("heart_rate_osc_min_bpm", SETTINGS.get("heart_rate_osc_min_bpm", 40)))))
            SETTINGS["heart_rate_osc_max_bpm"] = max(60, min(260, int(data.get("heart_rate_osc_max_bpm", SETTINGS.get("heart_rate_osc_max_bpm", 200)))))
            if SETTINGS["heart_rate_osc_max_bpm"] <= SETTINGS["heart_rate_osc_min_bpm"]:
                SETTINGS["heart_rate_osc_min_bpm"] = 40
                SETTINGS["heart_rate_osc_max_bpm"] = 200
        except Exception:
            SETTINGS["heart_rate_osc_min_bpm"] = 40
            SETTINGS["heart_rate_osc_max_bpm"] = 200
        if "hr_show_trend" in data:
            SETTINGS["hr_show_trend"] = data.get("hr_show_trend", True)
        if "hr_show_stats" in data:
            SETTINGS["hr_show_stats"] = data.get("hr_show_stats", False)
        _persist_settings(label="heart_rate_settings")
        return jsonify({"ok": True, "heart_rate": heart_rate_monitor.get_heart_rate_state()}), 200

    @app.route("/save_spotify_credentials", methods=["POST"])
    def save_spotify_credentials():
        data = request.get_json(force=True)
        client_id = str(data.get("client_id", "") or "").strip()
        if client_id and "•" not in client_id:
            SETTINGS["spotify_client_id"] = client_id
        client_secret = str(data.get("client_secret", "") or "").strip()
        if client_secret and "•" not in client_secret:
            SETTINGS["spotify_client_secret"] = client_secret
        _persist_settings(label="spotify_credentials")
        spotify.force_reinit()
        return jsonify({"ok": True, "redirect_uri": f"{request.host_url}spotify-callback"}), 200

    @app.route("/save_lastfm_username", methods=["POST"])
    def save_lastfm_username():
        data = request.get_json(force=True)
        username = str(data.get("lastfm_username", "") or "").strip()
        SETTINGS["lastfm_username"] = username
        _persist_settings(label="lastfm_username")
        return jsonify({"ok": True}), 200

    @app.route("/save_now_playing_method", methods=["POST"])
    def save_now_playing_method():
        data = request.get_json(force=True)
        method = str(data.get("now_playing_method", "") or "").strip()
        if method not in ("lastfm", "spotify_api"):
            return jsonify({"ok": False, "error": "Invalid now playing method."}), 400
        SETTINGS["now_playing_method"] = method
        _persist_settings(label="now_playing_method")
        return jsonify({"ok": True}), 200

    @app.route("/save_emoji_settings", methods=["POST"])
    def save_emoji_settings():
        data = request.get_json(force=True)
        time_emoji = data.get("time_emoji", "⏰")
        custom_emoji = data.get("custom_emoji", "💬")
        song_emoji = data.get("song_emoji", "🎶")
        window_emoji = data.get("window_emoji", "💻")
        heartrate_emoji = data.get("heartrate_emoji", "❤️")
        weather_emoji = data.get("weather_emoji", "🌤️")
        system_stats_emoji = data.get("system_stats_emoji", "📊")
        afk_emoji = data.get("afk_emoji", "💤")
        cpu_emoji = data.get("system_stats_cpu_emoji", "🧠")
        ram_emoji = data.get("system_stats_ram_emoji", "💾")
        gpu_emoji = data.get("system_stats_gpu_emoji", "🎮")
        network_emoji = data.get("system_stats_network_emoji", "📡")
        
        SETTINGS["time_emoji"] = time_emoji[:5] if time_emoji else "⏰"
        SETTINGS["custom_emoji"] = custom_emoji[:5] if custom_emoji else "💬"
        SETTINGS["song_emoji"] = song_emoji[:5] if song_emoji else "🎶"
        SETTINGS["window_emoji"] = window_emoji[:5] if window_emoji else "💻"
        SETTINGS["heartrate_emoji"] = heartrate_emoji[:5] if heartrate_emoji else "❤️"
        SETTINGS["weather_emoji"] = weather_emoji[:5] if weather_emoji else "🌤️"
        SETTINGS["system_stats_emoji"] = system_stats_emoji[:5] if system_stats_emoji else "📊"
        SETTINGS["afk_emoji"] = afk_emoji[:5] if afk_emoji else "💤"
        SETTINGS["system_stats_cpu_emoji"] = cpu_emoji[:5] if cpu_emoji else "🧠"
        SETTINGS["system_stats_ram_emoji"] = ram_emoji[:5] if ram_emoji else "💾"
        SETTINGS["system_stats_gpu_emoji"] = gpu_emoji[:5] if gpu_emoji else "🎮"
        SETTINGS["system_stats_network_emoji"] = network_emoji[:5] if network_emoji else "📡"
        
        with open(SETTINGS_FILE, "wb") as f:
            f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        return jsonify({"ok": True}), 200
    
    @app.route("/save_typing_settings", methods=["POST"])
    def save_typing_settings():
        data = request.get_json(force=True)
        duration = int(data.get("typed_message_duration", 5))
        duration = max(2, min(15, duration))
        SETTINGS["typed_message_duration"] = duration
        SETTINGS["typing_indicator_enabled"] = data.get("typing_indicator_enabled", True)
        with open(SETTINGS_FILE, "wb") as f:
            f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        return jsonify({"ok": True}), 200

    @app.route("/save_window_settings", methods=["POST"])
    def save_window_settings():
        data = request.get_json(force=True)
        SETTINGS["window_prefix"] = data.get("window_prefix", "")[:30]
        if data.get("window_tracking_mode") in ["app", "browser", "both"]:
            SETTINGS["window_tracking_mode"] = data["window_tracking_mode"]
        if "window_emoji" in data:
            SETTINGS["window_emoji"] = str(data.get("window_emoji") or "")[:8]
        if "window_title_max_length" in data:
            try:
                SETTINGS["window_title_max_length"] = max(10, min(int(data["window_title_max_length"]), 144))
            except (TypeError, ValueError):
                pass
        if "window_name_aliases" in data:
            raw_aliases = data.get("window_name_aliases")
            if isinstance(raw_aliases, dict):
                SETTINGS["window_name_aliases"] = {
                    str(k).strip()[:80]: str(v).strip()[:40]
                    for k, v in raw_aliases.items()
                    if str(k).strip() and str(v).strip()
                }
        with open(SETTINGS_FILE, "wb") as f:
            f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        return jsonify({"ok": True, "window_name_aliases": SETTINGS.get("window_name_aliases", {})}), 200

    @app.route("/save_afk_settings", methods=["POST"])
    def save_afk_settings():
        data = request.get_json(force=True)
        timeout = int(data.get("afk_timeout", 300))
        timeout = max(60, min(900, timeout))
        SETTINGS["afk_timeout"] = timeout
        SETTINGS["afk_message"] = data.get("afk_message", "")[:50]
        SETTINGS["afk_show_duration"] = data.get("afk_show_duration", True)
        with open(SETTINGS_FILE, "wb") as f:
            f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        return jsonify({"ok": True}), 200

    @app.route("/save_system_stats_settings", methods=["POST"])
    def save_system_stats_settings():
        data = request.get_json(force=True)
        if "enabled" in data:
            SETTINGS["system_stats_enabled"] = bool(data.get("enabled"))
        SETTINGS["system_stats_show_cpu"] = data.get("show_cpu", True)
        SETTINGS["system_stats_show_ram"] = data.get("show_ram", True)
        SETTINGS["system_stats_show_gpu"] = data.get("show_gpu", False)
        SETTINGS["system_stats_show_network"] = data.get("show_network", False)
        SETTINGS["system_stats_show_battery"] = data.get("show_battery", False)
        try:
            update_interval = int(data.get("update_interval", SETTINGS.get("system_stats_update_interval", 5)))
        except Exception:
            update_interval = SETTINGS.get("system_stats_update_interval", 5)
        SETTINGS["system_stats_update_interval"] = max(2, min(update_interval, 30))
        try:
            decimals = int(data.get("decimals", SETTINGS.get("system_stats_decimals", 0)))
        except Exception:
            decimals = 0
        SETTINGS["system_stats_decimals"] = max(0, min(decimals, 2))
        units = str(data.get("network_units", SETTINGS.get("system_stats_network_units", "bits"))).lower()
        SETTINGS["system_stats_network_units"] = units if units in {"bits", "bytes"} else "bits"
        SETTINGS["system_stats_template"] = str(
            data.get("template", SETTINGS.get("system_stats_template", DEFAULT_SYSTEM_STATS_TEMPLATE))
        )[:1200] or DEFAULT_SYSTEM_STATS_TEMPLATE
        system_stats.update_system_stats(enable_gpu=SETTINGS["system_stats_show_gpu"])
        if SETTINGS.get("system_stats_enabled", False):
            system_stats.start_system_stats(
                update_interval=SETTINGS["system_stats_update_interval"],
                enable_gpu=SETTINGS["system_stats_show_gpu"]
            )
        else:
            system_stats.stop_system_stats()
        with open(SETTINGS_FILE, "wb") as f:
            f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        _sync_runtime_from_settings()
        preview = _format_system_stats_line(system_stats.get_system_stats())
        return jsonify({
            "ok": True,
            "enabled": SETTINGS.get("system_stats_enabled", False),
            "preview": preview
        }), 200

    @app.route("/save_hr_settings", methods=["POST"])
    def save_hr_settings():
        data = request.get_json(force=True)
        SETTINGS["hr_show_trend"] = data.get("hr_show_trend", True)
        SETTINGS["hr_show_stats"] = data.get("hr_show_stats", False)
        with open(SETTINGS_FILE, "wb") as f:
            f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        return jsonify({"ok": True}), 200
    
    @app.route("/save_premium_styling", methods=["POST"])
    def save_premium_styling():
        data = request.get_json(force=True)
        custom_background = data.get("custom_background", "")
        custom_button_color = data.get("custom_button_color", "")
        
        SETTINGS["custom_background"] = custom_background[:200] if custom_background else ""
        SETTINGS["custom_button_color"] = custom_button_color[:50] if custom_button_color else ""
        
        with open(SETTINGS_FILE, "wb") as f:
            f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        return jsonify({"ok": True}), 200

    @app.route("/save_settings", methods=["POST"])
    def save_settings():
        try:
            global client
            ip = request.form.get("quest_ip", SETTINGS.get("quest_ip"))
            

            try:
                port = int(request.form.get("quest_port", SETTINGS.get("quest_port")))
            except (ValueError, TypeError):
                port = SETTINGS.get("quest_port", 9000)
            
            try:
                osc_send_interval = int(request.form.get("osc_send_interval", SETTINGS.get("osc_send_interval", 3)))
            except (ValueError, TypeError):
                osc_send_interval = SETTINGS.get("osc_send_interval", 3)
            
            try:
                dashboard_update_interval = int(request.form.get("dashboard_update_interval", SETTINGS.get("dashboard_update_interval", 3)))
            except (ValueError, TypeError):
                dashboard_update_interval = SETTINGS.get("dashboard_update_interval", 3)
            dashboard_update_interval = max(2, min(dashboard_update_interval, 60))
            
            timezone = request.form.get("timezone", SETTINGS.get("timezone"))

            SETTINGS.update({
                "quest_ip": ip,
                "quest_port": port,
                "osc_send_interval": osc_send_interval,
                "dashboard_update_interval": dashboard_update_interval,
                "timezone": timezone
            })
            with open(SETTINGS_FILE, "wb") as f:
                f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
            

            from settings import reload_settings
            reload_settings()
            
            client = make_client()
            

            print("[Settings] ✓ Settings saved successfully, redirecting to dashboard...")
            return redirect("/")
            
        except Exception as e:
            print(f"[Save Settings ERROR] {e}")
            import traceback
            traceback.print_exc()
            error_msg = f"Error saving settings: {str(e)}\n{traceback.format_exc()}"
            print(f"[ERROR] Full traceback:\n{error_msg}")
            return f"Error saving settings: {str(e)}", 500

    @app.route("/restart_app", methods=["POST"])
    def restart_app():
        print("[Server] ========================================")
        print("[Server] CLOSE REQUESTED BY USER")
        print("[Server] ========================================")
        

        SETTINGS["spotify_needs_restart"] = False
        with open(SETTINGS_FILE, "wb") as f:
            f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        print("[Server] ✓ Cleared restart flag in settings.json")
        
        import os
        import sys
        import threading
        
        def delayed_shutdown():
            import time
            time.sleep(2.0)
            print("[Server] ========================================")
            print("[Server] SHUTTING DOWN - PLEASE RELAUNCH APP")
            print("[Server] ========================================")
            os._exit(0)
        
        threading.Thread(target=delayed_shutdown, daemon=True).start()
        return jsonify({"ok": True, "message": "Closing Crystal Client... Please relaunch the app."}), 200

    @app.route("/save_customs", methods=["POST"])
    def save_customs():
        if request.is_json:
            data = request.get_json(force=True) or {}
            text = str(data.get("customs", "")).strip()
        else:
            text = request.form.get("customs", "").strip()
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if not lines:
            lines = ["Custom Message Test"]
        SETTINGS["custom_texts"] = lines
        with open(SETTINGS_FILE, "wb") as f:
            f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        nonlocal_vars_update_customs(lines)
        if request.is_json:
            return jsonify({"ok": True, "custom_texts": lines}), 200
        return redirect("/")

    @app.route("/update_custom_inline", methods=["POST"])
    def update_custom_inline():
        data = request.get_json(force=True)
        index = int(data.get("index", 0))
        new_text = data.get("text", "").strip()
        
        if 0 <= index < len(SETTINGS["custom_texts"]):
            SETTINGS["custom_texts"][index] = new_text
            with open(SETTINGS_FILE, "wb") as f:
                f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
            nonlocal_vars_update_customs(SETTINGS["custom_texts"])
            return jsonify({"ok": True}), 200
        return jsonify({"ok": False}), 400

    @app.route("/add_custom_message", methods=["POST"])
    def add_custom_message():
        data = request.get_json(force=True)
        new_text = data.get("text", "").strip()
        
        if new_text:
            SETTINGS["custom_texts"].append(new_text)
            with open(SETTINGS_FILE, "wb") as f:
                f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
            nonlocal_vars_update_customs(SETTINGS["custom_texts"])
            return jsonify({"ok": True}), 200
        return jsonify({"ok": False}), 400

    @app.route("/delete_custom_message", methods=["POST"])
    def delete_custom_message():
        data = request.get_json(force=True)
        index = int(data.get("index", 0))
        
        if 0 <= index < len(SETTINGS["custom_texts"]):
            SETTINGS["custom_texts"].pop(index)
            if not SETTINGS["custom_texts"]:
                SETTINGS["custom_texts"] = ["Custom Message Test"]
            with open(SETTINGS_FILE, "wb") as f:
                f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
            nonlocal_vars_update_customs(SETTINGS["custom_texts"])
            return jsonify({"ok": True}), 200
        return jsonify({"ok": False}), 400

    @app.route("/move_custom_message", methods=["POST"])
    def move_custom_message():
        data = request.get_json(force=True)
        index = int(data.get("index", 0))
        direction = data.get("direction", "up")
        
        messages = SETTINGS["custom_texts"]
        if direction == "up" and index > 0:
            messages[index], messages[index - 1] = messages[index - 1], messages[index]
        elif direction == "down" and index < len(messages) - 1:
            messages[index], messages[index + 1] = messages[index + 1], messages[index]
        else:
            return jsonify({"ok": False}), 400
        
        SETTINGS["custom_texts"] = messages
        with open(SETTINGS_FILE, "wb") as f:
            f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        nonlocal_vars_update_customs(SETTINGS["custom_texts"])
        return jsonify({"ok": True}), 200

    @app.route("/set_message_weight", methods=["POST"])
    def set_message_weight():
        data = request.get_json(force=True)
        index = str(data.get("index", 0))
        weight = int(data.get("weight", 1))
        
        if "weighted_messages" not in SETTINGS:
            SETTINGS["weighted_messages"] = {}
        
        SETTINGS["weighted_messages"][index] = max(1, weight)
        with open(SETTINGS_FILE, "wb") as f:
            f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        return jsonify({"ok": True}), 200

    def nonlocal_vars_update_customs(lines):
        global CUSTOM_TEXTS, current_custom_text, text_cycle_index
        CUSTOM_TEXTS = lines
        text_cycle_index = 0
        current_custom_text = CUSTOM_TEXTS[0] if CUSTOM_TEXTS else ""

    @app.route("/save_per_message_intervals", methods=["POST"])
    def save_per_message_intervals():
        data = request.get_json(force=True)
        intervals = data.get("intervals", {})
        SETTINGS["per_message_intervals"] = intervals
        with open(SETTINGS_FILE, "wb") as f:
            f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        return jsonify({"ok": True}), 200

    @app.route("/save_layout", methods=["POST"])
    def save_layout():
        data = request.get_json(force=True)
        layout = data.get("layout_order") or data.get("layout", SETTINGS.get("layout_order", ["time","custom","vrchat_live","song","window","heartrate","weather"]))
        allowed = {"time", "custom", "vrchat_live", "song", "window", "heartrate", "weather", "system_stats", "vr_battery", "volume", "device_storage", "afk", "mute"}
        filtered = [p for p in layout if p in allowed or _is_spacer_key(p)]
        if not [p for p in filtered if p in allowed]:
            filtered = ["time", "custom", "vrchat_live", "song", "window", "heartrate", "weather", "system_stats", "vr_battery", "afk"]
        SETTINGS["layout_order"] = filtered

        if "spacers" in data and isinstance(data["spacers"], dict):
            kept_ids = {p for p in filtered if _is_spacer_key(p)}
            SETTINGS["layout_spacers"] = {
                str(k): str(v)[:20] for k, v in data["spacers"].items() if k in kept_ids
            }

        with open(SETTINGS_FILE, "wb") as f:
            f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        return jsonify({"ok": True, "layout_order": filtered, "layout_spacers": SETTINGS.get("layout_spacers", {})}), 200

    @app.route("/reset_settings", methods=["POST"])
    def reset_settings():
        global client, CUSTOM_TEXTS, current_custom_text, text_cycle_index
        
        from settings import DEFAULTS
        
        SETTINGS.clear()
        SETTINGS.update(DEFAULTS)
        _persist_settings(backup=True, label="reset_settings")
        
        CUSTOM_TEXTS = DEFAULTS["custom_texts"]
        text_cycle_index = 0
        current_custom_text = CUSTOM_TEXTS[0]
        client = make_client()
        
        return jsonify({"ok": True}), 200

    @app.route("/download_settings", methods=["GET"])
    def download_settings():
        try:
            abs_path = os.path.abspath(SETTINGS_FILE)
            if not os.path.exists(abs_path):
                return jsonify({"error": "Settings file not found"}), 404
            
            response = send_file(
                abs_path,
                as_attachment=True,
                download_name=f"vrchat_chatbox_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                mimetype='application/octet-stream',
                etag=False,
                conditional=False
            )
            response.headers['Content-Disposition'] = f'attachment; filename="vrchat_chatbox_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json"'
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
            return response
        except Exception as e:
            log_error("Failed to download settings", e)
            return jsonify({"error": str(e)}), 500

    @app.route("/upload_settings", methods=["POST"])
    def upload_settings():
        try:
            data = request.get_json(force=True)
            if not data:
                return jsonify({"error": "No data provided"}), 400
            
            from settings import DEFAULTS
            validated_settings = {}
            for key, default_value in DEFAULTS.items():
                if key in data:
                    validated_settings[key] = data[key]
                else:
                    validated_settings[key] = default_value
            
            SETTINGS.clear()
            SETTINGS.update(validated_settings)
            
            with open(SETTINGS_FILE, "wb") as f:
                f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
            
            global client, CUSTOM_TEXTS, current_custom_text, text_cycle_index
            CUSTOM_TEXTS = SETTINGS.get("custom_texts", [])
            text_cycle_index = 0
            current_custom_text = CUSTOM_TEXTS[0] if CUSTOM_TEXTS else "Custom Message Test"
            client = make_client()
            
            return jsonify({"ok": True}), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    @app.route("/download_log", methods=["GET"])
    def download_log():
        abs_path = os.path.abspath(ERROR_LOG_FILE)
        if not os.path.exists(abs_path):
            return jsonify({"error": "No error log found"}), 404
        
        response = send_file(
            abs_path,
            as_attachment=True,
            download_name=f"vrchat_errors_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
            mimetype='application/octet-stream',
            etag=False,
            conditional=False
        )
        response.headers['Content-Disposition'] = f'attachment; filename="vrchat_errors_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log"'
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response

    @app.route("/spotify-auth", methods=["GET"])
    def spotify_auth():
        try:
            client_id = SETTINGS.get("spotify_client_id", "").strip()
            client_secret = SETTINGS.get("spotify_client_secret", "").strip()
            if not client_id or not client_secret:
                return render_template("error.html",
                    title="Spotify Not Configured",
                    message="Add your Spotify Client ID and Client Secret under Integrations - Spotify Setup first."), 400

            state = secrets.token_urlsafe(16)
            _spotify_oauth_state["value"] = state
            params = {
                "client_id": client_id,
                "response_type": "code",
                "redirect_uri": f"{request.host_url}spotify-callback",
                "scope": SPOTIFY_SCOPES,
                "state": state,
            }
            print(f"[Spotify Auth] Redirecting to Spotify authorization URL")
            return redirect(f"{SPOTIFY_AUTH_URL}?{urlencode(params)}")

        except Exception as e:
            print(f"[Spotify Auth ERROR] {e}")
            import traceback
            traceback.print_exc()
            return render_template("error.html",
                title="Spotify Authorization Error",
                message=f"An error occurred while starting Spotify authorization: {str(e)}"), 500

    @app.route("/spotify-callback")
    def spotify_callback():
        try:
            error = request.args.get('error')
            if error:
                print(f"[Spotify Callback] Authorization error: {error}")
                return render_template("error.html",
                    title="Spotify Authorization Denied",
                    message="You denied Spotify access. Please try again if you want to connect Spotify."), 400

            code = request.args.get("code", "").strip()
            state = request.args.get("state", "").strip()
            expected_state = _spotify_oauth_state.get("value", "")
            _spotify_oauth_state["value"] = ""
            if not code or not state or state != expected_state:
                return render_template("error.html",
                    title="Sign-in Expired",
                    message="Please start the connection again from Crystal Client."), 400

            client_id = SETTINGS.get("spotify_client_id", "").strip()
            client_secret = SETTINGS.get("spotify_client_secret", "").strip()
            if not client_id or not client_secret:
                return render_template("error.html",
                    title="Spotify Not Configured",
                    message="Add your Spotify Client ID and Client Secret under Integrations - Spotify Setup first."), 400

            auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
            token_response = requests.post(
                SPOTIFY_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": f"{request.host_url}spotify-callback",
                },
                headers={"Authorization": f"Basic {auth}"},
                timeout=15,
            )
            if token_response.status_code != 200:
                print(f"[Spotify Callback] Token exchange failed: {token_response.status_code} {token_response.text[:200]}")
                return render_template("error.html",
                    title="Spotify Error",
                    message="Could not complete sign-in. Double-check your Client ID and Secret, then try again."), 502

            tokens = token_response.json()
            refresh_token = tokens.get("refresh_token", "").strip()
            if refresh_token:
                print(f"[Spotify Callback] Received authorization token")
                SETTINGS["spotify_refresh_token"] = refresh_token
                print(f"[Spotify Callback] Authorization complete")
                SETTINGS["spotify_needs_restart"] = False
                with open(SETTINGS_FILE, "wb") as f:
                    f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
                spotify.force_reinit()
                return redirect("/?spotify=connected")

            return render_template("error.html",
                title="Authorization Failed",
                message="Spotify authorization was not successful. Please try again from the dashboard."), 400
                
        except Exception as e:
            print(f"[Spotify Callback ERROR] {e}")
            import traceback
            traceback.print_exc()
            return render_template("error.html",
                title="Spotify Connection Error",
                message=f"An error occurred while connecting to Spotify: {str(e)}"), 500

    @app.route("/toggle_weather", methods=["POST"])
    def toggle_weather():
        global show_weather
        show_weather = not show_weather
        SETTINGS["show_weather"] = show_weather
        SETTINGS["weather_enabled"] = show_weather
        _persist_settings(label="toggle_weather")
        
        if show_weather:
            weather_service.enable_weather(SETTINGS.get("weather_location", "auto"))
        else:
            weather_service.disable_weather()
        
        return jsonify({"show_weather": show_weather, "weather_enabled": SETTINGS.get("weather_enabled", False)}), 200

    @app.route("/toggle_slim_chatbox", methods=["POST"])
    def toggle_slim_chatbox():
        SETTINGS["slim_chatbox"] = not SETTINGS.get("slim_chatbox", False)
        _persist_settings(label="toggle_slim_chatbox")
        return jsonify({"slim_chatbox": SETTINGS["slim_chatbox"]}), 200

    @app.route("/save_chatbox_studio", methods=["POST"])
    def save_chatbox_studio():
        data = request.get_json(silent=True) or {}
        settings = _save_chatbox_template_settings(data)
        preview = get_current_preview()
        return jsonify({"ok": True, "settings": settings, "preview": preview}), 200

    @app.route("/get_frame_styles", methods=["GET"])
    def get_frame_styles():
        styles = chatbox_frames.get_frame_styles()
        current = SETTINGS.get("chatbox_frame", "none")
        return jsonify({"styles": styles, "current": current}), 200

    @app.route("/set_chatbox_frame", methods=["POST"])
    def set_chatbox_frame():
        data = request.get_json()
        frame_id = data.get("frame", "none")
        SETTINGS["chatbox_frame"] = frame_id
        _persist_settings(label="chatbox_frame")
        emoji = SETTINGS.get("chatbox_frame_emoji", chatbox_frames.DEFAULT_FRAME_EMOJI)
        preview = chatbox_frames.get_frame_preview(frame_id, emoji=emoji)
        return jsonify({"ok": True, "frame": frame_id, "preview": preview}), 200

    @app.route("/preview_frame", methods=["POST"])
    def preview_frame():
        data = request.get_json()
        frame_id = data.get("frame", "none")
        emoji = str(data.get("emoji") or SETTINGS.get("chatbox_frame_emoji", chatbox_frames.DEFAULT_FRAME_EMOJI))
        preview = chatbox_frames.get_frame_preview(frame_id, emoji=emoji)
        return jsonify({"preview": preview}), 200

    @app.route("/weather_status", methods=["GET"])
    def weather_status():
        state = weather_service.get_weather_state()
        return jsonify(state), 200

    @app.route("/save_weather_settings", methods=["POST"])
    def save_weather_settings():
        data = request.get_json()
        location = data.get("location", "auto")
        temp_unit = data.get("temp_unit", "F")
        SETTINGS["weather_location"] = location
        SETTINGS["weather_temp_unit"] = temp_unit
        with open(SETTINGS_FILE, "wb") as f:
            f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        return jsonify({"ok": True}), 200

    @app.route("/check_updates", methods=["GET"])
    def check_updates():
        update_info = github_updater.check_for_updates(force=True)
        return jsonify(update_info or {"error": "Could not check for updates"}), 200

    @app.route("/update_info", methods=["GET"])
    def update_info():
        current_version = github_updater.get_current_version()
        update_info = github_updater.check_for_updates(force=False)
        return jsonify({
            "current_version": current_version,
            "update_info": update_info
        }), 200

    @app.route("/generate_ai_message", methods=["POST"])
    def generate_ai_message():
        if not openai_client.is_configured():
            return jsonify({"error": "OpenAI not configured. Please set OPENAI_API_KEY environment variable."}), 400
        
        data = request.get_json()
        mood = data.get("mood", "funny")
        theme = data.get("theme", "")
        max_length = data.get("max_length", 30)
        
        message = openai_client.generate_message(mood, theme, max_length)
        
        if message:
            return jsonify({"message": message, "ok": True}), 200
        else:
            return jsonify({"error": "Failed to generate message"}), 500

    @app.route("/ai_moods", methods=["GET"])
    def ai_moods():
        return jsonify({"moods": list(openai_client.MOODS.keys())}), 200

    @app.route("/profiles", methods=["GET"])
    def get_profiles():
        profiles = profiles_manager.list_profiles()
        return jsonify({"profiles": profiles}), 200

    @app.route("/save_profile", methods=["POST"])
    def save_profile():
        data = request.get_json()
        name = data.get("name", "")
        
        if not name or not name.strip():
            return jsonify({"error": "Profile name is required"}), 400
        
        settings_to_save = {
            "show_time": show_time,
            "show_custom": show_custom,
            "show_music": show_music,
            "show_window": show_window,
            "show_heartrate": show_heartrate,
            "show_weather": show_weather,
            "custom_texts": SETTINGS.get("custom_texts", []),
            "time_emoji": SETTINGS.get("time_emoji", "⏰"),
            "song_emoji": SETTINGS.get("song_emoji", "🎶"),
            "window_emoji": SETTINGS.get("window_emoji", "💻"),
            "heartrate_emoji": SETTINGS.get("heartrate_emoji", "❤️"),
            "layout_order": SETTINGS.get("layout_order", ["time", "custom", "vrchat_live", "song", "window", "heartrate", "weather"]),
            "osc_send_interval": SETTINGS.get("osc_send_interval", 3),
            "music_progress": SETTINGS.get("music_progress", True),
            "progress_style": SETTINGS.get("progress_style", "bar"),
            "text_effect": SETTINGS.get("text_effect", "none")
        }
        
        if profiles_manager.get_profile(name):
            if profiles_manager.update_profile(name, settings_to_save):
                return jsonify({"ok": True, "message": "Profile updated"}), 200
            else:
                return jsonify({"error": "Failed to update profile"}), 500
        else:
            if profiles_manager.create_profile(name, settings_to_save):
                return jsonify({"ok": True, "message": "Profile created"}), 200
            else:
                return jsonify({"error": "Failed to create profile"}), 500

    @app.route("/load_profile", methods=["POST"])
    def load_profile():
        global show_time, show_custom, show_music, show_window, show_heartrate, show_weather
        
        data = request.get_json()
        name = data.get("name", "")
        
        if not name:
            return jsonify({"error": "Profile name is required"}), 400
        
        profile = profiles_manager.get_profile(name)
        
        if not profile:
            return jsonify({"error": "Profile not found"}), 404
        
        settings = profile.get("settings", {})
        
        show_time = settings.get("show_time", True)
        show_custom = settings.get("show_custom", True)
        show_music = settings.get("show_music", True)
        show_window = settings.get("show_window", False)
        show_heartrate = settings.get("show_heartrate", False)
        show_weather = settings.get("show_weather", False)
        
        for key, value in settings.items():
            SETTINGS[key] = value
        
        with open(SETTINGS_FILE, "wb") as f:
            f.write(json.dumps(SETTINGS, indent=4, ensure_ascii=False).encode("utf-8"))
        
        return jsonify({"ok": True, "message": "Profile loaded"}), 200

    @app.route("/delete_profile", methods=["POST"])
    def delete_profile():
        data = request.get_json()
        name = data.get("name", "")
        
        if not name:
            return jsonify({"error": "Profile name is required"}), 400
        
        if profiles_manager.delete_profile(name):
            return jsonify({"ok": True, "message": "Profile deleted"}), 200
        else:
            return jsonify({"error": "Failed to delete profile or cannot delete default profile"}), 500

    @app.route("/text_effects", methods=["GET"])
    def get_text_effects():
        effects = text_effects.get_available_effects()
        return jsonify({"effects": effects}), 200

    @app.route("/set_text_effect", methods=["POST"])
    def set_text_effect():
        data = request.get_json()
        effect = data.get("effect", "none")
        
        SETTINGS["text_effect"] = effect
        _persist_settings(label="text_effect")
        
        return jsonify({"ok": True, "effect": effect}), 200

    return app

app = create_app()
