#!/usr/bin/env bash
# Zenivo installer: virtual environment, Python dependencies and an optional systemd service.
# Idempotent: running it again reuses the virtual environment and rewrites only the unit file.
# It never touches data/ and never restarts a unit with a different name.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

INSTALL_DIR="$SCRIPT_DIR"
PORT=8080
HOST="0.0.0.0"
CAMERA="auto"
RUN_USER="${SUDO_USER:-$(id -un)}"
SERVICE_NAME="zenivo"
USE_SYSTEMD=1
DRY_RUN=0

usage() {
    cat <<'EOF'
Usage: ./install.sh [options]

Installs Zenivo (all-sky observatory software) into a Python virtual environment and,
unless told otherwise, as a systemd service.

Options:
  --dir PATH            Install directory containing run_allsky.py (default: the directory of this script)
  --port N              TCP port of the web UI and API (default: 8080)
  --host ADDR           Bind address (default: 0.0.0.0; use 127.0.0.1 behind a reverse proxy)
  --camera NAME         auto | rts5869 | picamera | v4l2 | simulator (default: auto)
  --user NAME           Linux user that runs the service (default: the current user)
  --service-name NAME   systemd unit name without ".service" (default: zenivo)
  --no-systemd          Only create the virtual environment and install the dependencies
  --dry-run             Print every action and change nothing
  -h, --help            Show this help

Examples:
  ./install.sh --dry-run
  ./install.sh --camera picamera --port 8095 --host 127.0.0.1
  ./install.sh --no-systemd
EOF
}

die() { echo "install.sh: error: $*" >&2; exit 1; }
info() { echo "==> $*"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --dir) [ $# -ge 2 ] || die "--dir needs a value"; INSTALL_DIR="$2"; shift 2 ;;
        --port) [ $# -ge 2 ] || die "--port needs a value"; PORT="$2"; shift 2 ;;
        --host) [ $# -ge 2 ] || die "--host needs a value"; HOST="$2"; shift 2 ;;
        --camera) [ $# -ge 2 ] || die "--camera needs a value"; CAMERA="$2"; shift 2 ;;
        --user) [ $# -ge 2 ] || die "--user needs a value"; RUN_USER="$2"; shift 2 ;;
        --service-name) [ $# -ge 2 ] || die "--service-name needs a value"; SERVICE_NAME="$2"; shift 2 ;;
        --no-systemd) USE_SYSTEMD=0; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" ;;
    esac
done

case "$CAMERA" in
    auto|rts5869|picamera|v4l2|simulator) ;;
    *) die "--camera must be one of: auto, rts5869, picamera, v4l2, simulator" ;;
esac
case "$PORT" in
    ''|*[!0-9]*) die "--port must be a number" ;;
esac
{ [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ]; } || die "--port must be between 1 and 65535"
case "$SERVICE_NAME" in
    ''|*[!A-Za-z0-9_.@-]*) die "--service-name may contain only letters, digits and _ . @ -" ;;
esac
case "$INSTALL_DIR" in
    /*) ;;
    *) INSTALL_DIR="$(pwd)/$INSTALL_DIR" ;;
esac
INSTALL_DIR="${INSTALL_DIR%/}"

# Run a command, or only print it with --dry-run.
run() {
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "[dry-run] $*"
    else
        "$@"
    fi
}

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    SUDO="sudo"
fi

# Version line (src/version.py is the single source of the version)
VERSION="unknown"
if [ -f "$SCRIPT_DIR/src/version.py" ]; then
    VERSION="$(sed -n 's/^__version__[[:space:]]*=[[:space:]]*["'\'']\([^"'\'']*\)["'\''].*/\1/p' "$SCRIPT_DIR/src/version.py" | head -n 1)"
    [ -n "$VERSION" ] || VERSION="unknown"
fi
echo "Zenivo $VERSION installer"
[ "$DRY_RUN" -eq 1 ] && echo "(dry run: nothing will be changed)"

# 1. Python >= 3.10
command -v python3 >/dev/null 2>&1 || die "python3 not found. Install it first (Debian/Ubuntu/Raspberry Pi OS: sudo apt install python3 python3-venv)"
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    die "Python 3.10 or newer is required, found $(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
fi
info "Python $(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])') found"

# 2. Install directory
if [ ! -f "$INSTALL_DIR/run_allsky.py" ]; then
    if [ "$DRY_RUN" -eq 1 ] && [ "$INSTALL_DIR" != "$SCRIPT_DIR" ]; then
        echo "[dry-run] note: $INSTALL_DIR/run_allsky.py does not exist; a real run would stop here"
    else
        die "$INSTALL_DIR/run_allsky.py not found. Copy or clone the Zenivo files there first (see docs/INSTALL.md)"
    fi
fi
REQ="$INSTALL_DIR/requirements.txt"
if [ ! -f "$REQ" ] && [ -f "$SCRIPT_DIR/requirements.txt" ]; then
    REQ="$SCRIPT_DIR/requirements.txt"
fi

# 3. Virtual environment
VENV="$INSTALL_DIR/.venv"
VENV_ARGS=()
if python3 -c 'import picamera2' >/dev/null 2>&1; then
    VENV_ARGS+=(--system-site-packages)
    info "System picamera2 found: the virtual environment will use --system-site-packages"
fi
if [ -x "$VENV/bin/python" ]; then
    info "Virtual environment already exists: $VENV"
else
    info "Creating virtual environment: $VENV"
    run python3 -m venv ${VENV_ARGS[@]+"${VENV_ARGS[@]}"} "$VENV"
fi

# 4. Python dependencies (headless OpenCV: Zenivo opens no GUI windows)
info "Installing Python dependencies"
run "$VENV/bin/python" -m pip install --upgrade pip
run "$VENV/bin/python" -m pip install -r "$REQ" fastapi uvicorn websockets opencv-python-headless requests httpx

# 5. systemd
if [ "$USE_SYSTEMD" -eq 1 ]; then
    command -v systemctl >/dev/null 2>&1 || die "systemctl not found; re-run with --no-systemd"
    id "$RUN_USER" >/dev/null 2>&1 || die "user '$RUN_USER' does not exist (use --user)"
    TEMPLATE="$SCRIPT_DIR/deploy/zenivo.service"
    [ -f "$TEMPLATE" ] || die "$TEMPLATE not found"
    UNIT_PATH="/etc/systemd/system/$SERVICE_NAME.service"

    # Warn about units from older installs that may use the same port or camera
    for old in allsky-ng allsky-web; do
        if [ "$old" != "$SERVICE_NAME" ] && systemctl list-unit-files "$old.service" 2>/dev/null | grep -q "^$old.service"; then
            echo "warning: unit '$old' exists on this host. It is left untouched. Stop it yourself if it uses port $PORT or the same camera."
        fi
    done

    RENDERED="$(mktemp)"
    trap 'rm -f "$RENDERED"' EXIT
    sed -e "s|@USER@|$RUN_USER|g" \
        -e "s|@INSTALL_DIR@|$INSTALL_DIR|g" \
        -e "s|@HOST@|$HOST|g" \
        -e "s|@PORT@|$PORT|g" \
        -e "s|@CAMERA@|$CAMERA|g" \
        "$TEMPLATE" > "$RENDERED"
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "[dry-run] render $TEMPLATE -> $UNIT_PATH (user=$RUN_USER dir=$INSTALL_DIR host=$HOST port=$PORT camera=$CAMERA):"
        grep -E '^(User|SupplementaryGroups|WorkingDirectory|ExecStart)=' "$RENDERED" | sed 's/^/[dry-run]   /'
    fi
    info "Installing systemd unit $UNIT_PATH"
    run $SUDO install -m 0644 "$RENDERED" "$UNIT_PATH"
    run $SUDO systemctl daemon-reload
    run $SUDO systemctl enable "$SERVICE_NAME.service"
    # Start a stopped service; restart our own unit if it is already running (it has a new unit file)
    if [ "$DRY_RUN" -eq 0 ] && systemctl is-active --quiet "$SERVICE_NAME.service"; then
        run $SUDO systemctl restart "$SERVICE_NAME.service"
    else
        run $SUDO systemctl start "$SERVICE_NAME.service"
    fi
fi

# 6. Next steps
echo
info "Done. Zenivo $VERSION"
SHOW_HOST="$HOST"
if [ "$HOST" = "0.0.0.0" ]; then
    SHOW_HOST="$(hostname -I 2>/dev/null | awk '{print $1}')"
    [ -n "$SHOW_HOST" ] || SHOW_HOST="localhost"
fi
echo "Web UI:     http://$SHOW_HOST:$PORT/"
echo "Version:    curl http://localhost:$PORT/api/version"
if [ "$USE_SYSTEMD" -eq 1 ]; then
    echo "Status:     systemctl status $SERVICE_NAME"
    echo "Logs:       journalctl -u $SERVICE_NAME -f   (and $INSTALL_DIR/data/logs/allsky.log)"
else
    echo "Start:      cd $INSTALL_DIR && .venv/bin/python run_allsky.py --port $PORT --camera $CAMERA"
fi
echo "Next:       set the time zone (timedatectl), then open Settings > Station & Optics and enter your latitude, longitude and elevation."
echo "Docs:       docs/INSTALL.md, docs/CONFIGURATION.md"
