
import time
import threading
from collections import Counter, deque

_lock = threading.Lock()
_session_start = time.time()
_song_log = deque(maxlen=500)
_last_song_text = ""
_message_count = 0


def note_song(song_text):
    global _last_song_text
    song_text = str(song_text or "").strip()
    if not song_text or song_text == _last_song_text:
        return
    with _lock:
        _last_song_text = song_text
        _song_log.append({"song": song_text, "timestamp": time.time()})


def note_message_sent():
    global _message_count
    with _lock:
        _message_count += 1


def format_duration(seconds):
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def get_top_songs(limit=5):
    with _lock:
        counter = Counter(entry["song"] for entry in _song_log)
    return [{"song": song, "plays": plays} for song, plays in counter.most_common(limit)]


def get_insights():
    uptime_seconds = time.time() - _session_start
    with _lock:
        message_count = _message_count
        unique_songs = len({entry["song"] for entry in _song_log})
    return {
        "session_started_at": _session_start,
        "uptime_seconds": int(uptime_seconds),
        "uptime_text": format_duration(uptime_seconds),
        "messages_sent_session": message_count,
        "unique_songs_played": unique_songs,
        "top_songs": get_top_songs(5),
    }


def reset():
    global _session_start, _last_song_text, _message_count
    with _lock:
        _session_start = time.time()
        _last_song_text = ""
        _message_count = 0
        _song_log.clear()
