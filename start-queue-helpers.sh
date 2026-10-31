#!/usr/bin/bash
# Start cancel-stuck-downloads.py and approve-reviews.py in --watch mode,
# detached from the terminal. Each exits by itself once aurral's queue is
# done. A watcher that is already running is left alone (its lock blocks a
# second copy). Logs: ~/.cache/aurral-stuck-downloads/watch.log and
# ~/.cache/aurral-approve-reviews/watch.log
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/.cache/aurral-stuck-downloads" "$HOME/.cache/aurral-approve-reviews"

setsid nohup "$here/cancel-stuck-downloads.py" --watch \
    >> "$HOME/.cache/aurral-stuck-downloads/watch.log" 2>&1 < /dev/null &
setsid nohup "$here/approve-reviews.py" --watch \
    >> "$HOME/.cache/aurral-approve-reviews/watch.log" 2>&1 < /dev/null &

sleep 3
pgrep -af 'cancel-stuck-downloads.py --watch|approve-reviews.py --watch' || true
