import os
import sys

os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"


def _report_crash(exc_type, exc_value, exc_tb):
    import traceback
    from datetime import datetime
    message = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    try:
        from settings import DATA_DIR
        path = os.path.join(DATA_DIR, "crash_log.txt")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n--- Crash at {datetime.now().isoformat()} ---\n{message}\n")
    except Exception:
        pass
    try:
        import crystalware_cloud
        crystalware_cloud.report_crash(message)
    except Exception:
        pass


def _report_thread_crash(args):
    _report_crash(args.exc_type, args.exc_value, args.exc_traceback)


sys.excepthook = _report_crash
try:
    import threading
    threading.excepthook = _report_thread_crash
except Exception:
    pass

from routes import app as flask_app


def main():
    port = int(os.environ.get("PORT", 5000))
    print(f"[Crystal Chatbox] Starting Flask server at http://127.0.0.1:{port} ...")
    try:
        flask_app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
    except Exception:
        _report_crash(*sys.exc_info())
        raise


if __name__ == "__main__":
    main()
