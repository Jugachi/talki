# Talki

Local voice dictation for Linux. Hold a key, speak, let go, and clean text appears at your cursor in any app. Speech recognition and AI editing both run on your own machine, so no audio or text ever leaves it.

Talki reimplements the feature set of Wispr Flow as a private, offline desktop app for Linux. It targets Arch Linux with KDE Plasma 6 on Wayland first. Other Wayland compositors and X11 work too, with the limitations listed under [Desktop support](#desktop-support).

## Features

- **Push-to-talk and hands-free.** Hold the shortcut to dictate and release it to insert the text. Double-tap to keep recording hands-free, press again to finish, or tap three times quickly to cancel.
- **Works in every app.** Text is pasted at the cursor through a virtual keyboard. Terminals get `Ctrl+Shift+V` automatically, and your clipboard is restored afterwards.
- **AI cleanup**, run by a local LLM:
  - Four levels: none, light, medium and high.
  - Removes filler words, stutters and false starts.
  - Applies spoken self-corrections ("at two, actually three" becomes "at 3").
  - Formats spoken lists as numbered lists.
  - Turns spoken punctuation, "new line" and "new paragraph" into the real thing.
- **Styles per app category.** Personal messages, work messages, email and everything else each get their own style: formal, casual, very casual or excited. Apps are recognised by window class, and browser tabs by title.
- **Chat-aware formatting.** Short chat messages lose their final period. Mid-sentence insertions continue in lowercase with a leading space. An optional "press enter" at the end of a dictation sends the message.
- **Personal dictionary.**
  - Custom words steer both recognition and cleanup.
  - "Misheard as" rules always get corrected.
  - Words you fix by hand right after dictating can be learned automatically.
- **Snippets.** Say a trigger phrase and Talki inserts a longer text. Matching ignores capitalization and works mid-sentence, and the longest matching trigger wins.
- **Command mode.** Select text, hold the command key and say what to do with it ("make this more formal", "translate to German"). With nothing selected, "hey Google / ask Claude / search Perplexity …" opens that search.
- **Transforms.** Up to 9 one-key rewrites of the current selection. Polish and Prompt Engineer come built in, and you can add your own.
- **About 100 languages.** Auto-detection runs once per dictation, and you can limit it to the languages you speak. A quick language switcher sits in the bar menu.
- **Developer mode.** In IDEs, identifiers near the cursor feed the vocabulary, and "at main dot py" becomes `@main.py`.
- **The bar.** A small overlay shows a live waveform while you speak. It never takes keyboard focus. Drag it anywhere on screen; the position is remembered. It has stop and cancel buttons, and its right-click menu holds the microphone and language pickers, "reset position" and "hide for 1 hour".
- **History.**
  - Searchable and grouped by day.
  - Audio playback, retry and flagging.
  - Copying the raw transcript undoes the AI edit.
  - Unfinished recordings are recovered after a crash.
- **Scratchpad.** Rich-text notes with dictation, pinning, search, autosave and version history.
- **Insights.** Words per minute (measured over speaking time only), total words, streaks, an activity heatmap, and a breakdown by category and app.
- **Audio.**
  - A ranked list of preferred microphones.
  - A microphone test.
  - Sound cues.
  - Pausing or muting media while you dictate.
  - A whisper mode for quiet speech.
  - A 20-minute cap per dictation, with a warning one minute before.
- **Customizable look.** Bar size (60–200%), colour presets (Midnight, Light, Ocean, Sunset, Forest, Neon) or your own colours, opacity, corner roundness and idle label. The Talki window can follow the system theme or use light or dark.
- **Privacy controls.** Choose whether history and audio are kept and for how long. Context awareness can be turned off.
- **Scriptable.** `talki ctl …` controls a running instance, so any compositor can bind keys to it.

## Install (Arch Linux)

```sh
git clone https://github.com/<you>/talki.git
cd talki
scripts/install.sh
```

The installer does the following:

1. Installs the system packages: `pyside6`, `layer-shell-qt`, `wl-clipboard`, `pipewire-audio`, `python-atspi`, `playerctl` and `uv`. On KDE it also installs `kdotool` from the AUR when `paru` or `yay` is available.
2. Grants your login seat access to `/dev/uinput` through a udev rule, which is needed for pasting.
3. Creates a virtual environment in `~/.local/share/talki/venv` and links `talki` into `~/.local/bin`.
4. Optionally builds **whisper.cpp with Vulkan** for GPU transcription (see below).
5. Optionally installs **Ollama** and pulls the cleanup model.
6. Optionally enables autostart.

Afterwards, run `talki doctor` at any time to check what works and what is missing.

On first launch, KDE asks you to confirm Talki's global shortcuts. You can change them later in *System Settings › Keyboard › Shortcuts › Talki*, or from Talki's settings.

## Default shortcuts

| Action | Shortcut |
|---|---|
| Push-to-talk (hold); double-tap for hands-free | `Ctrl+Meta+Space` |
| Hands-free start/stop | `Ctrl+Meta+H` |
| Command mode (hold, speak an instruction) | `Ctrl+Meta+C` |
| Cancel recording | `Ctrl+Meta+Esc`, or the ✕ on the bar |
| Paste last transcript | `Ctrl+Meta+V` |
| Copy last transcript | `Ctrl+Shift+Meta+C` |
| New scratchpad note by voice | `Ctrl+Meta+N` |
| Transform selection, slot 1–9 | `Ctrl+Meta+1` … `Ctrl+Meta+9` |

The desktop portal cannot bind modifier-only chords such as holding `Ctrl+Meta` alone, or mouse side buttons. Both are available with the **evdev** backend (Settings › Keyboard shortcuts). It reads raw input, so it requires `sudo usermod -aG input $USER` and a re-login.

## Speech recognition

Talki has two speech engines:

- **whisper.cpp server (recommended with a GPU).** `scripts/build-whisper-cpp.sh vulkan` builds `whisper-server` with Vulkan, downloads `large-v3-turbo`, and runs it as the systemd user service `talki-whisper`. On a Radeon RX 9070 XT a 5-second clip transcribes in about 0.3 s.
- **faster-whisper (built in).** It runs on the CPU with no extra setup, and the model downloads on first start. `large-v3-turbo` takes about 3 s for a 5-second clip on an 8-core CPU. For faster results pick `small` or `base` in Settings.

If your system has both an integrated and a discrete GPU, check `vulkaninfo --summary` and adjust `GGML_VK_VISIBLE_DEVICES` in `~/.config/systemd/user/talki-whisper.service`.

## AI cleanup (local LLM)

- **Default runtime:** [Ollama](https://ollama.com) with `qwen3.5:4b`. For better quality use `qwen3.5:9b`.
- **Other runtimes:** any OpenAI-compatible server works too, such as llama.cpp's `llama-server` or LM Studio. To use one, set *Settings › AI cleanup › API* to "OpenAI-compatible".
- **Without an LLM:** Talki still works. It falls back to rule-based formatting that removes fillers and handles spoken punctuation and capitalization.
- **AMD GPUs with an iGPU:** with `ollama-rocm`, add `HIP_VISIBLE_DEVICES=0` to the service (`sudo systemctl edit ollama`) so the discrete GPU is used.

## Desktop support

| | KDE Plasma 6 (Wayland) | GNOME 48+ (Wayland) | Hyprland / Sway | X11 |
|---|---|---|---|---|
| Global shortcuts | portal ✔ | portal ✔ | compositor binds + `talki ctl` | evdev |
| Paste into apps | ✔ uinput | ✔ uinput | ✔ uinput | ✔ uinput / xdotool |
| Per-app styles | ✔ kdotool | ✘ | ✔ hyprctl / swaymsg | ✔ xdotool |
| Overlay bar | ✔ layer-shell | fallback window | ✔ layer-shell | fallback window |

Example Hyprland binds for push-to-talk:

```
bind  = SUPER CTRL, SPACE, exec, talki ctl ptt-down
bindr = SUPER CTRL, SPACE, exec, talki ctl ptt-up
```

**Text-before-cursor context** is used for mid-sentence joins, auto-learning and IDE identifiers. It comes from AT-SPI accessibility:

- GTK apps and Firefox provide it out of the box.
- Qt apps need `QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1`.
- Chromium and Electron apps (VS Code, Cursor) need `--force-renderer-accessibility`.

## Command line

```
talki                 start the app (tray + bar)
talki ctl toggle      start/stop hands-free dictation (also: start, stop, cancel,
                      ptt-down, ptt-up, command-down, command-up, paste-last,
                      copy-last, scratchpad, transform <n>, show, status, quit)
talki devices         list microphones
talki doctor          check dependencies
talki config [key] [value]   read or change settings
```

## Files

| What | Where |
|---|---|
| Settings | `~/.config/talki/config.json` |
| History, dictionary, snippets and notes (SQLite) | `~/.local/share/talki/talki.db` |
| Stored audio | `~/.local/share/talki/audio/` |
| Log | `~/.local/share/talki/talki.log` |

## Development

```sh
uv venv --python /usr/bin/python3 --system-site-packages .venv
uv pip install -e . pytest
.venv/bin/pytest
.venv/bin/talki -v
```

The code is laid out as follows:

| Path | Contents |
|---|---|
| `src/talki/controller.py` | Recording state machine: hold, double-tap, lock and cancel |
| `src/talki/pipeline.py` | STT, then rules, then LLM, then rules |
| `src/talki/textproc.py` | Deterministic text rules, unit tested |
| `src/talki/inject.py` | Clipboard and uinput paste |
| `src/talki/hotkeys/` | Portal and evdev hotkey backends |
| `src/talki/ui/` | Qt hub window and QML layer-shell overlay |

Uninstall with `scripts/uninstall.sh`. Add `--purge` to also delete your data.

## License

MIT
