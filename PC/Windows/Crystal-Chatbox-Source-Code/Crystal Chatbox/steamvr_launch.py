import json
import os
import sys

from settings import DATA_DIR

IS_WINDOWS = sys.platform == "win32"

try:
    import openvr
    OPENVR_AVAILABLE = True
except Exception:
    openvr = None
    OPENVR_AVAILABLE = False

APP_KEY = "studio.crystalware.crystalclient"
MANIFEST_PATH = os.path.join(DATA_DIR, "crystalclient.vrmanifest")


def is_supported():
    return IS_WINDOWS and OPENVR_AVAILABLE and getattr(sys, "frozen", False)


def _write_manifest(auto_launch):
    manifest = {
        "source": "builtin",
        "applications": [
            {
                "app_key": APP_KEY,
                "launch_type": "binary",
                "binary_path_windows": sys.executable,
                "is_dashboard_overlay": False,
                "auto_launch": bool(auto_launch),
                "strings": {
                    "en_us": {
                        "name": "Crystal Client",
                        "description": "VRChat OSC chatbox companion"
                    }
                }
            }
        ]
    }
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=4)
    return MANIFEST_PATH


def register(auto_launch=True):
    if not is_supported():
        raise RuntimeError("SteamVR auto-launch needs the packaged Windows build with SteamVR installed.")
    manifest_path = _write_manifest(auto_launch)
    apps = openvr.VRApplications()
    apps.addApplicationManifest(manifest_path, False)
    return True


def unregister():
    if not OPENVR_AVAILABLE:
        return False
    if not os.path.exists(MANIFEST_PATH):
        return False
    apps = openvr.VRApplications()
    apps.removeApplicationManifest(MANIFEST_PATH)
    return True


def is_registered():
    if not OPENVR_AVAILABLE:
        return False
    try:
        apps = openvr.VRApplications()
        return bool(apps.isApplicationInstalled(APP_KEY))
    except Exception:
        return False
