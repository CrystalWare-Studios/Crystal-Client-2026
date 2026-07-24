import requests
import os
import sys
import json
import logging
from datetime import datetime, timedelta
from packaging import version

GITHUB_REPO = "CrystalWare-Studios/Crystal-Chatbox-2026"


if getattr(sys, 'frozen', False):

    BASE_DIR = os.path.dirname(sys.executable)
    DATA_DIR = os.path.join(BASE_DIR, "Crystal Chatbox Data")
elif "ANDROID_ARGUMENT" in os.environ:

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = os.environ.get("ANDROID_PRIVATE", BASE_DIR)
else:

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = BASE_DIR


os.makedirs(DATA_DIR, exist_ok=True)

VERSION_FILE = "version.txt"
UPDATE_CHECK_CACHE = os.path.join(DATA_DIR, ".update_cache.json")
UPDATE_CHECK_INTERVAL = 3600

logger = logging.getLogger(__name__)

def _version_file_path():
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, VERSION_FILE)


def get_current_version():
    try:
        path = _version_file_path()
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return f.read().strip()
    except Exception as e:
        logger.error(f"Error reading version: {e}")
    return "1.0.0"

def get_github_repo():
    if getattr(sys, "frozen", False):
        return None
    try:
        import subprocess
        result = subprocess.run(
            ['git', 'config', '--get', 'remote.origin.url'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            url = result.stdout.strip()

            if 'github.com' in url:

                if url.startswith('https://'):

                    parts = url.replace('https://github.com/', '').replace('.git', '').split('/')
                elif url.startswith('git@'):

                    parts = url.replace('git@github.com:', '').replace('.git', '').split('/')
                else:
                    return None
                
                if len(parts) >= 2:
                    return f"{parts[0]}/{parts[1]}"
    except Exception as e:
        logger.error(f"Error detecting GitHub repo: {e}")
    return None

def check_for_updates(force=False):
    try:

        if not force and os.path.exists(UPDATE_CHECK_CACHE):
            with open(UPDATE_CHECK_CACHE, 'r', encoding='utf-8') as f:
                cache = json.load(f)
                cache_time = datetime.fromisoformat(cache.get('checked_at', '2000-01-01'))
                if datetime.now() - cache_time < timedelta(seconds=UPDATE_CHECK_INTERVAL):
                    return cache.get('update_info')
        
        repo = get_github_repo()
        if not repo:
            repo = GITHUB_REPO
        

        url = f"https://api.github.com/repos/{repo}/releases/latest"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            release = response.json()
            latest_version = release.get('tag_name', '').lstrip('v')
            current_version = get_current_version()
            

            try:
                latest_ver = version.parse(latest_version)
                current_ver = version.parse(current_version)
                update_available = latest_ver > current_ver
            except Exception as e:
                logger.error(f"Error parsing versions: {e}")

                update_available = latest_version != current_version and latest_version > current_version
            
            exe_asset_url = ''
            for asset in release.get('assets', []) or []:
                name = str(asset.get('name', ''))
                if name.lower().endswith('.exe') and 'windows' in name.lower():
                    exe_asset_url = asset.get('browser_download_url', '')
                    break
            if not exe_asset_url:
                for asset in release.get('assets', []) or []:
                    if str(asset.get('name', '')).lower().endswith('.exe'):
                        exe_asset_url = asset.get('browser_download_url', '')
                        break

            can_apply = bool(exe_asset_url) and sys.platform == 'win32' and getattr(sys, 'frozen', False)

            update_info = {
                'current_version': current_version,
                'latest_version': latest_version,
                'update_available': update_available,
                'release_name': release.get('name', ''),
                'release_notes': release.get('body', ''),
                'release_url': release.get('html_url', ''),
                'published_at': release.get('published_at', ''),
                'download_url': release.get('zipball_url', ''),
                'exe_download_url': exe_asset_url,
                'can_apply': can_apply,
                'repo': repo
            }
            

            with open(UPDATE_CHECK_CACHE, 'wb') as f:
                f.write(json.dumps({
                    'checked_at': datetime.now().isoformat(),
                    'update_info': update_info
                }, indent=4, ensure_ascii=False).encode('utf-8'))
            
            return update_info
        
    except Exception as e:
        logger.error(f"Error checking for updates: {e}")
    
    return None

def get_update_status():
    try:
        if os.path.exists(UPDATE_CHECK_CACHE):
            with open(UPDATE_CHECK_CACHE, 'r', encoding='utf-8') as f:
                cache = json.load(f)
                return cache.get('update_info')
    except:
        pass
    
    return {
        'current_version': get_current_version(),
        'latest_version': 'Unknown',
        'update_available': False,
        'repo': get_github_repo()
    }

def apply_update(download_url):
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        return {
            'success': False,
            'message': 'One-click update is only available in the packaged Windows app. Download the latest release manually.'
        }
    if not download_url:
        return {
            'success': False,
            'message': 'No downloadable update was found for this platform. Download the latest release manually.'
        }

    import tempfile
    import subprocess

    try:
        target_exe = sys.executable
        temp_dir = tempfile.gettempdir()
        new_exe_path = os.path.join(temp_dir, 'CrystalClientUpdate.exe')

        response = requests.get(download_url, stream=True, timeout=60)
        response.raise_for_status()
        with open(new_exe_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=262144):
                if chunk:
                    f.write(chunk)

        if os.path.getsize(new_exe_path) < 1024 * 1024:
            return {'success': False, 'message': 'The downloaded update looked incomplete. Please try again.'}

        pid = os.getpid()
        script_path = os.path.join(temp_dir, 'crystal_client_updater.bat')
        script = (
            "@echo off\r\n"
            ":wait\r\n"
            f'tasklist /FI "PID eq {pid}" 2>NUL | find "{pid}" >NUL\r\n'
            "if not errorlevel 1 (\r\n"
            "    timeout /t 1 /nobreak >NUL\r\n"
            "    goto wait\r\n"
            ")\r\n"
            f'copy /Y "{new_exe_path}" "{target_exe}"\r\n'
            f'start "" "{target_exe}"\r\n'
            f'del "{new_exe_path}"\r\n'
            'del "%~f0"\r\n'
        )
        with open(script_path, 'w', encoding='utf-8') as f:
            f.write(script)

        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        subprocess.Popen(
            ['cmd.exe', '/c', script_path],
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
        )
        return {'success': True, 'message': 'Update downloaded. Crystal Chatbox will close and restart in a moment.'}
    except Exception as e:
        logger.error(f"Error applying update: {e}")
        return {'success': False, 'message': f'Update failed: {e}'}
