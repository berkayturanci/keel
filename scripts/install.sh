#!/usr/bin/env sh
# Keel Standalone Installer (curl -fsSL https://raw.githubusercontent.com/berkayturanci/keel/main/scripts/install.sh | sh)
# Project-neutral multi-agent workflow core and autonomous issue shipping backbone.
set -e

REPO="berkayturanci/keel"
PACKAGE="keel-workflow"
INSTALL_DIR="${KEEL_INSTALL_DIR:-$HOME/.local/share/keel}"
BIN_DIR="${KEEL_BIN_DIR:-$HOME/.local/bin}"

# Text formatting
if [ -t 1 ]; then
    BOLD="\033[1m"
    GREEN="\033[32m"
    CYAN="\033[36m"
    YELLOW="\033[33m"
    RED="\033[31m"
    RESET="\033[0m"
else
    BOLD=""
    GREEN=""
    CYAN=""
    YELLOW=""
    RED=""
    RESET=""
fi

printf "${CYAN}${BOLD}⚓ Keel Standalone Installer${RESET}\n"
printf "Multi-agent workflow core & autonomous issue shipping backbone\n\n"

if [ -n "$DRY_RUN" ]; then
    printf "${YELLOW}[DRY-RUN] Script running in verification mode.${RESET}\n"
    printf "Detected Target Dir: %s\n" "$INSTALL_DIR"
    printf "Detected Bin Dir   : %s\n" "$BIN_DIR"
    exit 0
fi

# Detect Python 3.10+
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        version=$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [ "$major" = "3" ] && [ "$minor" -ge 10 ] 2>/dev/null; then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    printf "${RED}Error: Keel requires Python 3.10 or newer.${RESET}\n"
    printf "Please install Python from https://python.org or your system package manager.\n"
    exit 1
fi

printf "✔ Found Python: %s ($($PYTHON --version))\n" "$PYTHON"

# Method 1: pipx (if available)
if command -v pipx >/dev/null 2>&1; then
    printf "✔ Using pipx to install %s...\n" "$PACKAGE"
    pipx install "$PACKAGE" --force || pipx upgrade "$PACKAGE"
    printf "\n${GREEN}${BOLD}🎉 Keel installed successfully via pipx!${RESET}\n"
    exit 0
fi

# Method 2: uv (if available)
if command -v uv >/dev/null 2>&1; then
    printf "✔ Using uv tool to install %s...\n" "$PACKAGE"
    uv tool install "$PACKAGE" --force
    printf "\n${GREEN}${BOLD}🎉 Keel installed successfully via uv!${RESET}\n"
    exit 0
fi

# Method 3: Isolated virtualenv in ~/.local/share/keel
printf "✔ Creating dedicated virtual environment at %s...\n" "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
mkdir -p "$BIN_DIR"

"$PYTHON" -m venv "$INSTALL_DIR"
"$INSTALL_DIR/bin/pip" install --upgrade pip --quiet
"$INSTALL_DIR/bin/pip" install "$PACKAGE" --quiet

# Symlink executable into $BIN_DIR
ln -sf "$INSTALL_DIR/bin/keel" "$BIN_DIR/keel"

printf "✔ Symlinked executable to %s/keel\n" "$BIN_DIR"

# Verify path
case ":$PATH:" in
    *":$BIN_DIR:"*)
        PATH_OK=1
        ;;
    *)
        PATH_OK=0
        ;;
esac

printf "\n${GREEN}${BOLD}🎉 Keel installed successfully!${RESET}\n"
if [ "$PATH_OK" -eq 1 ]; then
    "$BIN_DIR/keel" version || true
    printf "Run ${CYAN}keel init --auto${RESET} or ${CYAN}keel setup${RESET} inside any repository to get started.\n"
else
    printf "${YELLOW}Notice: %s is not in your current PATH.${RESET}\n" "$BIN_DIR"
    printf "Add it by running:\n"
    printf "  ${BOLD}export PATH=\"%s:\$PATH\"${RESET}\n\n" "$BIN_DIR"
    printf "Or add that line to your ${BOLD}~/.bashrc${RESET} or ${BOLD}~/.zshrc${RESET}.\n"
fi
