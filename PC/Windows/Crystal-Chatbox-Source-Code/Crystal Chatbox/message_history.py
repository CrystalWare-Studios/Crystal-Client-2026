import json
import os
import sys
import time
from collections import deque

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
    DATA_DIR = os.path.join(BASE_DIR, "Crystal Chatbox Data")
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = BASE_DIR

os.makedirs(DATA_DIR, exist_ok=True)

HISTORY_FILE = os.path.join(DATA_DIR, "message_history.json")

MAX_HISTORY = 50

_message_history = deque(maxlen=MAX_HISTORY)
_typed_history = deque(maxlen=20)

def load_history():
    global _message_history, _typed_history
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, "rb") as f:
                data = json.loads(f.read().decode("utf-8"))
                _message_history = deque(data.get("messages", []), maxlen=MAX_HISTORY)
                _typed_history = deque(data.get("typed", []), maxlen=20)
    except Exception as e:
        print(f"[Message History] Error loading: {e}")

def save_history():
    try:
        data = {
            "messages": list(_message_history),
            "typed": list(_typed_history)
        }
        with open(HISTORY_FILE, "wb") as f:
            f.write(json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))
    except Exception as e:
        print(f"[Message History] Error saving: {e}")

def add_sent_message(message, message_type="rotation"):
    if not message or len(message.strip()) == 0:
        return
    
    entry = {
        "text": message[:200],
        "type": message_type,
        "timestamp": time.time()
    }
    _message_history.append(entry)

def add_typed_message(message):
    if not message or len(message.strip()) == 0:
        return
    
    entry = {
        "text": message[:200],
        "timestamp": time.time()
    }
    _typed_history.append(entry)
    save_history()

def get_recent_messages(count=10):
    messages = list(_message_history)
    return messages[-count:] if len(messages) > count else messages

def get_typed_history(count=10):
    messages = list(_typed_history)
    return messages[-count:] if len(messages) > count else messages

def get_message_stats():
    now = time.time()
    hour_ago = now - 3600
    day_ago = now - 86400
    
    all_messages = list(_message_history)
    
    last_hour = sum(1 for m in all_messages if m.get("timestamp", 0) > hour_ago)
    last_day = sum(1 for m in all_messages if m.get("timestamp", 0) > day_ago)
    
    typed_count = len(_typed_history)
    
    return {
        "total": len(all_messages),
        "last_hour": last_hour,
        "last_day": last_day,
        "typed_count": typed_count
    }

def clear_history():
    global _message_history, _typed_history
    _message_history.clear()
    _typed_history.clear()
    save_history()

load_history()
