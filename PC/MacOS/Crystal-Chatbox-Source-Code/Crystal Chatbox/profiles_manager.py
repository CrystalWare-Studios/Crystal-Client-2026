import json
import logging
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
    DATA_DIR = os.path.join(BASE_DIR, "Crystal Chatbox Data")
elif "ANDROID_ARGUMENT" in os.environ:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = os.environ.get("ANDROID_PRIVATE", BASE_DIR)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = BASE_DIR

os.makedirs(DATA_DIR, exist_ok=True)

PROFILES_FILE = os.path.join(DATA_DIR, "profiles.json")

DEFAULT_PROFILE = {
    "name": "Default",
    "description": "Default Crystal Client profile.",
    "created_at": None,
    "updated_at": None,
    "settings": {
        "show_time": True,
        "show_custom": True,
        "show_music": True,
        "show_window": False,
        "show_heartrate": False,
        "custom_texts": ["Custom Message Test"],
        "time_emoji": "⏰",
        "song_emoji": "🎶",
        "window_emoji": "💻",
        "heartrate_emoji": "❤️",
        "layout_order": ["time", "custom", "song", "window", "heartrate", "weather", "system_stats", "afk"],
        "osc_send_interval": 3,
        "music_progress": True,
        "progress_style": "bar",
    },
}


def _profile_name(profile: Dict) -> str:
    return str(profile.get("name") or "Unnamed").strip()


def load_profiles() -> List[Dict]:
    try:
        if os.path.exists(PROFILES_FILE):
            with open(PROFILES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception as e:
        logger.error(f"Error loading profiles: {e}")
    return []


def save_profiles(profiles: List[Dict]) -> bool:
    try:
        with open(PROFILES_FILE, "w", encoding="utf-8", newline="\n") as f:
            json.dump(profiles, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error(f"Error saving profiles: {e}")
        return False


def get_profile(profile_name: str) -> Optional[Dict]:
    name = str(profile_name or "").strip()
    for profile in load_profiles():
        if _profile_name(profile) == name:
            return profile
    return None


def create_profile(name: str, settings: Dict, description: str = "", metadata: Optional[Dict] = None) -> bool:
    profiles = load_profiles()
    clean_name = str(name or "").strip()
    if not clean_name:
        return False
    if any(_profile_name(profile) == clean_name for profile in profiles):
        return False
    profile = metadata.copy() if isinstance(metadata, dict) else {}
    profile.update({
        "name": clean_name,
        "description": description,
        "created_at": profile.get("created_at") or datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "settings": settings,
    })
    profiles.append(profile)
    return save_profiles(profiles)


def update_profile(name: str, settings: Dict, description: str = "", metadata: Optional[Dict] = None) -> bool:
    profiles = load_profiles()
    clean_name = str(name or "").strip()
    for profile in profiles:
        if _profile_name(profile) == clean_name:
            if isinstance(metadata, dict):
                profile.update(metadata)
            profile["name"] = clean_name
            if description:
                profile["description"] = description
            profile["settings"] = settings
            profile["updated_at"] = datetime.now().isoformat()
            return save_profiles(profiles)
    return False


def delete_profile(name: str) -> bool:
    clean_name = str(name or "").strip()
    if clean_name.lower() == "default":
        return False
    profiles = load_profiles()
    next_profiles = [profile for profile in profiles if _profile_name(profile) != clean_name]
    if len(next_profiles) == len(profiles):
        return False
    return save_profiles(next_profiles)


def list_profiles() -> List[str]:
    return [_profile_name(profile) for profile in load_profiles()]


def export_profile(name: str) -> Optional[str]:
    profile = get_profile(name)
    if profile:
        return json.dumps(profile, indent=2, ensure_ascii=False)
    return None


def import_profile(profile_json: str) -> bool:
    try:
        profile_data = json.loads(profile_json)
        if not isinstance(profile_data, dict) or "name" not in profile_data or "settings" not in profile_data:
            return False
        profiles = load_profiles()
        name = _profile_name(profile_data)
        for index, profile in enumerate(profiles):
            if _profile_name(profile) == name:
                profiles[index] = profile_data
                return save_profiles(profiles)
        profiles.append(profile_data)
        return save_profiles(profiles)
    except Exception as e:
        logger.error(f"Error importing profile: {e}")
        return False
