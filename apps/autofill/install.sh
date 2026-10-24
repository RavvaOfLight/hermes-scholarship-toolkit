#!/usr/bin/env bash
# Install or update the autofill companion: the local service, then a copy of the browser
# extension that carries this computer's private key.
#   ./install.sh               (safe to run again after any change; then press Reload on the extension)
#   EXTENSION_DIR=/some/folder ./install.sh     to choose where the extension copy goes
#   ./install.sh --uninstall
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
STATE="$HOME/.hermes/scholarship-fill"
CFG="$STATE/companion.json"
UNIT="$HOME/.config/systemd/user/scholarship-autofill.service"

if [[ "${1:-}" == "--uninstall" ]]; then
  systemctl --user disable --now scholarship-autofill.service 2>/dev/null || true
  rm -f "$UNIT"; systemctl --user daemon-reload 2>/dev/null || true
  [[ -L "$STATE/app" ]] && rm "$STATE/app"
  echo "service removed. Your answers are still in $STATE/data; remove the extension in your browser."
  exit 0
fi

# Where the extension copy goes: on WSL, a Windows folder so Edge or Chrome on Windows can load it.
if [[ -z "${EXTENSION_DIR:-}" ]]; then
  if grep -qi microsoft /proc/version 2>/dev/null && command -v cmd.exe >/dev/null && command -v wslpath >/dev/null; then
    WINPROFILE="$(cd /mnt/c 2>/dev/null && cmd.exe /c 'echo %USERPROFILE%' 2>/dev/null | tr -d '\r')"
    EXTENSION_DIR="$(wslpath -u "$WINPROFILE")/ScholarshipAutofill"
  else
    EXTENSION_DIR="$HOME/.local/share/scholarship-autofill/extension"
  fi
fi

# 1. key + port (made once, owner-only)
python3 -c "import sys; sys.path.insert(0, sys.argv[1]); import autofill_server as s; s.load_config()" "$HERE"
read -r PORT TOKEN < <(python3 -c "import json, sys; c = json.load(open(sys.argv[1])); print(c['port'], c['token'])" "$CFG")

# 2. Hermes's skill finds the CLI here
mkdir -p "$STATE"
if [[ ! -e "$STATE/app" || -L "$STATE/app" ]]; then ln -sfn "$HERE" "$STATE/app"; fi

# 3. service (starts when you log in, restarts if it crashes)
if systemctl --user show-environment >/dev/null 2>&1; then
  mkdir -p "$(dirname "$UNIT")"
  cat > "$UNIT" <<UNITFILE
[Unit]
Description=Scholarship autofill companion (local answers for the browser extension)

[Service]
WorkingDirectory=$HERE
ExecStart=/usr/bin/env python3 $HERE/autofill_server.py
Restart=on-failure

[Install]
WantedBy=default.target
UNITFILE
  systemctl --user daemon-reload
  systemctl --user enable scholarship-autofill.service >/dev/null
  systemctl --user restart scholarship-autofill.service
else
  echo "systemd isn't running for your user (on WSL: add [boot] systemd=true to /etc/wsl.conf and restart WSL)."
  echo "Until then, start the service by hand:  python3 $HERE/autofill_server.py"
fi

# 4. extension copy with this computer's key
mkdir -p "$EXTENSION_DIR"
cp -r "$HERE/extension/." "$EXTENSION_DIR/"
printf 'self.AUTOFILL = { port: %s, token: "%s" };\n' "$PORT" "$TOKEN" > "$EXTENSION_DIR/config.js"

sleep 1
if curl -fsS "http://127.0.0.1:$PORT/v1/health" >/dev/null 2>&1; then echo "service OK on 127.0.0.1:$PORT"; else echo "service not answering yet on 127.0.0.1:$PORT"; fi
echo "extension folder: $EXTENSION_DIR${WINPROFILE:+  (Windows: $WINPROFILE\\ScholarshipAutofill)}"
echo "load it once: edge://extensions or chrome://extensions -> Developer mode -> Load unpacked -> that folder"
