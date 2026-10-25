#!/usr/bin/env bash
#
# Pull new commits and restart the bot. Runs on the bot Mac, from launchd or
# from `remote.sh deploy`.
#
# Usage: scripts/autodeploy.sh [--dry-run]
#   --dry-run  print the guard decision and the pending commits, change nothing

set -uo pipefail

# launchd starts jobs with a bare PATH that excludes Homebrew, so tmux is not found.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1
mkdir -p logs

SESSION="${COURTBOT_TMUX:-courtbot}"
WA_SESSION="$SESSION-wa"
PYTHON="${COURTBOT_PYTHON:-.venv/bin/python}"
NODE="${COURTBOT_NODE:-node}"
LOGFILE="logs/bot.out"
WA_LOGFILE="logs/whatsapp.out"
DEPLOY_LOG="logs/autodeploy.log"
LOCK="logs/autodeploy.lock"

# Keep in sync with BOT_PS_CMD in remote.sh.
BOT_PS_CMD='out=$(ps -Ao pid,etime,command | grep -E "^ *[0-9]+ +[^ ]+ +([^ ]*/)?(python[0-9.]*|uv) (run )?bot\.py" || true)'

dry_run=0
[ "${1:-}" = "--dry-run" ] && dry_run=1

# Dry runs print to the terminal. Real runs append to the deploy log.
say() {
  local line
  line="$(date '+%Y-%m-%d %H:%M:%S') $*"
  if [ "$dry_run" = 1 ]; then
    echo "$line"
  else
    echo "$line" >>"$DEPLOY_LOG"
  fi
}

notify_failure() {
  "$PYTHON" -c '
import sys
from dotenv import load_dotenv
load_dotenv(".env")
from utils.discord import send_discord_notification
send_discord_notification(sys.argv[1], title="Bot auto-deploy failed", success=False)
' "$1" >>"$DEPLOY_LOG" 2>&1 || say "discord notice failed"
}

# A failure that repeats on every poll posts one notice. The marker holds the
# last failure text, and a successful deploy clears it.
FAILED_MARK="logs/autodeploy.failed"

fail() {
  if [ "$dry_run" = 1 ]; then
    echo "FAILED: $*"
    exit 1
  fi
  if [ "$(cat "$FAILED_MARK" 2>/dev/null)" = "$*" ]; then
    exit 1
  fi
  echo "$*" >"$FAILED_MARK"
  say "FAILED: $*"
  notify_failure "$*"
  exit 1
}

# Everything below runs inside one function. bash reads a script as it runs,
# so the `git pull` below would otherwise rewrite this file under the reader.
main() {
if [ "$dry_run" = 0 ]; then
  if ! mkdir "$LOCK" 2>/dev/null; then
    # A killed run or a reboot leaves the lock behind. Reclaim it when its owner is gone.
    if kill -0 "$(cat "$LOCK/pid" 2>/dev/null)" 2>/dev/null; then
      say "skip: another deploy holds $LOCK"
      exit 0
    fi
    rm -rf "$LOCK"
    mkdir "$LOCK" || exit 0
  fi
  echo $$ >"$LOCK/pid"
  trap 'rm -rf "$LOCK"' EXIT
fi

# Guards run before the fetch. A refusal leaves the checkout alone, so the
# next poll still sees the new commit and retries.
if "$PYTHON" -c '
import sys
from datetime import datetime
from constants import LOCAL_TZ
from utils.roster_loop import QUIET_START, QUIET_END
now = datetime.now(LOCAL_TZ)
sys.exit(0 if QUIET_START <= (now.hour, now.minute) < QUIET_END else 1)
' 2>/dev/null; then
  quiet=1
else
  rc=$?
  # Exit 1 means outside the window. Anything else means the import failed.
  [ "$rc" = 1 ] || { say "refuse: could not read the quiet window"; exit 0; }
  quiet=0
fi

refusal=""
if [ "$quiet" = 1 ]; then
  refusal="inside the booking quiet window"
elif pgrep -f 'court_booking.py|open_play.py' >/dev/null; then
  refusal="a booking or open play run is active"
elif [ -n "$(git status --porcelain)" ]; then
  refusal="working tree is dirty"
fi

git fetch --quiet || { [ "$dry_run" = 1 ] && echo "fetch failed"; say "fetch failed"; exit 0; }
upstream="$(git rev-parse --abbrev-ref '@{u}' 2>/dev/null)" || fail "branch has no upstream"
head="$(git rev-parse HEAD)"
target="$(git rev-parse '@{u}')"

if [ "$dry_run" = 1 ]; then
  echo "branch $(git rev-parse --abbrev-ref HEAD) -> $upstream"
  if [ -n "$refusal" ]; then echo "guard: REFUSE ($refusal)"; else echo "guard: ok"; fi
  if [ "$head" = "$target" ]; then
    echo "pending: none"
  else
    echo "pending:"
    git log --format='  %h %s' "$head..$target"
  fi
  exit 0
fi

# A step that failed after the pull leaves this marker. It holds the pre-pull
# commit, then the changed files, so the next poll finishes the deploy.
PENDING="logs/autodeploy.pending"
[ "$head" = "$target" ] && [ ! -f "$PENDING" ] && exit 0

if [ -n "$refusal" ]; then
  say "refuse: $refusal (pending ${target:0:7})"
  exit 0
fi

if [ -f "$PENDING" ]; then
  from="$(head -n 1 "$PENDING")"
  changed="$(tail -n +2 "$PENDING")"
else
  from="$head"
  changed="$(git diff --name-only "$head" "$target")"
  git pull --ff-only --quiet || fail "git pull --ff-only failed at ${head:0:7}"
  { echo "$from"; echo "$changed"; } >"$PENDING"
fi

if echo "$changed" | grep -qx 'requirements.txt'; then
  "$PYTHON" -m pip install -r requirements.txt >>"$DEPLOY_LOG" 2>&1 \
    || fail "pip install failed after pulling ${target:0:7}"
fi

if echo "$changed" | grep -q '^whatsapp/'; then
  tmux kill-session -t "=$WA_SESSION" 2>/dev/null || true
  tmux new-session -d -s "$WA_SESSION" \
    "cd $REPO/whatsapp && $NODE server.js 2>&1 | tee -a ../$WA_LOGFILE" \
    || fail "whatsapp sidecar restart failed after pulling ${target:0:7}"
fi

# The restart is not retried: a bot that fails to come up stays down.
rm -f "$PENDING"
# Same stop and start steps as `remote.sh restart`.
tmux kill-session -t "=$SESSION" 2>/dev/null || true
eval "$BOT_PS_CMD"
if [ -n "$out" ]; then
  echo "$out" | awk '{print $1}' | while read -r p; do [ -n "$p" ] && kill "$p" || true; done
  sleep 2
fi
eval "$BOT_PS_CMD"
[ -z "$out" ] || fail "old bot.py would not stop: $out"

tmux new-session -d -s "$SESSION" \
  "cd $REPO && $PYTHON bot.py 2>&1 | tee -a $LOGFILE" \
  || fail "tmux could not start the bot after pulling ${target:0:7}"

sleep 30
eval "$BOT_PS_CMD"
[ -n "$out" ] || fail "bot.py is not running 30s after restart at ${target:0:7}"

rm -f "$FAILED_MARK" "$PENDING"
say "deployed ${from:0:7} -> ${target:0:7}: $(git log -1 --format=%s)"
}

main "$@"
exit
