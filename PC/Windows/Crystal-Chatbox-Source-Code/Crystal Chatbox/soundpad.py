import os
import time
from urllib.parse import quote

from werkzeug.utils import secure_filename, safe_join

from settings import SETTINGS_FILE


DATA_DIR = os.path.dirname(SETTINGS_FILE)
SOUNDPAD_DIR = os.path.join(DATA_DIR, "soundpad")

ALLOWED_AUDIO_EXTENSIONS = {
    ".aac",
    ".flac",
    ".m4a",
    ".mp3",
    ".oga",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
}


def ensure_soundpad_dir():
    os.makedirs(SOUNDPAD_DIR, exist_ok=True)
    return SOUNDPAD_DIR


def is_audio_filename(filename):
    _, ext = os.path.splitext(str(filename or "").lower())
    return ext in ALLOWED_AUDIO_EXTENSIONS


def display_name(filename):
    name, _ = os.path.splitext(os.path.basename(str(filename or "")))
    cleaned = name.replace("_", " ").replace("-", " ").strip()
    return cleaned or "Sound"


def _unique_filename(filename):
    folder = ensure_soundpad_dir()
    safe_name = secure_filename(filename or "")
    if not safe_name:
        safe_name = f"sound_{int(time.time())}.wav"

    stem, ext = os.path.splitext(safe_name)
    if not ext:
        ext = ".wav"
    if ext.lower() not in ALLOWED_AUDIO_EXTENSIONS:
        raise ValueError("Unsupported audio file type")

    candidate = f"{stem}{ext}"
    index = 1
    while os.path.exists(os.path.join(folder, candidate)):
        candidate = f"{stem}_{index}{ext}"
        index += 1
    return candidate


def get_clip_path(filename):
    folder = ensure_soundpad_dir()
    basename = os.path.basename(str(filename or ""))
    if not basename or not is_audio_filename(basename):
        return None
    path = safe_join(folder, basename)
    if not path or not os.path.isfile(path):
        return None
    return path


def list_clips():
    folder = ensure_soundpad_dir()
    clips = []
    for entry in os.scandir(folder):
        if not entry.is_file() or not is_audio_filename(entry.name):
            continue
        stat = entry.stat()
        clips.append(
            {
                "filename": entry.name,
                "name": display_name(entry.name),
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "url": f"/soundpad/file/{quote(entry.name)}",
            }
        )

    clips.sort(key=lambda clip: (clip["name"].lower(), clip["filename"].lower()))
    return clips


def save_uploads(files):
    saved = []
    for upload in files:
        if not upload or not upload.filename:
            continue
        if not is_audio_filename(upload.filename):
            continue
        filename = _unique_filename(upload.filename)
        path = os.path.join(ensure_soundpad_dir(), filename)
        upload.save(path)
        saved.append(filename)
    return saved


def delete_clip(filename):
    path = get_clip_path(filename)
    if not path:
        return False
    os.remove(path)
    return True
