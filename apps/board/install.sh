#!/usr/bin/env bash
# Run the board as a systemd user service, plus the weekly page watch if Hermes is installed.
#   ./install.sh                 (safe to run again; BOARD_PORT and BOARD_DATA_DIR are honored)
#   ./install.sh --uninstall
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
UNITS="$HOME/.config/systemd/user"
PORT="${BOARD_PORT:-8765}"
DATA="${BOARD_DATA_DIR:-$HOME/.hermes/scholarship-board}"
HERMES_PY="${HERMES_AGENT_DIR:-$HOME/.hermes/hermes-agent}/venv/bin/python"

if [[ "${1:-}" == "--uninstall" ]]; then
  systemctl --user disable --now scholarship-board.service scholarship-board-watch.timer scholarship-board-inbox.path 2>/dev/null || true
  rm -f "$UNITS"/scholarship-board.service "$UNITS"/scholarship-board-watch.{service,timer} "$UNITS"/scholarship-board-inbox.{path,service}
  rm -f "$HOME/.hermes/scholarship-board.json"
  systemctl --user daemon-reload
  echo "board service removed; your data is still in $DATA"
  exit 0
fi

mkdir -p "$DATA"
chmod 700 "$DATA"
# Tell the research skill where the board's inbox is, so each search run updates the board
# (scholarship_api.send_to_board drops a request; scholarship-board-inbox.path imports it).
mkdir -p "$DATA/inbox"
chmod 700 "$DATA/inbox"
python3 - "$DATA" "$PORT" <<'PY'
import json, sys
from pathlib import Path
data, port = sys.argv[1:]
config = Path.home() / ".hermes" / "scholarship-board.json"
config.parent.mkdir(parents=True, exist_ok=True)
config.write_text(json.dumps({"inbox": f"{data}/inbox", "url": f"http://127.0.0.1:{port}"}, indent=1) + "\n")
PY
if ! systemctl --user show-environment >/dev/null 2>&1; then
  echo "systemd isn't running for your user (on WSL: add [boot] systemd=true to /etc/wsl.conf and restart WSL)."
  echo "Start the board by hand instead:  BOARD_DATA_DIR=$DATA python3 $HERE/server.py $PORT"
  exit 0
fi
mkdir -p "$UNITS"
cat > "$UNITS/scholarship-board.service" <<UNIT
[Unit]
Description=Scholarship board (127.0.0.1:$PORT)

[Service]
Environment=BOARD_DATA_DIR=$DATA
ExecStart=/usr/bin/env python3 $HERE/server.py $PORT
Restart=on-failure

[Install]
WantedBy=default.target
UNIT

cat > "$UNITS/scholarship-board-inbox.service" <<UNIT
[Unit]
Description=Scholarship board: add finished research runs from the inbox

[Service]
Type=oneshot
Environment=BOARD_DATA_DIR=$DATA
ExecStart=/usr/bin/env python3 $HERE/inbox.py
UNIT
cat > "$UNITS/scholarship-board-inbox.path" <<UNIT
[Unit]
Description=Watch the scholarship board inbox

[Path]
PathExistsGlob=$DATA/inbox/*.json

[Install]
WantedBy=default.target
UNIT

if [[ -x "$HERMES_PY" ]]; then
  cat > "$UNITS/scholarship-board-watch.service" <<UNIT
[Unit]
Description=Scholarship board: weekly page watch for awards not open yet

[Service]
Type=oneshot
Environment=BOARD_DATA_DIR=$DATA
ExecStart=$HERMES_PY $HERE/watch.py
TimeoutStartSec=900
UNIT
  cat > "$UNITS/scholarship-board-watch.timer" <<UNIT
[Unit]
Description=Weekly scholarship page watch (Mondays 8:00)

[Timer]
OnCalendar=Mon *-*-* 08:00:00
Persistent=true
RandomizedDelaySec=10min

[Install]
WantedBy=timers.target
UNIT
fi

systemctl --user daemon-reload
systemctl --user enable --now scholarship-board.service >/dev/null
systemctl --user restart scholarship-board.service
systemctl --user enable --now scholarship-board-inbox.path >/dev/null
if [[ -x "$HERMES_PY" ]]; then
  systemctl --user enable --now scholarship-board-watch.timer >/dev/null
  echo "weekly page watch on (Mondays 8:00)"
else
  echo "Hermes venv not found at $HERMES_PY: page watch skipped (set HERMES_AGENT_DIR and rerun)"
fi
sleep 1
code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/" || true)
echo "board: http://127.0.0.1:$PORT (HTTP $code; 401 means the login is on)"
[[ -f "$DATA/.auth.json" ]] || echo "no login yet: python3 $HERE/set_password.py YOUR_NAME   (do this before using Tailscale Funnel)"
echo "to keep it running after you log out: loginctl enable-linger \"$USER\""
