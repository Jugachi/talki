#!/usr/bin/env bash
# Installs Talki for the current user on Arch Linux.
#   scripts/install.sh            interactive
#   scripts/install.sh --yes      accept all optional components
set -euo pipefail

cd "$(dirname "$0")/.."
REPO="$PWD"
YES=0
[[ "${1:-}" == "--yes" ]] && YES=1
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
VENV="$DATA/talki/venv"
BIN="$HOME/.local/bin"

ask() {
  ((YES)) && return 0
  read -r -p "$1 [Y/n] " a
  [[ -z "$a" || "$a" =~ ^[Yy] ]]
}

if ! command -v pacman >/dev/null; then
  echo "This installer targets Arch Linux (pacman)." >&2
  exit 1
fi

echo "==> System packages"
PKGS=(python pyside6 layer-shell-qt pipewire-audio wireplumber wl-clipboard python-atspi playerctl uv)
[[ -z "${WAYLAND_DISPLAY:-}" ]] && PKGS+=(xclip xdotool)
sudo pacman -S --needed "${PKGS[@]}"

if [[ "${XDG_CURRENT_DESKTOP:-}" == *KDE* ]] && ! command -v kdotool >/dev/null; then
  if command -v paru >/dev/null; then paru -S --needed kdotool
  elif command -v yay >/dev/null; then yay -S --needed kdotool
  else echo "   Install the AUR package 'kdotool' for per-app styles on KDE Wayland."
  fi
fi

echo "==> Virtual keyboard access (/dev/uinput)"
if [[ ! -w /dev/uinput ]]; then
  sudo install -Dm644 packaging/70-talki-uinput.rules /etc/udev/rules.d/70-talki-uinput.rules
  echo uinput | sudo tee /etc/modules-load.d/talki-uinput.conf >/dev/null
  sudo modprobe uinput
  sudo udevadm control --reload-rules
  sudo udevadm trigger --name-match=uinput
fi
[[ -w /dev/uinput ]] && echo "   ok" || echo "   /dev/uinput still not writable; log out and back in."

echo "==> Python environment ($VENV)"
# System site-packages give us the distro PySide6, which matches layer-shell-qt's Qt build.
uv venv --allow-existing --python /usr/bin/python3 --system-site-packages "$VENV"
uv pip install --python "$VENV/bin/python" "$REPO"
mkdir -p "$BIN"
ln -sf "$VENV/bin/talki" "$BIN/talki"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "   Add $BIN to your PATH." ;; esac

echo "==> Desktop entry"
install -Dm644 packaging/dev.talki.Talki.desktop "$DATA/applications/dev.talki.Talki.desktop"
sed -i "s|^Exec=talki|Exec=$BIN/talki|" "$DATA/applications/dev.talki.Talki.desktop"
update-desktop-database "$DATA/applications" 2>/dev/null || true

GPU=$(lspci 2>/dev/null | grep -iE 'vga|3d' || true)
if ask "Build whisper.cpp with Vulkan GPU acceleration (recommended for AMD/Intel/NVIDIA GPUs, ~1.6 GB model)?"; then
  scripts/build-whisper-cpp.sh vulkan large-v3-turbo
  "$BIN/talki" config stt.backend whisper-server >/dev/null
else
  echo "   Using faster-whisper on the CPU. The model downloads on first start."
fi

if ask "Install Ollama for AI cleanup, styles, command mode and transforms?"; then
  if grep -qi 'amd\|radeon' <<<"$GPU"; then OLLAMA=ollama-rocm
  elif grep -qi nvidia <<<"$GPU"; then OLLAMA=ollama-cuda
  else OLLAMA=ollama-vulkan
  fi
  sudo pacman -S --needed "$OLLAMA"
  sudo systemctl enable --now ollama.service
  sleep 2
  MODEL=$("$BIN/talki" config llm.model | tr -d '"')
  ollama pull "$MODEL"
fi

if ask "Start Talki automatically when you log in?"; then
  "$BIN/talki" config ui.autostart true >/dev/null
  mkdir -p "$HOME/.config/autostart"
  cp "$DATA/applications/dev.talki.Talki.desktop" "$HOME/.config/autostart/dev.talki.Talki.desktop"
fi

echo
"$BIN/talki" doctor || true
echo
echo "Done. Start Talki from your app launcher or run: talki"
echo "On first start KDE asks you to confirm Talki's global shortcuts."
