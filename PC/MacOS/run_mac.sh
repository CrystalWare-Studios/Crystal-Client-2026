#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLATFORM_DIR="$ROOT_DIR"
CHATBOX_DIR="$PLATFORM_DIR/Crystal-Chatbox-Source-Code/Crystal Chatbox"
CHATBOX_VENV="$PLATFORM_DIR/.venv"

resolve_python() {
  local candidate
  for candidate in \
    "python3.12" \
    "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12" \
    "python3.11" \
    "python3"; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
        command -v "$candidate"
        return 0
      fi
    elif [ -x "$candidate" ]; then
      if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
        echo "$candidate"
        return 0
      fi
    fi
  done

  echo "Python 3.11 or newer was not found. Install Python 3.12 from https://www.python.org/downloads/macos/." >&2
  return 1
}

PYTHON_BIN="$(resolve_python)"

if [ ! -d "$CHATBOX_DIR" ]; then
  echo "Chatbox folder not found: $CHATBOX_DIR"
  exit 1
fi

if [ -d "$CHATBOX_VENV" ] && [ ! -x "$CHATBOX_VENV/bin/python3" ] && [ ! -x "$CHATBOX_VENV/bin/python" ]; then
  rm -rf "$CHATBOX_VENV"
fi

if [ ! -d "$CHATBOX_VENV" ]; then
  "$PYTHON_BIN" -m venv "$CHATBOX_VENV"
fi

CHATBOX_PYTHON="$CHATBOX_VENV/bin/python3"
if [ ! -x "$CHATBOX_PYTHON" ]; then
  CHATBOX_PYTHON="$CHATBOX_VENV/bin/python"
fi

"$CHATBOX_PYTHON" -m pip install --upgrade pip
"$CHATBOX_PYTHON" -m pip install -r "$CHATBOX_DIR/requirements.txt"
"$CHATBOX_PYTHON" -m pip install pywebview pyobjc-framework-Cocoa

echo ""
echo "Starting Crystal Client for macOS on http://127.0.0.1:${PORT:-5000}"
echo ""

cd "$CHATBOX_DIR"
exec "$CHATBOX_PYTHON" main.py "$@"
