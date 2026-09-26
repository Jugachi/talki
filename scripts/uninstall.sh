#!/usr/bin/env bash
# Removes Talki's program files. Pass --purge to also delete settings, history and models.
set -euo pipefail

DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
CONF="${XDG_CONFIG_HOME:-$HOME/.config}"

talki ctl quit 2>/dev/null || true
systemctl --user disable --now talki-whisper.service 2>/dev/null || true
rm -f "$CONF/systemd/user/talki-whisper.service"
systemctl --user daemon-reload 2>/dev/null || true
rm -f "$HOME/.local/bin/talki" "$DATA/applications/dev.talki.Talki.desktop" "$CONF/autostart/dev.talki.Talki.desktop"
rm -rf "$DATA/talki/venv" "$DATA/talki/whisper.cpp" "${XDG_CACHE_HOME:-$HOME/.cache}/talki"

if [[ "${1:-}" == "--purge" ]]; then
  rm -rf "$DATA/talki" "$CONF/talki"
  echo "Removed Talki and all of its data."
else
  echo "Removed Talki. Settings and history remain in $CONF/talki and $DATA/talki (use --purge to delete)."
fi
if [[ -f /etc/udev/rules.d/70-talki-uinput.rules ]]; then
  echo "To drop the uinput rule: sudo rm /etc/udev/rules.d/70-talki-uinput.rules /etc/modules-load.d/talki-uinput.conf"
fi
