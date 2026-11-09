#!/usr/bin/env bash
# Open the picker on an isolated tmux server with made-up sessions. Uses temporary transcripts,
# index and claude-tasks state, so it never touches your real ones. Semantic search is off, so
# no ollama is needed. Handy for trying keybindings or taking screenshots.
#
#   scripts/demo.sh            attach to the demo (Esc to quit, then the server is killed)
#   scripts/demo.sh --capture  type a query, print the picker as plain text and exit

set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
dir="$(mktemp -d)"
conf="$dir/tmux.conf"
sock=claude-sessions-demo
T() { tmux -L "$sock" -f "$conf" "$@"; }
trap 'T kill-server 2>/dev/null || true; rm -rf -- "$dir"' EXIT

printf 'set -g base-index 1\nset -g pane-base-index 1\n' >"$conf"
mkdir -p "$dir/claude/projects" "$dir/tasks"

# Transcripts: id, project folder, title (empty: none), minutes since last message, exchanges.
python3 - "$dir/claude/projects" "$HOME" <<'EOF'
import json, sys, time, uuid
from datetime import datetime, timezone
from pathlib import Path

out, home = Path(sys.argv[1]), sys.argv[2]
sessions = [
    ("api", "code/api", "Add rate limiting to /login", 6, [
        ("Add rate limiting to the /login endpoint, 5 attempts per minute per IP.",
         "I added a sliding-window limiter backed by Redis and return 429 with Retry-After."),
    ]),
    ("audio", "code/dotfiles", "Bluetooth headphones keep disconnecting", 4, [
        ("My bluetooth headphones disconnect every few minutes on Plasma.",
         "Disable the headset's power saving: set AutoEnable and FastConnectable in main.conf."),
    ]),
    ("rental", "code/scraper", "Car rental price scraper", 4300, [
        ("Write a scraper that compares car rental prices for a date range.",
         "I wrote it with httpx and selectolax; results are saved as CSV per provider."),
    ]),
    ("rc", "code/rc-car", "Bluetooth RC car controller in Rust", 140, [
        ("I'm building a small RC car on an ESP32. Can you set up a Rust project for it?",
         "Sure. I created an esp-idf project with a motor driver module and a PWM helper."),
        ("Now let me drive the car from my phone over Bluetooth.",
         "I added a BLE GATT service with a steering and a throttle characteristic, so any\n"
         "BLE app can write to them. The car stops if no command arrives for 500 ms."),
        ("The car drifts left at full throttle.",
         "That's motor mismatch. I added a per-motor trim value to the config."),
    ]),
    ("pad", "code/dotfiles", None, 1500, [
        ("how do I pair a ps4 controller over bluetooth on linux and use it in steam",
         "Hold PS + Share until the light bar flashes, then pair it with bluetoothctl."),
    ]),
    ("e2e", "code/webshop", "Fix flaky checkout e2e test", 2900, [
        ("The checkout e2e test fails about once in ten runs.",
         "The test clicks Pay before the payment form is ready. I wait for the iframe now."),
    ]),
    ("nvim", "code/dotfiles", "Migrate nvim config to lazy.nvim", 8000, [
        ("Migrate my packer config to lazy.nvim.", "Done: every plugin is now a lazy spec."),
    ]),
    ("thesis", "school/thesis", "Summarize related work chapter", 15000, [
        ("Summarize the related work chapter in five bullet points.", "Here are five bullets."),
    ]),
    ("trip", "notes", "Vietnam trip packing checklist", 30000, [
        ("Make me a packing checklist for two weeks in Vietnam.", "Here is a checklist."),
    ]),
]
now = time.time()
for name, folder, title, minutes, exchanges in sessions:
    sid = str(uuid.uuid5(uuid.NAMESPACE_URL, "claude-sessions-demo/" + name))
    cwd = f"{home}/{folder}"
    ts = datetime.fromtimestamp(now - minutes * 60, timezone.utc).isoformat().replace("+00:00", "Z")
    base = {"cwd": cwd, "gitBranch": "main", "timestamp": ts, "entrypoint": "cli"}
    recs = []
    for prompt, reply in exchanges:
        recs.append({**base, "type": "user", "message": {"role": "user", "content": prompt}})
        recs.append({**base, "type": "assistant",
                     "message": {"role": "assistant", "content": [{"type": "text", "text": reply}]}})
    if title:
        recs.append({"type": "ai-title", "aiTitle": title, "sessionId": sid})
    d = out / cwd.replace("/", "-")
    d.mkdir(exist_ok=True)
    (d / f"{sid}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
EOF

# One session is open in a tmux pane, as tmux-claude-tasks would record it.
pane=$(T new-session -d -P -F '#{pane_id}' -s work sleep 3600)
sid=$(python3 -c 'import uuid; print(uuid.uuid5(uuid.NAMESPACE_URL, "claude-sessions-demo/audio"))')
printf 'running\t%s\t%s\t%s\n' "$(date +%s)" "$HOME/code/dotfiles" "$sid" >"$dir/tasks/${pane#%}"

T new-session -d -s picker -x 108 -y 27 \
    "CLAUDE_CONFIG_DIR=$dir/claude CLAUDE_SESSIONS_DIR=$dir/state CLAUDE_TASKS_DIR=$dir/tasks \
     CLAUDE_SESSIONS_MODEL=none $root/claude-sessions"

if [[ "${1:-}" == --capture ]]; then
    sleep 1.5
    T send-keys -t picker 'bluetooth car'
    sleep 1.5
    T capture-pane -p ${CAPTURE_ANSI:+-e} -t picker
else
    TMUX='' T attach -t picker
fi
