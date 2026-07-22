import threading
import time
import requests
import json
from collections import deque
from settings import SETTINGS

heart_rate_state = {
    "bpm": 0,
    "is_connected": False,
    "last_update": None
}

hr_stats = {
    "history": deque(maxlen=600),
    "session_min": 999,
    "session_max": 0,
    "session_avg": 0,
    "session_samples": 0,
    "trend": "stable",
    "trend_history": deque(maxlen=10)
}

heart_rate_lock = threading.Lock()
hr_stats_lock = threading.Lock()

hyperate_ws = None
hyperate_ws_thread = None
hyperate_last_hr = 0
hyperate_connected = False

def get_heart_rate_state():
    with heart_rate_lock:
        return heart_rate_state.copy()

def fetch_from_pulsoid():
    token = SETTINGS.get("heart_rate_pulsoid_token", "").strip()
    if not token:
        return None
    
    try:
        headers = {"Authorization": f"Bearer {token}"}
        response = requests.get(
            "https://dev.pulsoid.net/api/v1/data/heart_rate/latest",
            headers=headers,
            timeout=5
        )
        
        if response.status_code == 200:
            data = response.json()
            hr_data = data.get("data", {})
            return hr_data.get("heart_rate") or hr_data.get("heartRate", 0)
        else:
            print(f"[Heart Rate] Pulsoid API error: {response.status_code}")
            return None
    except Exception as e:
        print(f"[Heart Rate] Pulsoid fetch error: {e}")
        return None

def start_hyperate_websocket(session_id):
    global hyperate_ws, hyperate_ws_thread, hyperate_last_hr, hyperate_connected
    
    if hyperate_ws_thread and hyperate_ws_thread.is_alive():
        return
    
    def ws_thread():
        global hyperate_ws, hyperate_last_hr, hyperate_connected
        
        try:
            import websocket
        except ImportError:
            print("[Heart Rate] websocket-client not installed, HypeRate unavailable")
            return
        
        def on_message(ws, message):
            global hyperate_last_hr, hyperate_connected
            try:
                data = json.loads(message)
                if data.get("event") == "hr_update":
                    payload = data.get("payload", {})
                    hr = payload.get("hr", 0)
                    if hr > 0:
                        hyperate_last_hr = hr
                        hyperate_connected = True
                elif data.get("event") == "phx_reply":
                    if data.get("payload", {}).get("status") == "ok":
                        hyperate_connected = True
                        print(f"[Heart Rate] HypeRate WebSocket connected to session: {session_id}")
            except Exception as e:
                print(f"[Heart Rate] HypeRate message parse error: {e}")
        
        def on_error(ws, error):
            global hyperate_connected, hyperate_last_hr
            hyperate_connected = False
            hyperate_last_hr = 0
            print(f"[Heart Rate] HypeRate WebSocket error: {error}")
        
        def on_close(ws, close_status_code, close_msg):
            global hyperate_connected, hyperate_last_hr
            hyperate_connected = False
            hyperate_last_hr = 0
            print(f"[Heart Rate] HypeRate WebSocket closed")
        
        def on_open(ws):
            join_msg = json.dumps({
                "topic": f"hr:{session_id}",
                "event": "phx_join",
                "payload": {},
                "ref": 0
            })
            ws.send(join_msg)
            
            def send_heartbeat():
                ref = 1
                while True:
                    time.sleep(30)
                    try:
                        heartbeat = json.dumps({
                            "topic": "phoenix",
                            "event": "heartbeat",
                            "payload": {},
                            "ref": ref
                        })
                        ws.send(heartbeat)
                        ref += 1
                    except:
                        break
            
            threading.Thread(target=send_heartbeat, daemon=True).start()
        
        while SETTINGS.get("heart_rate_enabled", False) and SETTINGS.get("heart_rate_source") == "hyperate":
            try:
                hyperate_ws = websocket.WebSocketApp(
                    "wss://app.hyperate.io/socket/websocket?vsn=2.0.0",
                    on_open=on_open,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close
                )
                hyperate_ws.run_forever()
            except Exception as e:
                print(f"[Heart Rate] HypeRate WebSocket connection error: {e}")
            
            hyperate_connected = False
            time.sleep(5)
    
    hyperate_ws_thread = threading.Thread(target=ws_thread, daemon=True)
    hyperate_ws_thread.start()

def fetch_from_hyperate():
    global hyperate_last_hr, hyperate_connected
    
    session_id = SETTINGS.get("heart_rate_hyperate_id", "").strip()
    if not session_id:
        return None
    
    start_hyperate_websocket(session_id)
    
    if hyperate_connected and hyperate_last_hr > 0:
        return hyperate_last_hr
    
    return None

def fetch_from_custom_api():
    api_url = SETTINGS.get("heart_rate_custom_api", "").strip()
    if not api_url:
        return None
    
    try:
        response = requests.get(api_url, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            return data.get("bpm") or data.get("heart_rate") or data.get("hr", 0)
        else:
            print(f"[Heart Rate] Custom API error: {response.status_code}")
            return None
    except Exception as e:
        print(f"[Heart Rate] Custom API fetch error: {e}")
        return None

def start_heart_rate_tracker(interval=5):
    def tracker():
        global heart_rate_state
        print("[Heart Rate Tracker] Thread started")
        last_error_time = 0
        
        while True:
            try:
                if not SETTINGS.get("heart_rate_enabled", False) and not hr_simulator_state["enabled"]:
                    time.sleep(interval)
                    continue
                
                bpm = fetch_heart_rate()
                
                with heart_rate_lock:
                    if bpm is not None and bpm > 0:
                        heart_rate_state["bpm"] = int(bpm)
                        heart_rate_state["is_connected"] = True
                        heart_rate_state["last_update"] = time.time()
                        update_hr_stats(int(bpm))
                    else:
                        if heart_rate_state["last_update"] and (time.time() - heart_rate_state["last_update"]) > 30:
                            heart_rate_state["is_connected"] = False
                            heart_rate_state["bpm"] = 0
                
                time.sleep(interval)
            except Exception as e:
                current_time = time.time()
                if current_time - last_error_time > 60:
                    print(f"[Heart Rate Tracker ERROR] {e}")
                    last_error_time = current_time
                time.sleep(interval)
    
    threading.Thread(target=tracker, daemon=True).start()

def update_hr_stats(bpm):
    with hr_stats_lock:
        if bpm <= 0:
            return
        
        hr_stats["history"].append({"bpm": bpm, "time": time.time()})
        hr_stats["trend_history"].append(bpm)
        
        if bpm < hr_stats["session_min"]:
            hr_stats["session_min"] = bpm
        if bpm > hr_stats["session_max"]:
            hr_stats["session_max"] = bpm
        
        hr_stats["session_samples"] += 1
        old_avg = hr_stats["session_avg"]
        hr_stats["session_avg"] = old_avg + (bpm - old_avg) / hr_stats["session_samples"]
        
        if len(hr_stats["trend_history"]) >= 5:
            recent = list(hr_stats["trend_history"])
            first_half = sum(recent[:len(recent)//2]) / (len(recent)//2)
            second_half = sum(recent[len(recent)//2:]) / (len(recent) - len(recent)//2)
            diff = second_half - first_half
            
            if diff > 5:
                hr_stats["trend"] = "rising"
            elif diff < -5:
                hr_stats["trend"] = "falling"
            else:
                hr_stats["trend"] = "stable"

def get_hr_stats():
    with hr_stats_lock:
        return {
            "session_min": hr_stats["session_min"] if hr_stats["session_min"] < 999 else 0,
            "session_max": hr_stats["session_max"],
            "session_avg": round(hr_stats["session_avg"], 1),
            "trend": hr_stats["trend"],
            "samples": hr_stats["session_samples"]
        }

def reset_hr_stats():
    with hr_stats_lock:
        hr_stats["history"].clear()
        hr_stats["session_min"] = 999
        hr_stats["session_max"] = 0
        hr_stats["session_avg"] = 0
        hr_stats["session_samples"] = 0
        hr_stats["trend"] = "stable"
        hr_stats["trend_history"].clear()

def get_trend_icon():
    with hr_stats_lock:
        trend = hr_stats["trend"]
        if trend == "rising":
            return "📈"
        elif trend == "falling":
            return "📉"
        return ""

def format_hr_with_stats(bpm, show_trend=True, show_stats=False):
    if bpm <= 0:
        return ""
    
    parts = [f"❤️ {bpm} BPM"]
    
    if show_trend:
        trend_icon = get_trend_icon()
        if trend_icon:
            parts[0] = f"❤️ {bpm} BPM {trend_icon}"
    
    if show_stats:
        stats = get_hr_stats()
        if stats["samples"] >= 5:
            parts.append(f"Avg: {stats['session_avg']}")
    
    return " ".join(parts)

hr_simulator_state = {
    "enabled": False,
    "base_bpm": 72,
    "variation": 5
}

def set_simulator_enabled(enabled):
    global hr_simulator_state
    hr_simulator_state["enabled"] = enabled
    if enabled:
        print("[Heart Rate] Simulator ENABLED - generating fake data for testing")
    else:
        print("[Heart Rate] Simulator DISABLED")

def is_simulator_enabled():
    return hr_simulator_state["enabled"]

def get_simulated_hr():
    import random
    import math
    
    base = hr_simulator_state["base_bpm"]
    variation = hr_simulator_state["variation"]
    
    wave = math.sin(time.time() / 10) * (variation / 2)
    noise = random.uniform(-variation/2, variation/2)
    
    return int(base + wave + noise)

def fetch_heart_rate():
    if hr_simulator_state["enabled"]:
        return get_simulated_hr()
    
    source = SETTINGS.get("heart_rate_source", "pulsoid")
    
    if source == "pulsoid":
        return fetch_from_pulsoid()
    elif source == "hyperate":
        return fetch_from_hyperate()
    elif source == "custom":
        return fetch_from_custom_api()
    
    return None
