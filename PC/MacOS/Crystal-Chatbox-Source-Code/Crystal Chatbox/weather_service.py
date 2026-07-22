import requests
import logging
from datetime import datetime, timedelta
import threading
import time

logger = logging.getLogger(__name__)


weather_state = {
    "temp_c": None,
    "temp_f": None,
    "condition": None,
    "location": None,
    "last_updated": None,
    "enabled": False,
    "emoji": "🌤️"
}

weather_lock = threading.Lock()
weather_thread = None


WEATHER_API_URL = "https://wttr.in/{location}?format=j1"

def get_weather_state():
    with weather_lock:
        state = weather_state.copy()
        if state.get('last_updated') and isinstance(state['last_updated'], datetime):
            state['last_updated'] = state['last_updated'].isoformat()
        return state

def update_weather(location="auto"):
    try:

        if not location or location.lower() == "auto":
            location = ""
        
        url = WEATHER_API_URL.format(location=location or "")
        print(f"[Weather] Fetching from: {url}")
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            

            current = data.get('current_condition', [{}])[0]
            nearest_area = data.get('nearest_area', [{}])[0]
            
            temp_c = current.get('temp_C', 'N/A')
            temp_f = current.get('temp_F', 'N/A')
            condition = current.get('weatherDesc', [{}])[0].get('value', 'Unknown')
            location_name = nearest_area.get('areaName', [{}])[0].get('value', 'Unknown')
            

            emoji = "🌤️"
            condition_lower = condition.lower()
            if "sun" in condition_lower or "clear" in condition_lower:
                emoji = "☀️"
            elif "cloud" in condition_lower:
                emoji = "☁️"
            elif "rain" in condition_lower:
                emoji = "🌧️"
            elif "storm" in condition_lower:
                emoji = "⛈️"
            elif "snow" in condition_lower:
                emoji = "❄️"
            elif "fog" in condition_lower or "mist" in condition_lower:
                emoji = "🌫️"
            
            with weather_lock:
                weather_state['temp_c'] = temp_c
                weather_state['temp_f'] = temp_f
                weather_state['condition'] = condition
                weather_state['location'] = location_name
                weather_state['last_updated'] = datetime.now()
                weather_state['emoji'] = emoji
            
            print(f"[Weather] Updated: {temp_f}°F / {temp_c}°C, {condition} in {location_name}")
            logger.info(f"Weather updated: {temp_f}°F / {temp_c}°C, {condition} in {location_name}")
            return True
        else:
            print(f"[Weather] API returned status code: {response.status_code}")
            
    except Exception as e:
        print(f"[Weather ERROR] {e}")
        logger.error(f"Error fetching weather: {e}")
    
    return False

def weather_updater_thread(interval=600, location="auto"):
    global weather_state
    
    print(f"[Weather] Thread started (interval: {interval}s)")
    logger.info(f"[Weather] Thread started (interval: {interval}s)")
    
    while True:
        try:
            with weather_lock:
                enabled = weather_state.get('enabled', False)
            
            if enabled:
                print(f"[Weather] Running scheduled update for location: {location}")
                update_weather(location)
            else:
                print("[Weather] Weather tracking is disabled")
            
            time.sleep(interval)
            
        except Exception as e:
            print(f"[Weather ERROR] Thread error: {e}")
            logger.error(f"Weather updater error: {e}")
            time.sleep(60)

def start_weather_tracker(interval=600, location="auto", enabled=False):
    global weather_thread, weather_state
    
    print(f"[Weather] start_weather_tracker called with enabled={enabled}, location={location}")
    
    with weather_lock:
        weather_state['enabled'] = enabled
    

    if enabled and (weather_thread is None or not weather_thread.is_alive()):
        print(f"[Weather] Starting weather tracker thread...")
        weather_thread = threading.Thread(
            target=weather_updater_thread,
            args=(interval, location),
            daemon=True
        )
        weather_thread.start()
        

        print(f"[Weather] Triggering initial weather update...")
        threading.Thread(target=update_weather, args=(location,), daemon=True).start()
    else:
        if not enabled:
            print(f"[Weather] Weather tracker NOT started (disabled)")

def enable_weather(location="auto"):
    with weather_lock:
        weather_state['enabled'] = True
    update_weather(location)

def disable_weather():
    with weather_lock:
        weather_state['enabled'] = False

def get_weather_text(temp_unit="F"):
    state = get_weather_state()
    
    if not state.get('enabled') or (not state.get('temp_f') and not state.get('temp_c')):
        return None
    
    emoji = state.get('emoji', '🌤️')
    condition = state.get('condition', 'Unknown')
    
    if temp_unit.upper() == "C":
        temp = f"{state.get('temp_c', '?')}°C"
    else:
        temp = f"{state.get('temp_f', '?')}°F"
    
    return f"{emoji} {temp} {condition}"
