#!/usr/bin/env bash
# BitTwin - run straight from this folder without installing anything.
#
#   ./run.sh              run the self-tests, then start BitTwin
#   ./run.sh --no-test    start BitTwin immediately
#   ./run.sh --test       only run the self-tests
#   ./run.sh LEFT RIGHT   start with two folders pre-selected
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

bold=$'\e[1m'; dim=$'\e[2m'; green=$'\e[32m'; red=$'\e[31m'; yellow=$'\e[33m'; reset=$'\e[0m'
say()  { printf '%s\n' "${bold}BitTwin${reset}  $*"; }
ok()   { printf '  %s✓%s %s\n' "$green" "$reset" "$*"; }
warn() { printf '  %s!%s %s\n' "$yellow" "$reset" "$*"; }
fail() { printf '  %s✗%s %s\n' "$red" "$reset" "$*"; }

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
APT_PKGS="python3 python3-pyqt6 python3-paramiko python3-keyring $(svg_pkg) openssh-client"

check_deps() {
    local missing=()
    command -v python3 >/dev/null 2>&1 || missing+=(python3)
    if command -v python3 >/dev/null 2>&1; then
        python3 -c 'import PyQt6.QtWidgets' 2>/dev/null || missing+=(python3-pyqt6)
        python3 -c 'import paramiko' 2>/dev/null       || missing+=(python3-paramiko)
    fi
    if [ ${#missing[@]} -eq 0 ]; then
        ok "Dependencies found (Python $(python3 -c 'import sys;print(sys.version.split()[0])'), PyQt6, Paramiko)"
        return 0
    fi
    fail "Missing: ${missing[*]}"
    echo
    echo "  BitTwin uses the Qt and SSH libraries that ship with Kubuntu. Install them with:"
    echo "      sudo apt install $APT_PKGS"
    echo
    if command -v apt-get >/dev/null 2>&1 && [ -t 0 ]; then
        read -r -p "  Install them now? [y/N] " ans
        if [[ "$ans" =~ ^[Yy]$ ]]; then
            # shellcheck disable=SC2086  # word splitting of the package list is intended
            sudo apt-get update && sudo apt-get install -y $APT_PKGS
            return 0
        fi
    fi
    exit 1
}

run_tests() {
    say "running self-tests ${dim}(real copies, simulated corruption, SFTP)${reset}"
    local out
    if out=$(python3 -m unittest discover -s tests 2>&1); then
        ok "$(printf '%s\n' "$out" | grep -E '^Ran ' ) - all passed"
        return 0
    fi
    printf '%s\n' "$out" | tail -n 25
    fail "Some self-tests failed."
    return 1
}

MODE=run
ARGS=()
for a in "$@"; do
    case "$a" in
        --no-test) MODE=notest ;;
        --test)    MODE=testonly ;;
        -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) ARGS+=("$a") ;;
    esac
done

say "v$(python3 -c 'import bittwin;print(bittwin.__version__)' 2>/dev/null || echo '?')  ${dim}(portable run, nothing is installed)${reset}"
check_deps

case "$MODE" in
    testonly) run_tests; exit $? ;;
    run)
        if ! run_tests; then
            if [ -t 0 ]; then
                read -r -p "  Start BitTwin anyway? [y/N] " ans
                [[ "$ans" =~ ^[Yy]$ ]] || exit 1
            else
                exit 1
            fi
        fi ;;
esac

say "starting…"
exec python3 -m bittwin "${ARGS[@]+"${ARGS[@]}"}"
