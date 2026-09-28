"""The staff connector: the Claude app paired with a session, asking two counts.

A staff-scoped surface, separate from the agent's in-process tool registry: nothing here
is reachable by a patient's turn, and nothing in `chat.agent` imports it.
"""
