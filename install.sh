#!/usr/bin/env bash
# BitTwin installer - installs for the current user only (no root needed
# for BitTwin itself; apt may ask for your password if Qt/SSH libs are missing).
#
#   ./install.sh               install / update BitTwin
#   ./install.sh --uninstall   remove BitTwin (keeps your settings and logs)
#   ./install.sh --purge       remove BitTwin and its settings, logs and history
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/bittwin"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/BitTwin"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/BitTwin"

bold=$'\e[1m'; green=$'\e[32m'; red=$'\e[31m'; reset=$'\e[0m'
say()  { printf '%s\n' "${bold}BitTwin${reset}  $*"; }
ok()   { printf '  %s✓%s %s\n' "$green" "$reset" "$*"; }
fail() { printf '  %s✗%s %s\n' "$red" "$reset" "$*"; }

refresh_menus() {
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q "$DESKTOP_DIR" 2>/dev/null || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -q -t "${ICON_DIR%/scalable/apps}" 2>/dev/null || true
    fi
    for k in kbuildsycoca6 kbuildsycoca5; do
        if command -v "$k" >/dev/null 2>&1; then
            "$k" --noincremental >/dev/null 2>&1 || true
            break
        fi
    done
}

uninstall() {
    say "uninstalling…"
    rm -rf "$APP_DIR"
    rm -f "$BIN_DIR/bittwin" "$DESKTOP_DIR/bittwin.desktop" "$ICON_DIR/bittwin.svg"
    ok "Removed program files, menu entry and icon"
    if [ "${1:-}" = "--purge" ]; then
        rm -rf "$CONFIG_DIR" "$STATE_DIR"
        rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/BitTwin.conf"
        ok "Removed settings, saved connections, logs and sync history"
    else
        echo "  Settings and logs were kept in $CONFIG_DIR and $STATE_DIR"
    fi
    refresh_menus
    exit 0
}

case "${1:-}" in
    --uninstall) uninstall ;;
    --purge)     uninstall --purge ;;
    -h|--help)   sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    "") ;;
    *) fail "Unknown option: $1"; exit 2 ;;
esac

say "installing for user $(id -un)"

# ---------------------------------------------------------------- dependencies
# The Qt SVG icon plugin is packaged as qt6-svg-plugins on Ubuntu 25.04+
# and as libqt6svg6 on Ubuntu 24.04.
svg_pkg() {
    if command -v apt-cache >/dev/null 2>&1 && apt-cache show qt6-svg-plugins >/dev/null 2>&1; then
        echo qt6-svg-plugins
    else
        echo libqt6svg6
    fi
}
APT_PKGS="python3 python3-pyqt6 python3-paramiko $(svg_pkg) openssh-client"
if ! python3 -c 'import PyQt6.QtWidgets, paramiko' 2>/dev/null; then
    fail "BitTwin needs the Qt and SSH libraries that ship with Ubuntu/Kubuntu."
    echo
    echo "  These official Ubuntu packages are required:"
    echo "      $APT_PKGS"
    echo
    echo "  Installing them needs administrator rights (sudo will ask for your password)."
    if [ ! -t 0 ]; then
        echo "  Run this first, then run ./install.sh again:"
        echo "      sudo apt install $APT_PKGS"
        exit 1
    fi
    read -r -p "  Install them now with apt? [y/N] " ans
    if [[ ! "$ans" =~ ^[Yy]$ ]]; then
        echo "  Nothing was installed."
        exit 1
    fi
    # shellcheck disable=SC2086  # word splitting of the package list is intended
    sudo apt-get update && sudo apt-get install -y $APT_PKGS
fi
ok "Dependencies found"

# ---------------------------------------------------------------- self-test
if out=$(python3 -m unittest discover -s tests 2>&1); then
    ok "Self-tests passed ($(printf '%s\n' "$out" | grep -E '^Ran ' | sed 's/^Ran //'))"
else
    printf '%s\n' "$out" | tail -n 20
    fail "Self-tests failed - not installing."
    exit 1
fi

# ---------------------------------------------------------------- files
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR" "$BIN_DIR" "$DESKTOP_DIR" "$ICON_DIR"
cp -r bittwin assets "$APP_DIR/"
cp LICENSE README.md "$APP_DIR/" 2>/dev/null || true
find "$APP_DIR" -name '__pycache__' -type d -prune -exec rm -rf {} +
ok "Program installed to $APP_DIR"

cat > "$BIN_DIR/bittwin" <<LAUNCH
#!/usr/bin/env bash
PYTHONPATH="$APP_DIR\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m bittwin "\$@"
LAUNCH
chmod 755 "$BIN_DIR/bittwin"
ok "Command installed: $BIN_DIR/bittwin"

cp assets/bittwin.svg "$ICON_DIR/bittwin.svg"
sed "s|@EXEC@|$BIN_DIR/bittwin|" assets/bittwin.desktop > "$DESKTOP_DIR/bittwin.desktop"
chmod 644 "$DESKTOP_DIR/bittwin.desktop"
refresh_menus
ok "Added to the application menu (Utilities → BitTwin)"

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "  Note: $BIN_DIR is not on your PATH yet - log out and back in, or start BitTwin from the menu." ;;
esac
echo
say "installed. Start it from the application menu or by typing ${bold}bittwin${reset}."
