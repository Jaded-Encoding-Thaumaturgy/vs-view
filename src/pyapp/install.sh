#!/usr/bin/env bash
set -euo pipefail

APP_NAME="VSView"
ICON_NAME="vsview"
DESKTOP_NAME="vsview"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
DATA_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}"

echo "Installing ${APP_NAME}..."
echo ""

# Install binary
mkdir -p "${BIN_DIR}"
install -m 755 "${SCRIPT_DIR}/${APP_NAME}" "${BIN_DIR}/${APP_NAME}"
echo "  ✔️  - Binary     → ${BIN_DIR}/${APP_NAME}"

# Install icons
if [ -d "${SCRIPT_DIR}/icons" ]; then
    for size_dir in "${SCRIPT_DIR}"/icons/*/; do
        [ -d "${size_dir}" ] || continue
        size="$(basename "${size_dir}")"
        icon_dest="${DATA_DIR}/icons/hicolor/${size}/apps"
        mkdir -p "${icon_dest}"
        install -m 644 "${size_dir}/${ICON_NAME}.png" "${icon_dest}/${ICON_NAME}.png"
    done
    echo "  ✔️  - Icons      → ${DATA_DIR}/icons/hicolor/*/apps/${ICON_NAME}.png"
fi

# Create .desktop entry
mkdir -p "${DATA_DIR}/applications"
cat > "${DATA_DIR}/applications/${DESKTOP_NAME}.desktop" << DESKTOP
[Desktop Entry]
Type=Application
Name=${APP_NAME}
Exec=${BIN_DIR}/${APP_NAME}
Icon=${ICON_NAME}
Terminal=true
Categories=Video;AudioVideo;
Comment=VapourSynth frame previewer
StartupWMClass=${DESKTOP_NAME}
DESKTOP

echo "  ✔️  - Desktop    → ${DATA_DIR}/applications/${DESKTOP_NAME}.desktop"

# Update system caches
gtk-update-icon-cache -f -t "${DATA_DIR}/icons/hicolor" 2>/dev/null || true
update-desktop-database "${DATA_DIR}/applications" 2>/dev/null || true

echo ""
echo "${APP_NAME} has been installed successfully!"

# Check PATH
case ":${PATH}:" in
    *":${BIN_DIR}:"*)
    ;;
    *)
        echo ""
        echo "⚠  ${BIN_DIR} is not in your PATH."
        echo "   Add this to your shell profile:"
        echo "   export PATH=\"\${HOME}/.local/bin:\${PATH}\""
    ;;
esac
