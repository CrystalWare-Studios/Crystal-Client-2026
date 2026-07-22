import os
import sys

os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

from routes import app as flask_app


def main():
    port = int(os.environ.get("PORT", 5000))
    print(f"[Crystal Client] Starting Flask server at http://127.0.0.1:{port} ...")
    flask_app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
