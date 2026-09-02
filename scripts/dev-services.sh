#!/usr/bin/env bash
#
# Start, stop and inspect the three dev services in the background.
#
# Usage: scripts/dev-services.sh {up|down|status} [chat|scheduler|frontend|all]
#
# Each service records its pid under .run/ and is stopped by that pid. That is the whole point
# of this script: the obvious alternative, `pkill -f "chat.main"`, also matches the command line
# of the *shell running that very command*, so it kills the caller along with the service - and
# any editor, agent or script whose command line happens to quote the module name. `kill` on a
# recorded pid, plus `pkill -P` for the child that `uv`/`npm` spawns, matches nothing by accident.
#
# Logs go to .run/<service>.log. Both .run/*.pid and .run/*.log are gitignored.
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
  targets=("${SERVICES[@]}")
else
  targets=("$target")
fi

case "$action" in
  up)     for s in "${targets[@]}"; do start_one "$s"; done ;;
  down)   for s in "${targets[@]}"; do stop_one "$s"; done ;;
  status) for s in "${targets[@]}"; do status_one "$s"; done ;;
  *)
    echo "usage: $0 {up|down|status} [chat|scheduler|frontend|all]" >&2
    exit 2
    ;;
esac
