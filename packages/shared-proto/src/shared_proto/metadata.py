"""gRPC metadata keys that are part of the chat<->scheduler contract.

Declared here, beside the generated stubs, because a metadata key is as much a
two-sided agreement as a message field: the sender and the receiver must spell it
identically or it simply stops arriving. Neither side is told - `x-turn-id` going
unrecognized produces no error, no failed RPC, and no failing test, only log lines
that quietly stop joining up.
"""

# The caller's per-turn correlation id, bound on the receiving side for the handler's
# lifetime so both services' lines for one patient turn share a key.
TURN_ID_METADATA_KEY = "x-turn-id"
