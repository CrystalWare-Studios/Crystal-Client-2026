import json
import os
import sys

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
    DATA_DIR = os.path.join(BASE_DIR, "Crystal Chatbox Data")
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = BASE_DIR

os.makedirs(DATA_DIR, exist_ok=True)

PHRASES_FILE = os.path.join(DATA_DIR, "quick_phrases.json")

DEFAULT_PHRASES = [
    {"text": "Hello! 👋", "emoji": "👋", "category": "greetings"},
    {"text": "Be right back!", "emoji": "🔙", "category": "status"},
    {"text": "AFK for a bit", "emoji": "💤", "category": "status"},
    {"text": "Following you!", "emoji": "🚶", "category": "social"},
    {"text": "Nice avatar!", "emoji": "✨", "category": "compliments"},
    {"text": "Let's go!", "emoji": "🎉", "category": "social"},
    {"text": "One moment please", "emoji": "⏳", "category": "status"},
    {"text": "Thank you!", "emoji": "💖", "category": "social"},
    {"text": "See you later!", "emoji": "👋", "category": "greetings"},
    {"text": "Having fun!", "emoji": "😄", "category": "mood"},
    {"text": "Join me!", "emoji": "🎮", "category": "social"},
    {"text": "Good vibes only", "emoji": "✌️", "category": "mood"}
]

def load_phrases():
    try:
        if os.path.exists(PHRASES_FILE):
            with open(PHRASES_FILE, "rb") as f:
                return json.loads(f.read().decode("utf-8"))
    except Exception as e:
        print(f"[Quick Phrases] Error loading: {e}")
    return DEFAULT_PHRASES.copy()

def save_phrases(phrases):
    try:
        with open(PHRASES_FILE, "wb") as f:
            f.write(json.dumps(phrases, indent=2, ensure_ascii=False).encode("utf-8"))
        return True
    except Exception as e:
        print(f"[Quick Phrases] Error saving: {e}")
        return False

def get_phrases():
    return load_phrases()

def add_phrase(text, emoji="", category="custom"):
    phrases = load_phrases()
    phrases.append({
        "text": text,
        "emoji": emoji,
        "category": category
    })
    return save_phrases(phrases)

def remove_phrase(index):
    phrases = load_phrases()
    if 0 <= index < len(phrases):
        phrases.pop(index)
        return save_phrases(phrases)
    return False

def update_phrase(index, text, emoji="", category="custom"):
    phrases = load_phrases()
    if 0 <= index < len(phrases):
        phrases[index] = {
            "text": text,
            "emoji": emoji,
            "category": category
        }
        return save_phrases(phrases)
    return False

def reset_to_defaults():
    return save_phrases(DEFAULT_PHRASES.copy())

def get_phrases_by_category(category):
    phrases = load_phrases()
    return [p for p in phrases if p.get("category") == category]

def get_categories():
    phrases = load_phrases()
    categories = set()
    for p in phrases:
        if p.get("category"):
            categories.add(p["category"])
    return sorted(list(categories))
