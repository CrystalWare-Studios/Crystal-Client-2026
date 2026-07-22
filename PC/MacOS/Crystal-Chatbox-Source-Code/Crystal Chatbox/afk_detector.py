import threading
import time

afk_state = {
    "is_afk": False,
    "afk_since": 0,
    "last_activity": 0,
    "afk_message": "",
    "enabled": False
}

_afk_thread = None
_afk_running = False
_afk_lock = threading.Lock()

def update_activity():
    with _afk_lock:
        afk_state["last_activity"] = time.time()
        if afk_state["is_afk"]:
            afk_state["is_afk"] = False
            afk_state["afk_since"] = 0
            print("[AFK] User returned - AFK cleared")

def check_afk(timeout_seconds):
    with _afk_lock:
        if not afk_state["enabled"]:
            return False
        
        current_time = time.time()
        time_since_activity = current_time - afk_state["last_activity"]
        
        if time_since_activity >= timeout_seconds and not afk_state["is_afk"]:
            afk_state["is_afk"] = True
            afk_state["afk_since"] = current_time
            print(f"[AFK] User went AFK after {int(time_since_activity)}s of inactivity")
        
        return afk_state["is_afk"]

def get_afk_duration():
    with _afk_lock:
        if not afk_state["is_afk"] or afk_state["afk_since"] == 0:
            return 0
        return int(time.time() - afk_state["afk_since"])

def format_afk_duration(seconds):
    if seconds < 60:
        return f"{seconds} seconds"
    elif seconds < 3600:
        mins = seconds // 60
        if mins == 1:
            return "1 minute"
        return f"{mins} minutes"
    else:
        hours = seconds // 3600
        mins = (seconds % 3600) // 60
        if hours == 1:
            if mins > 0:
                return f"1 hour {mins} min"
            return "1 hour"
        if mins > 0:
            return f"{hours} hours {mins} min"
        return f"{hours} hours"

def get_afk_message(custom_message="", show_duration=True):
    with _afk_lock:
        if not afk_state["is_afk"]:
            return ""
        
        if afk_state["afk_since"] == 0:
            duration = 0
        else:
            duration = int(time.time() - afk_state["afk_since"])
        
        if show_duration and duration >= 60:
            duration_text = format_afk_duration(duration)
            if custom_message:
                return f"{custom_message} for {duration_text}"
            return f"AFK for {duration_text}"
        else:
            if custom_message:
                return custom_message
            return "AFK"

def set_afk_enabled(enabled):
    with _afk_lock:
        afk_state["enabled"] = enabled
        if enabled:
            afk_state["last_activity"] = time.time()
        else:
            afk_state["is_afk"] = False
            afk_state["afk_since"] = 0
        print(f"[AFK] Detection {'enabled' if enabled else 'disabled'}")

def set_custom_afk_message(message):
    with _afk_lock:
        afk_state["afk_message"] = message

def is_afk():
    with _afk_lock:
        return afk_state["is_afk"]

def get_afk_state():
    with _afk_lock:
        return afk_state.copy()

def get_time_until_afk(timeout_seconds):
    with _afk_lock:
        if not afk_state["enabled"]:
            return -1
        
        if afk_state["is_afk"]:
            return 0
        
        time_since_activity = time.time() - afk_state["last_activity"]
        remaining = timeout_seconds - time_since_activity
        
        return max(0, int(remaining))

def format_countdown(seconds):
    if seconds < 0:
        return "Disabled"
    if seconds == 0:
        return "AFK"
    
    mins = seconds // 60
    secs = seconds % 60
    return f"{mins}:{secs:02d}"
