#!/usr/bin/env bash
# Builds whisper.cpp's HTTP server with GPU support and installs it as a systemd user
# service that Talki can use (Settings > Speech recognition > whisper.cpp server).
#
#   scripts/build-whisper-cpp.sh [vulkan|hip|cpu] [model]
#
# Vulkan works on AMD, Intel and NVIDIA through Mesa/RADV and needs no ROCm.
set -euo pipefail

BACKEND="${1:-vulkan}"
MODEL="${2:-large-v3-turbo}"
VERSION="v1.9.4"
PORT="8178"
SRC="${XDG_CACHE_HOME:-$HOME/.cache}/talki/whisper.cpp"
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/talki/whisper.cpp"
MODELS="${XDG_DATA_HOME:-$HOME/.local/share}/talki/models"

case "$BACKEND" in
  vulkan) FLAGS=(-DGGML_VULKAN=ON); DEPS=(vulkan-headers shaderc glslang vulkan-icd-loader) ;;
  hip)    FLAGS=(-DGGML_HIP=ON); DEPS=(rocm-hip-sdk) ;;
  cpu)    FLAGS=(); DEPS=() ;;
  *) echo "backend must be vulkan, hip or cpu" >&2; exit 1 ;;
esac

missing=()
for p in cmake git "${DEPS[@]}"; do pacman -Qq "$p" &>/dev/null || missing+=("$p"); done
if ((${#missing[@]})); then
  echo "Installing build dependencies: ${missing[*]}"
  sudo pacman -S --needed "${missing[@]}"
fi

if [[ -d "$SRC/.git" ]]; then
  git -C "$SRC" fetch --depth 1 origin tag "$VERSION"
  git -C "$SRC" checkout -q "$VERSION"
else
  git clone --depth 1 --branch "$VERSION" https://github.com/ggml-org/whisper.cpp "$SRC"
fi

cmake -S "$SRC" -B "$SRC/build" -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_EXAMPLES=ON \
  -DWHISPER_BUILD_SERVER=ON -DBUILD_SHARED_LIBS=OFF "${FLAGS[@]}"
cmake --build "$SRC/build" -j"$(nproc)" --target whisper-server
mkdir -p "$PREFIX" "$MODELS"
install -m755 "$SRC/build/bin/whisper-server" "$PREFIX/whisper-server"

MODEL_FILE="$MODELS/ggml-$MODEL.bin"
if [[ ! -f "$MODEL_FILE" ]]; then
  echo "Downloading ggml-$MODEL.bin"
  curl -L --fail -o "$MODEL_FILE.part" "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-$MODEL.bin"
  mv "$MODEL_FILE.part" "$MODEL_FILE"
fi

VAD_FILE="$MODELS/ggml-silero-v5.1.2.bin"
if [[ ! -f "$VAD_FILE" ]]; then
  curl -L --fail -o "$VAD_FILE.part" "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin" \
    && mv "$VAD_FILE.part" "$VAD_FILE" || rm -f "$VAD_FILE.part"
fi
VAD_ARGS=""
[[ -f "$VAD_FILE" ]] && VAD_ARGS="--vad --vad-model $VAD_FILE"

UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/talki-whisper.service"
mkdir -p "$(dirname "$UNIT")"
cat > "$UNIT" <<EOF
[Unit]
Description=whisper.cpp server for Talki

[Service]
# On machines with an iGPU as well, pin the discrete GPU (see: vulkaninfo --summary).
Environment=GGML_VK_VISIBLE_DEVICES=0
ExecStart=$PREFIX/whisper-server --host 127.0.0.1 --port $PORT --model $MODEL_FILE --threads 4 $VAD_ARGS
Restart=on-failure

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now talki-whisper.service
echo
echo "whisper-server running on http://127.0.0.1:$PORT"
echo "In Talki: Settings > Speech recognition > Engine: whisper.cpp server"
