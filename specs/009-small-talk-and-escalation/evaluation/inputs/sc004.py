"""SC-004: the same bare 'ok' after arrival instructions vs after a slot offer."""
import json, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from drive import new_chat, observe, state, call

print("--- closing case: 'ok' after instructions (expect small talk, no booking) ---")
for trial in range(3):
    c = new_chat()
    observe(c, "What should I do before my first visit?")
    r = observe(c, "ok")
    print(f" trial {trial+1}: intents={r['intents']} source={r['answer_source']}")

print("--- confirmation case: 'ok' after a slot offer (expect a booking) ---")
for trial in range(3):
    c = new_chat()
    observe(c, "I'd like to book an appointment with any doctor next Monday morning")
    r = observe(c, "ok")
    print(f" trial {trial+1}: intents={r['intents']} source={r['answer_source']} reply={r['reply'][:70]}")
