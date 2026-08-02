#!/usr/bin/env bash
#
# Drive the court-reserve-bot deployment on the remote Mac over SSH.
#
# Config (override via env or scripts/remote.env):
#   COURTBOT_HOST    ssh alias or user@host        (default: courtbot)
#   COURTBOT_REPO    repo path on the remote       (default: ~/projects/court-reserve-bot)
#   COURTBOT_TMUX    tmux session name             (default: courtbot)
#   COURTBOT_PYTHON  interpreter, relative to repo (default: .venv/bin/python)
#
# Usage: scripts/remote.sh <command> [args]
#   doctor          check ssh reachability and the remote toolchain
#   status          tmux session, bot process, git revision
#   logs [n]        last n lines of the bot log (default 60)
#   follow          stream the bot log until interrupted
#   pull            git pull on the remote
#   start           start the bot in a detached tmux session
#   stop [--force]  stop the tmux session; --force also kills a stray bot.py
#   restart         stop --force, then start
#   book [args]     run a one-off court_booking.py with the given args
#   exec <cmd>      run an arbitrary command in the remote repo

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
[ -f "$script_dir/remote.env" ] && . "$script_dir/remote.env"

HOST="${COURTBOT_HOST:-courtbot}"
REPO="${COURTBOT_REPO:-~/projects/court-reserve-bot}"
SESSION="${COURTBOT_TMUX:-courtbot}"
PYTHON="${COURTBOT_PYTHON:-.venv/bin/python}"
LOGFILE="logs/bot.out"

# BSD pgrep has no -a (full command line), so match on ps output instead. The
# [b] bracket keeps the grep from matching its own command line.
BOT_PS_CMD='out=$(ps -Ao pid,etime,command | grep "[b]ot\.py" || true)'

# A non-interactive ssh session on macOS gets a bare PATH that excludes
# Homebrew, so tmux is not found unless we put it back.
REMOTE_PREFIX='export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH";'

# Run a command inside the remote repo. $REPO is intentionally unquoted so a
# leading ~ expands on the remote side.
rsh() {
  ssh -o BatchMode=yes "$HOST" "$REMOTE_PREFIX cd $REPO && $*"
}

# Same, but with a TTY — needed for anything that streams or attaches.
rsh_tty() {
  ssh -t "$HOST" "$REMOTE_PREFIX cd $REPO && $*"
}

usage() {
  # Print the header comment block, stopping at the first non-comment line.
  awk 'NR>2 && /^#/ { sub(/^# ?/, ""); print; next } NR>2 { exit }' "${BASH_SOURCE[0]}"
  exit "${1:-0}"
}

cmd="${1:-}"
[ $# -gt 0 ] && shift || true

case "$cmd" in
  doctor)
    echo "==> ssh $HOST"
    if ! ssh -o BatchMode=yes -o ConnectTimeout=8 "$HOST" 'echo "    connected as $(whoami)@$(hostname -s)"'; then
      echo "    FAILED — see the bootstrap steps in README.md (Remote control over SSH)" >&2
      exit 1
    fi
    echo "==> remote toolchain"
    rsh 'for b in tmux git; do printf "    %-6s %s\n" "$b" "$(command -v $b || echo MISSING)"; done'
    echo "==> repo at $REPO"
    rsh "printf '    python %s\n' \"\$(command -v $PYTHON || echo MISSING)\"; \
         printf '    .env   %s\n' \"\$([ -f .env ] && echo present || echo MISSING)\"; \
         printf '    branch %s\n' \"\$(git rev-parse --abbrev-ref HEAD)\""
    ;;

  status)
    echo "==> tmux sessions"
    # Assign first: a `|| echo` after a pipe tests the pipe's last command,
    # which succeeds even when tmux/grep found nothing.
    rsh 'out=$(tmux ls 2>/dev/null || true); [ -n "$out" ] && echo "$out" | sed "s/^/    /" || echo "    (none)"'
    echo "==> bot process"
    rsh "$BOT_PS_CMD"' ; [ -n "$out" ] && echo "$out" | sed "s/^/    /" || echo "    not running"'
    echo "==> revision"
    rsh "git log -1 --format='    %h %s (%cr)'"
    ;;

  logs)
    rsh "tail -n ${1:-60} $LOGFILE 2>/dev/null || echo 'no $LOGFILE yet — was the bot started with this script?'"
    ;;

  follow)
    rsh_tty "tail -f $LOGFILE"
    ;;

  pull)
    rsh 'git pull --ff-only'
    ;;

  start)
    # Two Discord clients on one token means every scheduled run fires twice,
    # so refuse to start whenever any bot.py is already alive — including one
    # started by hand outside tmux, which `tmux has-session` would not see.
    existing=$(rsh "$BOT_PS_CMD"'; echo "$out"')
    if [ -n "$existing" ]; then
      echo "a bot.py is already running — refusing to start a second one:" >&2
      echo "$existing" | sed 's/^/    /' >&2
      echo "use 'stop --force' first, or restart" >&2
      exit 1
    fi
    # tee keeps a durable log; tmux scrollback alone is not readable over ssh.
    rsh "mkdir -p logs && tmux new-session -d -s $SESSION \
         \"cd $REPO && $PYTHON bot.py 2>&1 | tee -a $LOGFILE\""
    echo "started session '$SESSION'"
    ;;

  stop)
    if rsh "tmux kill-session -t $SESSION 2>/dev/null"; then
      echo "stopped tmux session '$SESSION'"
    else
      echo "no tmux session '$SESSION'"
    fi
    stray=$(rsh "$BOT_PS_CMD"'; echo "$out"')
    if [ -n "$stray" ]; then
      if [ "${1:-}" = "--force" ]; then
        # Kill by PID, the first ps column. Not xargs: BSD xargs has no -r and
        # would run a bare `kill` if the process vanished between calls.
        rsh "$BOT_PS_CMD"'; echo "$out" | awk "{print \$1}" | while read -r p; do [ -n "$p" ] && kill "$p" || true; done'
        echo "killed stray bot.py process(es)"
      else
        echo "a bot.py is still running outside tmux:" >&2
        echo "$stray" | sed 's/^/    /' >&2
        echo "re-run with 'stop --force' to kill it" >&2
        exit 1
      fi
    fi
    ;;

  restart)
    "$0" stop --force
    "$0" start
    ;;

  book)
    rsh_tty "HEADLESS=true $PYTHON court_booking.py $*"
    ;;

  exec)
    [ $# -gt 0 ] || usage 1
    rsh "$*"
    ;;

  ""|-h|--help|help)
    usage
    ;;

  *)
    echo "unknown command: $cmd" >&2
    usage 1
    ;;
esac
