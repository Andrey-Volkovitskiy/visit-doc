#!/usr/bin/env bash
#
# Drive a conversation from the shell, against a chat service already running on :8000.
#
# Usage:
#   scripts/dev-chat.sh session                 # mint a session + its first chat, print the id
#   scripts/dev-chat.sh chat                    # another chat in that session, print its id
#   scripts/dev-chat.sh say <chat_id> "text"    # post a patient turn, stream the reply
#   scripts/dev-chat.sh thread <chat_id>        # the whole thread, one line per message
#   scripts/dev-chat.sh console                 # the staff listing: emphasis, marks, pause
#   scripts/dev-chat.sh staff <chat_id> "text"  # post into the thread as staff
#   scripts/dev-chat.sh assistant <chat_id> on|off
#   scripts/dev-chat.sh faq "text"              # add a FAQ entry (embeds via the live API)
#
# The session cookie is HttpOnly and every route is scoped by it, so all of this hangs off one
# jar (.run/dev.jar, override with VISITDOC_JAR): lose it and the ids you were holding resolve to
# nothing, exactly as they would for a browser that cleared its cookies.
#
# A patient turn spends real Claude and Voyage calls, which is expected for manual testing (see
# docs/testing-strategy.md). A turn into a *paused* conversation spends none - the message is
# stored and marked, and no reply is generated - which is the cheap way to exercise marks,
# emphasis and the console.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JAR="${VISITDOC_JAR:-$ROOT/.run/dev.jar}"
BASE="${VISITDOC_BASE:-localhost:8000}"
mkdir -p "$(dirname "$JAR")"

# Built with json.dumps rather than a printf template, so a message carrying a quote or a
# newline reaches the API as the text that was typed instead of as a parse error.
json() { python3 -c 'import json,sys; print(json.dumps(dict(zip(sys.argv[1::2], sys.argv[2::2]))))' "$@"; }

case "${1:-}" in
  session)
    rm -f "$JAR"
    curl -s -c "$JAR" -X POST "$BASE/chats" |
      python3 -c 'import json,sys; print("session minted, first chat:", json.load(sys.stdin)["id"])'
    ;;

  chat)
    curl -s -b "$JAR" -c "$JAR" -X POST "$BASE/chats" |
      python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])'
    ;;

  say)
    curl -s -N -b "$JAR" -X POST "$BASE/chat" -H 'content-type: application/json' \
      -d "$(json chat_id "$2" message "$3" local_now "$(date +%Y-%m-%dT%H:%M:%S)")"
    ;;

  thread)
    curl -s -b "$JAR" "$BASE/chats/$2/messages" | python3 -c '
import json, sys
for m in json.load(sys.stdin)["messages"]:
    print("%-10s %-26s %s" % (m["sender"], m["attention_mark"] or "-", m["content"][:70]))
'
    ;;

  console)
    curl -s -b "$JAR" "$BASE/console/conversations" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print("needs a person:", d["attention_total"])
for c in d["conversations"]:
    print("  %s  emph=%-5s esc=%-5s reason=%-24s may_reply=%-5s pause=%s" % (
        c["chat_id"], c["emphasized"], c["escalated"], c["escalation_reason"],
        c["assistant_may_reply"], c["pause_seconds_remaining"]))
'
    ;;

  staff)
    curl -s -b "$JAR" -X POST "$BASE/console/chats/$2/messages" \
      -H 'content-type: application/json' -d "$(json content "$3")"
    echo
    ;;

  assistant)
    enabled=false
    [ "${3:-}" = "on" ] && enabled=true
    curl -s -b "$JAR" -X POST "$BASE/console/chats/$2/assistant" \
      -H 'content-type: application/json' -d "{\"enabled\":$enabled}"
    echo
    ;;

  faq)
    curl -s -b "$JAR" -X POST "$BASE/faq" -H 'content-type: application/json' \
      -d "$(json content "$2")"
    echo
    ;;

  *)
    sed -n '3,21p' "$0" >&2
    exit 2
    ;;
esac
