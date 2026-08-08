"""Message rows -> alternating Claude `messages` history (research.md #5)."""

from typing import Literal, cast

from anthropic.types import MessageParam

from chat.domain.models import Message, MessageSender

_Role = Literal["user", "assistant"]


def build_history_messages(
    history: list[Message], current_message: str, current_message_id: str
) -> tuple[list[MessageParam], list[str]]:
    """Build the alternating `user`/`assistant` list for a Claude `messages` call.

    `history` is every prior `Message` row for the chat, oldest first, queried before
    the current message was inserted (data-model.md); `current_message`/
    `current_message_id` are the just-validated patient message and its already-
    minted id, appended as the final entry - not re-read from the database.
    Consecutive same-role entries (a burst of patient messages, FR-014, or a patient
    message that got no reply, research.md #3) are merged into one, joined by a blank
    line, to satisfy the Messages API's strict alternation requirement. The returned
    list's last entry - always `user`, since `current_message` is appended last - is
    also reused as `search_faq`'s retrieval query (research.md #6).

    Returns: the alternating `user`/`assistant` message list to send to Claude, and
        the ordered ids of every patient message that ended up merged into that final
        `user` entry - the trailing contiguous patient-message run in `history`, plus
        `current_message_id` - so the caller can record, on the assistant reply this
        turn produces, every patient message it actually answers (not just the
        current one), even when a burst was merged into it.
    """
    entries: list[MessageParam] = []
    for message in history:
        role: _Role = (
            "assistant" if message.sender == MessageSender.ASSISTANT else "user"
        )
        _append_or_merge(entries, role, message.content)
    _append_or_merge(entries, "user", current_message)

    trailing_ids: list[str] = []
    for message in reversed(history):
        if message.sender != MessageSender.PATIENT:
            break
        trailing_ids.append(message.id)
    trailing_ids.reverse()
    trailing_ids.append(current_message_id)

    return entries, trailing_ids


def _append_or_merge(entries: list[MessageParam], role: _Role, content: str) -> None:
    """Append `content` as a new entry, or merge it into the last one if same-role."""
    if entries and entries[-1]["role"] == role:
        merged = f"{entries[-1]['content']}\n\n{content}"
        entries[-1] = cast(MessageParam, {"role": role, "content": merged})
        return
    entries.append(cast(MessageParam, {"role": role, "content": content}))
