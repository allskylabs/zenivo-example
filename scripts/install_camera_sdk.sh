#!/usr/bin/env bash
# Installs the vendor SDK a ZWO ASI or QHY camera needs, so Zenivo can see the camera.
#
#   scripts/install_camera_sdk.sh zwo|qhy|all [--venv DIR] [--no-udev] [--dry-run]
#
# What it does:
#   - zwo: pip install zwoasi into the venv, libASICamera2.so -> <zenivo>/vendor/lib (no root needed)
#   - qhy: libqhyccd.so -> <zenivo>/vendor/lib (no Python package is needed)
#   - udev rules -> /etc/udev/rules.d (needs sudo; without it the exact command is printed, and many systems
#     let you open the camera without the rules anyway)
# The libraries and rules come from the INDI 3rd-party repository (https://github.com/indilib/indi-3rdparty),
# which redistributes the vendors' SDKs; the ZWO SDK is MIT licensed, QHY's is QHYCCD's own licence (read
# libqhy/ in that repository). Nothing is downloaded unless you run this script. Pinned to one commit so the files
# do not change under you. Replug the camera afterwards.
set -euo pipefail

INDI_REF="4c3a04f43e4a3f85759a0d0aaab6719bd9d2aeb8"
BASE="https://raw.githubusercontent.com/indilib/indi-3rdparty/$INDI_REF"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$SCRIPT_DIR/.venv"
DRY_RUN=0
UDEV=1
WHAT=""

die() { echo "install_camera_sdk.sh: error: $*" >&2; exit 1; }
info() { echo "==> $*"; }
run() { if [ "$DRY_RUN" -eq 1 ]; then echo "[dry-run] $*"; else "$@"; fi; }

while [ $# -gt 0 ]; do
    case "$1" in
        zwo|qhy|all) WHAT="$1"; shift ;;
        --venv) [ $# -ge 2 ] || die "--venv needs a value"; VENV="$2"; shift 2 ;;
        --no-udev) UDEV=0; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) sed -n 2,14p "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "unknown option: $1 (use: zwo | qhy | all, --venv DIR, --dry-run)" ;;
    esac
done
[ -n "$WHAT" ] || die "say which: zwo, qhy or all"

# the INDI folder name for this CPU
case "$(uname -m)" in
    x86_64|amd64) ARCH=x64 ;;
    aarch64|arm64) ARCH=armv8 ;;
    armv7l|armv7) ARCH=armv7 ;;
    *) die "no prebuilt SDK for CPU '$(uname -m)'" ;;
esac
SUDO=""; [ "$(id -u)" -eq 0 ] || SUDO="sudo"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

fetch() {  # fetch URL DEST
    if [ "$DRY_RUN" -eq 1 ]; then echo "[dry-run] download $1"; return 0; fi
    curl -fsSL --retry 3 -m 120 -o "$2" "$1" || die "could not download $1"
}

install_lib() {  # install_lib FOLDER REMOTE_NAME LIBNAME RULES_REMOTE RULES_NAME
    local folder="$1" remote="$2" lib="$3" rules_remote="$4" rules_name="$5"
    info "Downloading $lib ($ARCH)"
    fetch "$BASE/$folder/$ARCH/$remote" "$TMP/$lib"
    fetch "$BASE/$folder/$rules_remote" "$TMP/$rules_name"
    if [ "$DRY_RUN" -eq 0 ]; then
        head -c 4 "$TMP/$lib" | grep -q "ELF" || die "$lib is not a Linux library (download failed?)"
    fi
    run mkdir -p "$SCRIPT_DIR/vendor/lib"
    run install -m 0755 "$TMP/$lib" "$SCRIPT_DIR/vendor/lib/$lib"
    local cmd="install -m 0644 $TMP/$rules_name /etc/udev/rules.d/$rules_name && udevadm control --reload-rules && udevadm trigger"
    if [ "$UDEV" -eq 0 ]; then
        mkdir -p "$SCRIPT_DIR/vendor"; cp "$TMP/$rules_name" "$SCRIPT_DIR/vendor/$rules_name" 2>/dev/null || true
        echo "note: udev rules skipped (--no-udev); the file is in $SCRIPT_DIR/vendor/ if you need it"
    elif [ "$DRY_RUN" -eq 1 ]; then
        echo "[dry-run] sudo sh -c '$cmd'"
    elif [ "$(id -u)" -eq 0 ] || sudo -n true 2>/dev/null; then
        $SUDO sh -c "$cmd"
    elif [ -t 0 ]; then
        echo
        echo "The udev rules go into /etc/udev/rules.d/, which needs administrator rights (sudo)."
        printf "Install them now? sudo will ask for your password. [Y/n] "
        read -r ans
        case "$ans" in
            n|N|no|NO) mkdir -p "$SCRIPT_DIR/vendor"; cp "$TMP/$rules_name" "$SCRIPT_DIR/vendor/$rules_name"
                       echo "Skipped. Later, if the camera is not found, run:"
                       echo "  sudo install -m 0644 $SCRIPT_DIR/vendor/$rules_name /etc/udev/rules.d/ && sudo udevadm control --reload-rules && sudo udevadm trigger" ;;
            *) sudo sh -c "$cmd" || die "sudo failed; run this yourself: sudo sh -c '$cmd'" ;;
        esac
    else
        mkdir -p "$SCRIPT_DIR/vendor"; cp "$TMP/$rules_name" "$SCRIPT_DIR/vendor/$rules_name"
        echo "note: sudo needs a password and there is no terminal to ask for it, so the udev rules were not installed."
        echo "If the camera is not found, run:"
        echo "  sudo install -m 0644 $SCRIPT_DIR/vendor/$rules_name /etc/udev/rules.d/ && sudo udevadm control --reload-rules && sudo udevadm trigger"
    fi
}

if [ "$WHAT" = "zwo" ] || [ "$WHAT" = "all" ]; then
    if [ -x "$VENV/bin/python" ]; then
        info "Installing the zwoasi Python package into $VENV"
        run "$VENV/bin/python" -m pip install zwoasi
    else
        echo "note: no virtual environment at $VENV; run ./install.sh first, or pip install zwoasi in your own environment" >&2
    fi
    install_lib libasi libASICamera2.bin libASICamera2.so 99-asi.rules 99-asi.rules
    info "ZWO SDK installed. Replug the camera, then pick it under Settings > Camera (Rescan)."
fi
if [ "$WHAT" = "qhy" ] || [ "$WHAT" = "all" ]; then
    install_lib libqhy libqhyccd.bin libqhyccd.so 85-qhyccd.rules 85-qhyccd.rules
    info "QHY SDK installed. Replug the camera, then pick it under Settings > Camera (Rescan). Older QHY models that need firmware: see docs/INSTALL.md."
fi
