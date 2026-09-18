#!/usr/bin/env bash
#
# Start, stop and inspect the three dev services in the background.
#
# Usage: scripts/dev-services.sh {up|down|status} [chat|scheduler|frontend|all]
#        scripts/dev-services.sh free-ports [chat|scheduler|all]
#
# `down` stops what this script started; `free-ports` stops what is on chat's and scheduler's
# ports however it got there, which is what a lost pid file or a hand-started service needs.
#
# Each service records its pid under .run/ and is stopped by that pid. That is the whole point
# of this script: the obvious alternative, `pkill -f "chat.main"`, also matches the command line
# of the *shell running that very command*, so it kills the caller along with the service - and
# any editor, agent or script whose command line happens to quote the module name. `kill` on a
# recorded pid, plus `pkill -P` for the child that `uv`/`npm` spawns, matches nothing by accident.
#
# Logs go to .run/<service>.log. Both .run/*.pid and .run/*.log are gitignored.
#
# Each service is started from this script's own environment, so a variable exported for the call
# reaches the service's process: `LOG_FORMAT=json make services-up` starts chat logging one JSON
# object per line, which is what the golden harness (`make eval-run`) requires - it reads a turn's
# events back out of .run/chat.log and stops before its first turn if they are not JSON. Without it
# chat uses its console format, for people. The variable only takes effect when chat starts, and
# `up` leaves an already-running service alone, so stop chat first (`make services-down`, or
# `scripts/dev-services.sh down chat`).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT/.run"
SERVICES=(scheduler chat frontend)   # scheduler first: chat dials it on the first booking turn

port_of() {
  case "$1" in
    chat) echo 8000 ;;
    scheduler) echo 8001 ;;
    frontend) echo 5173 ;;
  esac
}

start_one() {
  local name="$1" pidfile="$RUN_DIR/$1.pid" log="$RUN_DIR/$1.log"

  if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    echo "  $name already running (pid $(cat "$pidfile"))"
    return 0
  fi

  mkdir -p "$RUN_DIR"
  # `setsid` puts each service in its own process group, so the recorded pid is also a group id
  # and `stop_one` can signal the whole tree at once. Signalling only the pid leaves whatever it
  # spawned holding the port - `npm run dev` in particular reaches vite through a shell, so vite
  # is a grandchild and survives both `kill <pid>` and `pkill -P <pid>`.
  case "$name" in
    chat)
      setsid bash -c "cd '$ROOT' && exec uv run --package chat -- python -m chat.main" > "$log" 2>&1 &
      ;;
    scheduler)
      setsid bash -c "cd '$ROOT' && exec uv run --package scheduler -- python -m scheduler.main" > "$log" 2>&1 &
      ;;
    frontend)
      setsid bash -c "cd '$ROOT/services/frontend' && exec npm run dev" > "$log" 2>&1 &
      ;;
    *)
      echo "  unknown service: $name" >&2
      return 1
      ;;
  esac
  echo $! > "$pidfile"
  echo "  $name starting -> :$(port_of "$name")  (pid $(cat "$pidfile"), log .run/$name.log)"
}

stop_one() {
  local name="$1" pidfile="$RUN_DIR/$1.pid" pid

  if [ ! -f "$pidfile" ]; then
    echo "  $name not started by this script (no .run/$name.pid)"
    return 0
  fi
  pid="$(cat "$pidfile")"
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$pidfile"
    echo "  $name already stopped"
    return 0
  fi

  # The whole process group, not just the pid: see the `setsid` note in start_one. The negative
  # pid is what makes kill(2) signal the group.
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
  for _ in $(seq 1 20); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.25
  done
  kill -KILL -- "-$pid" 2>/dev/null
  rm -f "$pidfile"

  if ss -ltn 2>/dev/null | grep -q ":$(port_of "$name") "; then
    echo "  $name stopped, but port $(port_of "$name") is still bound - something else is on it"
  else
    echo "  $name stopped"
  fi
}

# The pids listening on a port, newline-separated. `ss` reports only sockets this user may see,
# which is exactly the set this script is allowed to kill anyway.
listener_pids() {
  ss -ltnpH "sport = :$1" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u
}

# The module name a service's own command line must contain before this script will kill its
# process. Only the two Python services have one: the frontend's command line is node/vite, which
# names nothing specific to this repo, so `free-ports` does not offer to kill it.
module_of() {
  case "$1" in
    chat) echo "chat.main" ;;
    scheduler) echo "scheduler.main" ;;
  esac
}

# Stop whatever is *listening on a service's port*, rather than whatever this script started.
# That is the difference from `down`: it recovers the case where the pid file is gone but the
# service is not - started by hand with `make run-chat-dev`, or left behind when .run/ was wiped.
# It is still not `pkill -f "chat.main"`: the pid comes from the listening socket, and is killed
# only once its own command line is confirmed to name that service's module, so an unrelated
# process on 8000 is reported and left alone.
free_one() {
  local name="$1" port pid pgid args module found=0 killed=0
  port="$(port_of "$name")"
  module="$(module_of "$name")"

  for pid in $(listener_pids "$port"); do
    found=1
    args="$(ps -p "$pid" -o args= 2>/dev/null)"
    if [[ "$args" != *"$module"* ]]; then
      echo "  $name port $port held by pid $pid, which is not $name - left alone: $args"
      continue
    fi
    # The listener is usually the `uv`-spawned child rather than the recorded pid, so signal its
    # whole process group. Never this script's own group, whatever ss reported.
    pgid="$(ps -p "$pid" -o pgid= 2>/dev/null | tr -d ' ')"
    if [ -n "$pgid" ] && [ "$pgid" != "$(ps -p $$ -o pgid= | tr -d ' ')" ]; then
      kill -TERM -- "-$pgid" 2>/dev/null
    else
      pgid=""
      kill -TERM "$pid" 2>/dev/null
    fi
    for _ in $(seq 1 20); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.25
    done
    if kill -0 "$pid" 2>/dev/null; then
      if [ -n "$pgid" ]; then
        kill -KILL -- "-$pgid" 2>/dev/null
      else
        kill -KILL "$pid" 2>/dev/null
      fi
    fi
    killed=1
    echo "  $name stopped (pid $pid was holding port $port)"
  done

  if [ "$found" = 0 ]; then
    echo "  $name port $port free"
  fi
  # A pid file naming a process that no longer exists is stale either way: drop it so the next
  # `up` starts rather than reporting the service already running.
  local recorded="$RUN_DIR/$name.pid"
  if [ "$killed" = 1 ] || { [ -f "$recorded" ] && ! kill -0 "$(cat "$recorded")" 2>/dev/null; }; then
    rm -f "$recorded"
  fi
}

status_one() {
  local name="$1" pidfile="$RUN_DIR/$1.pid" port state
  port="$(port_of "$name")"
  state="stopped"
  if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    state="running (pid $(cat "$pidfile"))"
  fi
  if ss -ltn 2>/dev/null | grep -q ":$port "; then
    printf "  %-10s %-26s port %s listening\n" "$name" "$state" "$port"
  else
    printf "  %-10s %-26s port %s free\n" "$name" "$state" "$port"
  fi
}

action="${1:-status}"
target="${2:-all}"
if [ "$target" = "all" ]; then
  # `free-ports` knows how to identify only the two Python services (see module_of).
  if [ "$action" = "free-ports" ]; then
    targets=(chat scheduler)
  else
    targets=("${SERVICES[@]}")
  fi
else
  targets=("$target")
fi

case "$action" in
  up)     for s in "${targets[@]}"; do start_one "$s"; done ;;
  down)   for s in "${targets[@]}"; do stop_one "$s"; done ;;
  status) for s in "${targets[@]}"; do status_one "$s"; done ;;
  free-ports)
    for s in "${targets[@]}"; do
      if [ -z "$(module_of "$s")" ]; then
        echo "  free-ports handles chat and scheduler only, not $s" >&2
        exit 2
      fi
      free_one "$s"
    done
    ;;
  *)
    echo "usage: $0 {up|down|status} [chat|scheduler|frontend|all]" >&2
    echo "       $0 free-ports [chat|scheduler|all]" >&2
    exit 2
    ;;
esac
