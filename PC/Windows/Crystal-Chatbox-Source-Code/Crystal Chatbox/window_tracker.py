import threading
import time
import sys
import os
from settings import SETTINGS

window_state = {
    "window_title": "",
    "app_name": ""
}
window_lock = threading.Lock()

def get_window_state():
    with window_lock:
        return window_state.copy()

def _apply_name_alias(app_name, default_name):
    aliases = SETTINGS.get("window_name_aliases", {})
    if not aliases:
        return default_name
    candidates = [
        (app_name or "").strip().lower(),
        (app_name or "").strip().lower().replace(".exe", ""),
        (default_name or "").strip().lower(),
    ]
    lookup = {str(k).strip().lower(): v for k, v in aliases.items()}
    for candidate in candidates:
        if candidate and candidate in lookup:
            return lookup[candidate]
    return default_name


def sanitize_app_name(app_name, title):
    if not app_name and not title:
        return "Unknown"

    default_name = _sanitize_app_name_default(app_name, title)
    return _apply_name_alias(app_name, default_name)


def _sanitize_app_name_default(app_name, title):
    app_lower = (app_name or "").lower()
    title_lower = (title or "").lower()

    if "crystal chatbox" in title_lower or "crystal client" in title_lower or "crystal chatbox" in app_lower or "crystal client" in app_lower:
        return "Crystal Chatbox"

    if "youtube" in title_lower:
        video_title = title or app_name or ""
        for suffix in (
            " - YouTube - Google Chrome",
            " - YouTube - Microsoft Edge",
            " - YouTube - Mozilla Firefox",
            " - YouTube - Brave",
            " - YouTube",
        ):
            if video_title.endswith(suffix):
                video_title = video_title[: -len(suffix)]
                break
        video_title = video_title.strip()
        if video_title and video_title.lower() not in {"youtube", "new tab"}:
            max_len = SETTINGS.get("window_title_max_length", 50)
            if len(video_title) > max_len:
                video_title = video_title[:max_len] + "..."
            return f"YouTube: {video_title}"
        return "YouTube"

    if "code.exe" in app_lower or app_lower in {"code", "vscode"} or "visual studio code" in app_lower:
        return "VS Code"

    if "cursor.exe" in app_lower or app_lower == "cursor":
        return "Cursor"
    
    if "discord" in app_lower or "discord" in title_lower:
        return "Discord"
    
    if "spotify.exe" in app_lower or app_lower == "spotify":
        return "Spotify"
    
    if "terminal" in app_lower or "iterm" in app_lower or "cmd" in app_lower or "powershell" in app_lower or "konsole" in app_lower:
        if title and ".py" in title_lower:
            file_parts = title.split(" - ")
            file_name = file_parts[-1] if len(file_parts) > 1 else title.split()[-1] if title.split() else title
            return f"{file_name} - Terminal"
        return "Terminal"
    
    if "code" in app_lower or "vscode" in app_lower or "visual studio code" in app_lower:
        return "VS Code"
    
    if "chrome" in app_lower or "firefox" in app_lower or "safari" in app_lower or "edge" in app_lower or "brave" in app_lower:
        use_title = title or app_name or ""
        clean_title = use_title.replace(" - Google Chrome", "").replace(" - Mozilla Firefox", "").replace(" - Safari", "").replace(" - Microsoft Edge", "").replace(" - Brave", "").replace(" — Mozilla Firefox", "").strip()
        if clean_title and clean_title.lower() not in ["google chrome", "mozilla firefox", "safari", "microsoft edge", "brave", "new tab"]:
            max_len = SETTINGS.get("window_title_max_length", 50)
            if len(clean_title) > max_len:
                return clean_title[:max_len] + "..."
            return clean_title
        browser_name = "Chrome" if "chrome" in app_lower else "Firefox" if "firefox" in app_lower else "Safari" if "safari" in app_lower else "Edge" if "edge" in app_lower else "Brave" if "brave" in app_lower else "Browser"
        return browser_name
    
    if app_lower in {"python.exe", "pythonw.exe", "python", "pythonw"} and title:
        max_len = SETTINGS.get("window_title_max_length", 50)
        clean_title = title.strip()
        if len(clean_title) > max_len:
            clean_title = clean_title[:max_len] + "..."
        return clean_title
    
    if " - " in str(app_name):
        parts = app_name.split(" - ")
        return parts[-1] if len(parts) > 1 else app_name
    
    return app_name or title or "Unknown"

def _windows_process_name_from_pid(pid):
    try:
        import psutil
        process = psutil.Process(pid)
        return process.name() or os.path.basename(process.exe()) or ""
    except Exception:
        return ""

def get_active_window_windows():
    if sys.platform != "win32":
        return None

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None

        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value or ""

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_name = _windows_process_name_from_pid(pid.value)

        clean_name = sanitize_app_name(process_name, title)
        return {
            "title": title,
            "app": clean_name
        }
    except Exception:
        return None

def get_active_window_cross_platform():
    try:
        import pywinctl as pwc
        active_window = pwc.getActiveWindow()
        
        if active_window:
            title = active_window.title
            app_name = title
            
            try:
                if hasattr(active_window, 'app'):
                    app_info = active_window.app
                    if app_info and hasattr(app_info, 'name'):
                        app_name = app_info.name or title
            except AttributeError:
                pass
            
            clean_name = sanitize_app_name(app_name, title)
            
            return {
                "title": title or "",
                "app": clean_name
            }
        return None
    except Exception as e:
        return None

def get_active_window_macos_fallback():
    try:
        import subprocess
        
        app_name = subprocess.check_output([
            "osascript",
            "-e",
            'tell application "System Events" to get name of first application process whose frontmost is true'
        ]).decode("utf-8").strip()
        
        title = None
        if app_name == "Google Chrome":
            try:
                title = subprocess.check_output([
                    "osascript",
                    "-e",
                    'tell application "Google Chrome" to get title of active tab of front window'
                ]).decode("utf-8").strip()
            except:
                pass
        elif app_name == "Safari":
            try:
                title = subprocess.check_output([
                    "osascript",
                    "-e",
                    'tell application "Safari" to get name of front document'
                ]).decode("utf-8").strip()
            except:
                pass
        elif app_name == "Firefox":
            try:
                title = subprocess.check_output([
                    "osascript",
                    "-e",
                    'tell application "Firefox" to get name of front window'
                ]).decode("utf-8").strip()
            except:
                pass
        
        clean_name = sanitize_app_name(app_name, title)
        
        if title:
            return {"title": title, "app": clean_name}
        else:
            return {"title": app_name, "app": clean_name}
    except Exception:
        return None

def start_window_tracker(interval=2):
    def tracker():
        global window_state
        
        platform = sys.platform
        print(f"[Window Tracker] Thread started (Platform: {platform})")
        last_error_time = 0
        use_fallback = False

        while True:
            try:
                if not SETTINGS.get("window_tracking_enabled", False):
                    time.sleep(interval)
                    continue

                window_info = None
                
                if platform == "win32":
                    window_info = get_active_window_windows()

                if window_info is None and not use_fallback:
                    window_info = get_active_window_cross_platform()
                
                if window_info is None and platform == "darwin":
                    window_info = get_active_window_macos_fallback()
                    if window_info:
                        use_fallback = True

                with window_lock:
                    if window_info:
                        window_state["window_title"] = window_info.get("title", "")
                        window_state["app_name"] = window_info.get("app", "Unknown")
                    else:
                        window_state["window_title"] = ""
                        window_state["app_name"] = "Unknown"

            except Exception as e:
                current_time = time.time()
                if current_time - last_error_time > 60:
                    print(f"[Window Tracker ERROR] {e}")
                    last_error_time = current_time
                with window_lock:
                    window_state["window_title"] = ""
                    window_state["app_name"] = "Unknown"

            time.sleep(interval)

    threading.Thread(target=tracker, daemon=True).start()
