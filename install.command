#!/bin/bash
# One-step installer for Simpli OMS and the SimpliEarn Desk app (macOS).
# Double-click this file in Finder, or run ./install.command in a terminal.
# Safe to run again: it skips what is already set up and rebuilds the app.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
fail() { printf '\n\033[31merror: %s\033[0m\n' "$1" >&2; finish 1; }
ask() { local reply; read -r -p "$1 [y/N] " reply; [[ "$reply" =~ ^[Yy] ]]; }
finish() {
  # Keep the Terminal window open when launched by double-click.
  echo
  read -r -p "Press Enter to close." _ || true
  exit "${1:-0}"
}

[ "$(uname)" = "Darwin" ] || fail "SimpliEarn Desk is a Mac app. On other systems, run: python3 setup.py"
[ -f "$ROOT/setup.py" ] && [ -d "$ROOT/desktop" ] || fail "run this from a simpli-oms checkout on the desktop-oms branch"

# Finder starts .command files without the user's nvm or Homebrew setup on some machines.
export NVM_DIR="${NVM_DIR:-$HOME/.nvm}"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh" >/dev/null 2>&1 || true
for prefix in /opt/homebrew /usr/local "$HOME/homebrew"; do
  [ -x "$prefix/bin/brew" ] && eval "$("$prefix/bin/brew" shellenv)" && break
done

step "Checking Python 3.11+"
PYTHON=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    PYTHON="$(command -v "$candidate")"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  if command -v brew >/dev/null && ask "Python 3.11 or newer is missing. Install it with Homebrew?"; then
    brew install python@3.13
    PYTHON="$(brew --prefix)/bin/python3.13"
  else
    fail "install Python 3.11 or newer from https://www.python.org/downloads/ and run this again"
  fi
fi
echo "using $PYTHON ($("$PYTHON" --version))"

step "Checking Node.js 18+"
if ! command -v node >/dev/null || [ "$(node -p 'process.versions.node.split(".")[0]')" -lt 18 ]; then
  if command -v brew >/dev/null && ask "Node.js 18 or newer is missing. Install it with Homebrew?"; then
    brew install node
  else
    fail "install Node.js 18 or newer from https://nodejs.org and run this again"
  fi
fi
echo "using node $(node --version)"

step "Shared env file"
ENV_FILE="$ROOT/backend/.env"
env_ready() { [ -f "$ENV_FILE" ] && grep -q '^APCA_API_KEY_ID=..*' "$ENV_FILE" && ! grep -q 'PROJECT_REF' "$ENV_FILE"; }
if env_ready; then
  echo "backend/.env already has the shared keys"
else
  echo "You need the shared env file from the SimpliEarn team (it is not in git)."
  while true; do
    read -r -p "Drag the file into this window and press Enter: " given
    given="${given%"${given##*[![:space:]]}"}"   # trim trailing space from drag-and-drop
    given="${given//\\ / }"; given="${given#\'}"; given="${given%\'}"
    if [ -f "$given" ]; then
      cp "$given" "$ENV_FILE"
      env_ready && break
      echo "That file is missing the Alpaca keys or database URL. Try again."
    else
      echo "No file at: $given"
    fi
  done
  echo "saved backend/.env"
fi

step "Installing the OMS backend and dashboard"
"$PYTHON" setup.py

step "Installing the strategies environment"
VENV="$ROOT/strategies/.venv"
if ! "$VENV/bin/python" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
  rm -rf "$VENV"
  "$PYTHON" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install -q --upgrade pip
"$VENV/bin/python" -m pip install -q -r "$ROOT/strategies/requirements.txt"
"$VENV/bin/python" -m ipykernel install --user --name simpli-oms-strategies --display-name "Simpli OMS strategies" >/dev/null

# xgboost (used by zscore-spy) needs OpenMP on macOS.
if ! "$VENV/bin/python" -c 'import xgboost' 2>/dev/null; then
  if command -v brew >/dev/null; then
    echo "xgboost needs OpenMP; installing libomp with Homebrew"
    brew install libomp
  fi
  # xgboost only looks in /opt/homebrew, so point it at Homebrew's libomp elsewhere or scikit-learn's bundled copy.
  SITE="$("$VENV/bin/python" -c 'import site; print(site.getsitepackages()[0])')"
  XGB="$SITE/xgboost/lib/libxgboost.dylib"
  for libdir in "$(brew --prefix libomp 2>/dev/null)/lib" "$SITE/sklearn/.dylibs"; do
    "$VENV/bin/python" -c 'import xgboost' 2>/dev/null && break
    if [ -f "$libdir/libomp.dylib" ] && [ -f "$XGB" ]; then
      install_name_tool -add_rpath "$libdir" "$XGB" 2>/dev/null || true
      codesign --force -s - "$XGB" 2>/dev/null || true
    fi
  done
  if ! "$VENV/bin/python" -c 'import xgboost' 2>/dev/null; then
    echo "warning: xgboost still can't load, so zscore-spy won't run. Install Homebrew and run: brew install libomp"
  fi
fi

step "Building SimpliEarn Desk"
mkdir -p "$ROOT/desktop/data/logs"
"$ROOT/desktop/build_app.sh"

step "Done"
echo "SimpliEarn Desk is in ~/Applications. Opening it now."
echo "Click Start OMS at the bottom of the sidebar to start the backend and dashboard."
open "$HOME/Applications/SimpliEarn Desk.app"
finish 0
