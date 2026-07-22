import threading
import time
import platform

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

system_stats_state = {
    "cpu_percent": 0,
    "ram_percent": 0,
    "ram_used_gb": 0,
    "ram_total_gb": 0,
    "gpu_percent": 0,
    "gpu_name": "",
    "gpu_available": False,
    "network_sent_speed": 0,
    "network_recv_speed": 0,
    "network_sent_total": 0,
    "network_recv_total": 0,
    "network_interfaces": [],
    "last_update": 0,
    "available": PSUTIL_AVAILABLE,
    "battery_available": False,
    "battery_percent": 0,
    "battery_plugged": False,
}

_stats_thread = None
_stats_running = False
_stats_interval = 5
_stats_enable_gpu = False
_last_net_io = None
_last_net_time = 0
_gpu_unavailable_until = 0

def _active_network_interface_names():
    if not PSUTIL_AVAILABLE:
        return []

    try:
        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()
    except Exception:
        return []

    names = []
    blocked_terms = (
        "loopback",
        "pseudo-interface",
        "isatap",
        "teredo",
        "bluetooth",
        "docker",
        "hyper-v",
        "vethernet",
        "virtualbox",
        "vmware",
        "wsl",
    )
    for name, iface_stats in stats.items():
        lowered = name.lower()
        if any(term in lowered for term in blocked_terms):
            continue
        if not getattr(iface_stats, "isup", False):
            continue

        has_network_addr = False
        for addr in addrs.get(name, []):
            family = getattr(addr, "family", None)
            family_name = getattr(family, "name", "")
            if family_name in {"AF_INET", "AF_INET6"}:
                address = str(getattr(addr, "address", "") or "")
                if address and not address.startswith("127.") and address != "::1":
                    has_network_addr = True
                    break

        if has_network_addr:
            names.append(name)

    return names

def _network_counters_for_active_interfaces():
    try:
        per_nic = psutil.net_io_counters(pernic=True)
        names = _active_network_interface_names()
        selected = [per_nic[name] for name in names if name in per_nic]
        if selected:
            class NetworkTotals:
                pass
            totals = NetworkTotals()
            totals.bytes_sent = sum(counter.bytes_sent for counter in selected)
            totals.bytes_recv = sum(counter.bytes_recv for counter in selected)
            return totals, names
    except Exception:
        pass

    counters = psutil.net_io_counters()
    return counters, []

def format_network_speed(kilobytes_per_second):
    bits_per_second = float(kilobytes_per_second or 0) * 1024 * 8
    if bits_per_second >= 1_000_000:
        return f"{round(bits_per_second / 1_000_000, 1)}Mbps"
    if bits_per_second >= 1_000:
        return f"{round(bits_per_second / 1_000)}Kbps"
    return f"{round(bits_per_second)}bps"

def get_battery_stats():
    try:
        battery = psutil.sensors_battery()
    except Exception:
        return False, 0, False
    if battery is None:
        return False, 0, False
    return True, round(battery.percent), bool(battery.power_plugged)


def get_gpu_stats():
    global _gpu_unavailable_until

    if platform.system() != "Windows":
        return 0, "", False

    now = time.time()
    if now < _gpu_unavailable_until:
        return 0, "", False

    gpu_percent = 0
    gpu_name = ""
    gpu_available = False
    
    try:
        import subprocess
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=utilization.gpu,name', '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(',')
            if len(parts) >= 2:
                gpu_percent = int(parts[0].strip())
                gpu_name = parts[1].strip()
                gpu_available = True
    except:
        _gpu_unavailable_until = now + 300
        pass
    
    return gpu_percent, gpu_name, gpu_available

def update_system_stats(enable_gpu=None):
    global _last_net_io, _last_net_time, system_stats_state
    
    if not PSUTIL_AVAILABLE:
        return
    
    try:
        should_poll_gpu = _stats_enable_gpu if enable_gpu is None else bool(enable_gpu)
        cpu_percent = psutil.cpu_percent(interval=None)
        
        mem = psutil.virtual_memory()
        ram_percent = mem.percent
        ram_used_gb = round(mem.used / (1024**3), 1)
        ram_total_gb = round(mem.total / (1024**3), 1)
        
        current_time = time.time()
        net_io, net_names = _network_counters_for_active_interfaces()
        
        if _last_net_io is not None and _last_net_time > 0:
            time_diff = current_time - _last_net_time
            if time_diff > 0:
                sent_delta = max(0, net_io.bytes_sent - _last_net_io.bytes_sent)
                recv_delta = max(0, net_io.bytes_recv - _last_net_io.bytes_recv)
                sent_speed = sent_delta / time_diff
                recv_speed = recv_delta / time_diff
            else:
                sent_speed = 0
                recv_speed = 0
        else:
            sent_speed = 0
            recv_speed = 0
        
        _last_net_io = net_io
        _last_net_time = current_time
        
        if should_poll_gpu:
            gpu_percent, gpu_name, gpu_available = get_gpu_stats()
        else:
            gpu_percent, gpu_name, gpu_available = 0, "", False

        battery_available, battery_percent, battery_plugged = get_battery_stats()

        system_stats_state.update({
            "cpu_percent": round(cpu_percent, 1),
            "ram_percent": round(ram_percent, 1),
            "ram_used_gb": ram_used_gb,
            "ram_total_gb": ram_total_gb,
            "gpu_percent": gpu_percent,
            "gpu_name": gpu_name,
            "gpu_available": gpu_available,
            "network_sent_speed": round(sent_speed / 1024, 1),
            "network_recv_speed": round(recv_speed / 1024, 1),
            "network_sent_total": round(net_io.bytes_sent / (1024**3), 2),
            "network_recv_total": round(net_io.bytes_recv / (1024**3), 2),
            "network_interfaces": net_names,
            "last_update": current_time,
            "available": True,
            "battery_available": battery_available,
            "battery_percent": battery_percent,
            "battery_plugged": battery_plugged,
        })
        
    except Exception as e:
        print(f"[System Stats] Error: {e}")

def _stats_worker():
    global _stats_running
    
    while _stats_running:
        update_system_stats()
        time.sleep(max(2, min(int(_stats_interval or 5), 30)))

def start_system_stats(update_interval=5, enable_gpu=False):
    global _stats_thread, _stats_running, _stats_interval, _stats_enable_gpu
    
    if not PSUTIL_AVAILABLE:
        print("[System Stats] psutil not available - stats disabled")
        return

    try:
        _stats_interval = max(2, min(int(update_interval), 30))
    except Exception:
        _stats_interval = 5
    _stats_enable_gpu = bool(enable_gpu)
    
    if _stats_thread is not None and _stats_thread.is_alive():
        return
    
    _stats_running = True
    update_system_stats(enable_gpu=_stats_enable_gpu)
    _stats_thread = threading.Thread(target=_stats_worker, daemon=True)
    _stats_thread.start()
    print("[System Stats] Monitoring started")

def stop_system_stats():
    global _stats_running
    _stats_running = False
    print("[System Stats] Monitoring stopped")

def get_system_stats():
    return system_stats_state.copy()

def format_system_stats(show_cpu=True, show_ram=True, show_gpu=False, show_network=False):
    stats = get_system_stats()

    if not stats.get("available", False):
        return ""

    parts = []

    if show_cpu:
        parts.append(f"CPU: {stats['cpu_percent']}%")

    if show_ram:
        parts.append(f"RAM: {stats['ram_percent']}%")

    if show_gpu and stats.get("gpu_available", False):
        parts.append(f"GPU: {stats['gpu_percent']}%")

    if show_network:
        down = stats.get("network_recv_speed", 0)
        up = stats.get("network_sent_speed", 0)
        parts.append(f"Down {format_network_speed(down)} Up {format_network_speed(up)}")

    return " | ".join(parts)

