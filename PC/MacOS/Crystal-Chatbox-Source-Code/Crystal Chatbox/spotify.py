import asyncio
import base64
import os
import sys
import threading
import time

import requests

from settings import SETTINGS, save_settings

IS_WINDOWS = sys.platform == "win32"
IS_ANDROID = "ANDROID_ARGUMENT" in os.environ

try:
    if IS_WINDOWS and not IS_ANDROID:
        from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as _MediaManager
        from winsdk.windows.storage.streams import Buffer as _Buffer, InputStreamOptions as _InputStreamOptions
        WINDOWS_MEDIA_AVAILABLE = True
    else:
        WINDOWS_MEDIA_AVAILABLE = False
except Exception:
    WINDOWS_MEDIA_AVAILABLE = False

SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"
SPOTIFY_API_URL = "https://api.spotify.com/v1/me/player"
SPOTIFY_AVAILABLE = True

LASTFM_API_URL = "https://ws.audioscrobbler.com/2.0/"
LASTFM_API_KEY = "825b76dcc029242543303ecd848cf6c4"


spotify_state = {
    "song_text": "",
    "song_pos": 0,
    "song_dur": 0,
    "album_art": "",
    "status": "not_configured",
    "configured": False,
    "available": SPOTIFY_AVAILABLE,
    "source": "windows_media" if WINDOWS_MEDIA_AVAILABLE else "lastfm",
    "last_error": "",
    "last_error_at": 0,
}

spotify_lock = threading.Lock()
sp = None
force_reinit_event = threading.Event()
tracker_started = False
tracker_lock = threading.Lock()


def _now_playing_method():
    if IS_ANDROID:
        return "lastfm"
    return SETTINGS.get("now_playing_method", "spotify_api")


def _current_source():
    if WINDOWS_MEDIA_AVAILABLE:
        return "windows_media"
    return "lastfm" if _now_playing_method() == "lastfm" else "spotify_api"


def get_spotify_state():
    with spotify_lock:
        state = spotify_state.copy()
    state["source"] = _current_source()
    return state


def _configured():
    if WINDOWS_MEDIA_AVAILABLE:
        return True
    if _now_playing_method() == "lastfm":
        return bool(SETTINGS.get("lastfm_username", "").strip())
    return bool(
        SETTINGS.get("spotify_refresh_token", "").strip()
        and SETTINGS.get("spotify_client_id", "").strip()
        and SETTINGS.get("spotify_client_secret", "").strip()
    )


def _friendly_error(error):
    text = str(error or "")
    lower = text.lower()
    if "failed to resolve" in lower or "nameresolutionerror" in lower or "getaddrinfo failed" in lower:
        return "Could not reach the server. Check your internet connection or DNS, then try again."
    if "user not found" in lower:
        return "Last.fm could not find that username. Double-check it under Integrations - Now Playing Setup."
    if "invalid api key" in lower:
        return "Last.fm rejected the API key baked into this build. Contact the app developer."
    if "403" in lower or "forbidden" in lower:
        return "Spotify blocked this request (403). Your Client ID/Secret may be wrong, or this Spotify account isn't added as a user on your Spotify app yet."
    if "400" in lower or "invalid_client" in lower or "invalid_grant" in lower:
        return "Spotify rejected your Client ID/Secret. Double-check them under Integrations - Now Playing Setup."
    if "401" in lower or "unauthorized" in lower or "token" in lower:
        return "Spotify needs you to reconnect your account."
    if "timeout" in lower:
        return "The request took too long to respond. It will retry automatically."
    return text[:240] or "Now Playing is unavailable."


def _set_state(**updates):
    with spotify_lock:
        spotify_state.update(updates)
        spotify_state["configured"] = _configured()
        spotify_state["available"] = SPOTIFY_AVAILABLE
        if updates.get("last_error"):
            spotify_state["last_error_at"] = time.time()


def force_reinit():
    global sp
    sp = None
    force_reinit_event.set()
    print("[Now Playing] Re-initialization requested.")


async def _read_windows_media_session():
    manager = await _MediaManager.request_async()
    session = manager.get_current_session()
    if session is None:
        return None

    info = await session.try_get_media_properties_async()
    playback = session.get_playback_info()
    timeline = session.get_timeline_properties()

    is_playing = playback.playback_status == 4
    title = (info.title or "").strip()
    artist = (info.artist or "").strip()
    song_text = f"{title} - {artist}".strip(" -") if is_playing and title else ""

    album_art = ""
    if is_playing and info.thumbnail is not None:
        try:
            stream = await info.thumbnail.open_read_async()
            size = stream.size
            buf = _Buffer(size)
            await stream.read_async(buf, size, _InputStreamOptions.READ_AHEAD)
            data = bytes(buf)
            mime = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
            album_art = f"data:{mime};base64,{base64.b64encode(data).decode()}"
        except Exception:
            album_art = ""

    return {
        "song_text": song_text,
        "song_pos": int(timeline.position.total_seconds()) if is_playing else 0,
        "song_dur": int(timeline.end_time.total_seconds()) if is_playing else 0,
        "album_art": album_art,
    }


def _tracker_loop_windows(interval):
    print("[Now Playing] Reading from Windows Media (any player), no setup needed.")
    while True:
        try:
            result = asyncio.run(_read_windows_media_session())
            if result is None:
                _set_state(status="active", last_error="", song_text="", song_pos=0, song_dur=0, album_art="")
            else:
                _set_state(status="active", last_error="", **result)
        except Exception as exc:
            _set_state(status="error", last_error=str(exc).strip() or type(exc).__name__)
        time.sleep(interval)


_lastfm_track_state = {"key": None, "started_at": 0.0, "duration": 0}


def _lastfm_track_duration(artist, title):
    try:
        response = requests.get(
            LASTFM_API_URL,
            params={
                "method": "track.getInfo",
                "artist": artist,
                "track": title,
                "api_key": LASTFM_API_KEY,
                "format": "json",
            },
            timeout=15,
        )
        payload = response.json()
        return int(payload.get("track", {}).get("duration", 0) or 0) // 1000
    except Exception:
        return 0


def _read_lastfm_now_playing():
    username = SETTINGS.get("lastfm_username", "").strip()
    if not username:
        return None

    response = requests.get(
        LASTFM_API_URL,
        params={
            "method": "user.getrecenttracks",
            "user": username,
            "api_key": LASTFM_API_KEY,
            "format": "json",
            "limit": 1,
        },
        timeout=15,
    )
    payload = response.json()
    if isinstance(payload, dict) and "error" in payload:
        raise RuntimeError(payload.get("message", "Last.fm request failed."))
    response.raise_for_status()

    tracks = payload.get("recenttracks", {}).get("track", [])
    if not tracks:
        _lastfm_track_state["key"] = None
        return None

    track = tracks[0]
    if not track.get("@attr", {}).get("nowplaying"):
        _lastfm_track_state["key"] = None
        return None

    title = track.get("name", "")
    artist = track.get("artist", {}).get("#text", "")
    album_art = ""
    for image in track.get("image", []):
        if image.get("size") == "extralarge" and image.get("#text"):
            album_art = image["#text"]

    key = (artist, title)
    if _lastfm_track_state["key"] != key:
        _lastfm_track_state["key"] = key
        _lastfm_track_state["started_at"] = time.time()
        _lastfm_track_state["duration"] = _lastfm_track_duration(artist, title)

    duration = _lastfm_track_state["duration"]
    position = int(time.time() - _lastfm_track_state["started_at"])
    if duration > 0:
        position = min(position, duration)

    return {
        "song_text": f"{title} - {artist}".strip(" -"),
        "song_pos": position,
        "song_dur": duration,
        "album_art": album_art,
    }


def init_spotify_web():
    global sp
    refresh_token = SETTINGS.get("spotify_refresh_token", "").strip()
    client_id = SETTINGS.get("spotify_client_id", "").strip()
    client_secret = SETTINGS.get("spotify_client_secret", "").strip()

    if not client_id or not client_secret:
        sp = None
        _set_state(
            status="not_configured",
            last_error="Add your Spotify Client ID and Client Secret, then click Connect Spotify.",
            song_text="",
            song_pos=0,
            song_dur=0,
            album_art="",
        )
        print("[Spotify] Waiting for Client ID/Secret.")
        return

    if not refresh_token:
        sp = None
        _set_state(
            status="not_configured",
            last_error="Click Connect Spotify to authorize playback access.",
            song_text="",
            song_pos=0,
            song_dur=0,
            album_art="",
        )
        print("[Spotify] Waiting for user authorization.")
        return

    try:
        auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        response = requests.post(
            SPOTIFY_TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            headers={"Authorization": f"Basic {auth}"},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        access_token = payload.get("access_token", "")
        if not access_token:
            raise RuntimeError("Spotify did not return an access token.")
        sp = access_token
        rotated_refresh_token = payload.get("refresh_token", "").strip()
        if rotated_refresh_token and rotated_refresh_token != refresh_token:
            SETTINGS["spotify_refresh_token"] = rotated_refresh_token
            save_settings(SETTINGS)
        _set_state(status="connected", last_error="")
        print("[Spotify] Connected.")
    except Exception as exc:
        friendly = _friendly_error(exc)
        sp = None
        _set_state(status="error", last_error=friendly, song_text="", song_pos=0, song_dur=0, album_art="")
        print(f"[Spotify] {friendly}")


def _update_playback(current):
    with spotify_lock:
        if current and current.get("is_playing") and current.get("item"):
            item = current["item"]
            artists = ", ".join(artist.get("name", "") for artist in item.get("artists", []))
            track_name = item.get("name", "Unknown")
            spotify_state["song_text"] = f"{track_name} - {artists}".strip(" -")
            spotify_state["song_pos"] = current.get("progress_ms", 0) // 1000
            spotify_state["song_dur"] = item.get("duration_ms", 0) // 1000
            images = item.get("album", {}).get("images", [])
            spotify_state["album_art"] = images[0].get("url", "") if images else ""
        else:
            spotify_state["song_text"] = ""
            spotify_state["song_pos"] = 0
            spotify_state["song_dur"] = 0
            spotify_state["album_art"] = ""
        spotify_state["status"] = "connected"
        spotify_state["last_error"] = ""
        spotify_state["configured"] = _configured()
        spotify_state["available"] = SPOTIFY_AVAILABLE


def _get_current_playback(access_token):
    response = requests.get(
        SPOTIFY_API_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    if response.status_code == 204:
        return None
    response.raise_for_status()
    return response.json()


def _tracker_loop_platform(interval):
    global sp
    print("[Now Playing Tracker] Thread started")
    last_error_time = 0.0
    last_init_attempt = 0.0
    logged_waiting = False

    while True:
        if _now_playing_method() == "lastfm":
            try:
                result = _read_lastfm_now_playing()
                if result is None:
                    _set_state(status="active", last_error="", song_text="", song_pos=0, song_dur=0, album_art="")
                else:
                    _set_state(status="active", last_error="", **result)
            except Exception as exc:
                friendly = _friendly_error(exc)
                if time.time() - last_error_time > 60:
                    print(f"[Now Playing Tracker] {friendly}")
                    last_error_time = time.time()
                _set_state(status="error", last_error=friendly, song_text="", song_pos=0, song_dur=0, album_art="")
            time.sleep(max(8, interval))
            continue

        try:
            if force_reinit_event.is_set():
                force_reinit_event.clear()
                last_init_attempt = 0.0
                sp = None

            if sp is None:
                now = time.time()
                retry_seconds = 60 if spotify_state.get("status") == "error" else 10
                if _configured() and (last_init_attempt == 0.0 or now - last_init_attempt >= retry_seconds):
                    print("[Spotify Tracker] Attempting initialization...")
                    init_spotify_web()
                    last_init_attempt = now
                if sp is None:
                    if not logged_waiting:
                        print("[Spotify Tracker] Waiting for setup or connectivity.")
                        logged_waiting = True
                    time.sleep(max(5, interval))
                    continue

            logged_waiting = False
            try:
                current = _get_current_playback(sp)
            except Exception as exc:
                if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code == 401:
                    friendly = "Spotify needs you to reconnect your account."
                else:
                    friendly = _friendly_error(exc)
                if time.time() - last_error_time > 60:
                    print(f"[Spotify Tracker] {friendly}")
                    last_error_time = time.time()
                _set_state(status="error", last_error=friendly, song_text="", song_pos=0, song_dur=0, album_art="")
                if "reconnect" in friendly.lower():
                    sp = None
                time.sleep(max(5, interval))
                continue

            _update_playback(current)
            time.sleep(interval)
        except Exception as exc:
            friendly = _friendly_error(exc)
            if time.time() - last_error_time > 60:
                print(f"[Spotify Tracker] {friendly}")
                last_error_time = time.time()
            _set_state(status="error", last_error=friendly)
            time.sleep(max(5, interval))


def start_spotify_tracker(interval=1):
    global tracker_started
    with tracker_lock:
        if tracker_started:
            return
        tracker_started = True

    target = _tracker_loop_windows if WINDOWS_MEDIA_AVAILABLE else _tracker_loop_platform
    threading.Thread(target=target, args=(max(1, interval),), daemon=True).start()
