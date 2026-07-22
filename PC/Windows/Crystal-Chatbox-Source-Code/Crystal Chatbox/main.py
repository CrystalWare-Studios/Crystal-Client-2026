import sys
import os

if getattr(sys, 'frozen', False) and sys.platform == 'win32':


    try:
        devnull = open(os.devnull, 'w', encoding='utf-8', errors='ignore')
        sys.stdout = devnull
        sys.stderr = devnull
    except:
        pass


    os.environ['PYTHONUTF8'] = '1'
    os.environ['PYTHONIOENCODING'] = 'utf-8'
elif sys.platform == 'win32':


    for _stream_name in ("stdout", "stderr"):
        _stream = getattr(sys, _stream_name, None)
        if _stream is not None and hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    os.environ['PYTHONUTF8'] = '1'
    os.environ['PYTHONIOENCODING'] = 'utf-8'

import threading
import time
import argparse

try:
    import setproctitle
    setproctitle.setproctitle("Crystal Client")
except ImportError:
    pass

try:
    import webview
    WEBVIEW_AVAILABLE = True
except ImportError:
    WEBVIEW_AVAILABLE = False

from routes import app as flask_app
import json
import shutil

def start_server(app, host=None, port=5000):
    if host is None:
        host = os.environ.get("HOST", "0.0.0.0")
    print(f"[Server] Starting Flask server at http://{host}:{port} ...")
    app.run(host=host, port=port, debug=False, use_reloader=False)

class DownloadAPI:
    
    def download_settings(self):
        try:
            settings_file = os.path.join(os.path.dirname(__file__), "settings.json")
            if not os.path.exists(settings_file):
                return {"success": False, "error": "Settings file not found"}
            
            downloads_path = os.path.expanduser("~/Downloads")
            from datetime import datetime
            filename = f"vrchat_chatbox_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            dest_path = os.path.join(downloads_path, filename)
            
            shutil.copy2(settings_file, dest_path)
            return {"success": True, "path": dest_path, "filename": filename}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def download_log(self):
        try:
            log_file = os.path.join(os.path.dirname(__file__), "vrchat_errors.log")
            if not os.path.exists(log_file):
                return {"success": False, "error": "No error log found"}
            
            downloads_path = os.path.expanduser("~/Downloads")
            from datetime import datetime
            filename = f"vrchat_errors_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
            dest_path = os.path.join(downloads_path, filename)
            
            shutil.copy2(log_file, dest_path)
            return {"success": True, "path": dest_path, "filename": filename}
        except Exception as e:
            return {"success": False, "error": str(e)}

def start_gui(app, host="127.0.0.1", port=5000):
    if not WEBVIEW_AVAILABLE:
        print("[GUI] PyWebview not available, falling back to server mode...")
        start_server(app, host=host, port=port)
        return
    
    server_thread = threading.Thread(target=start_server, args=(app, host, port), daemon=True)
    server_thread.start()

    print("[GUI] Waiting for server to start...")
    time.sleep(2)

    print("[GUI] Launching PyWebview window...")
    api = DownloadAPI()
    window = webview.create_window(
        title="Crystal Client",
        url=f"http://{host}:{port}",
        width=1200,
        height=800,
        resizable=True,
        fullscreen=False,
        min_size=(960, 640),
        background_color="#101114",
        js_api=api
    )

    webview.start(debug=False)
    print("[GUI] Application closed.")
    sys.exit(0)

IS_ANDROID = "ANDROID_ARGUMENT" in os.environ

def main():
    parser = argparse.ArgumentParser(description="Launch Crystal Client Dashboard.")
    parser.add_argument("--nogui", action="store_true", help="Run server only, without GUI.")


    args, _unknown = parser.parse_known_args()

    port = int(os.environ.get("PORT", 5000))

    is_replit = os.environ.get("REPL_ID") or os.environ.get("REPLIT_DB_URL")

    if args.nogui or is_replit or IS_ANDROID:


        start_server(flask_app, port=port)
    else:
        start_gui(flask_app, port=port)

if __name__ == "__main__":
    main()
