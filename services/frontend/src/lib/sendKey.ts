import type { KeyboardEvent } from "react";

/**
 * Whether a keystroke in a message box means "send this now".
 *
 * Enter sends, and so does Ctrl/Cmd+Enter - the habit people arrive with from other
 * chat clients, where one or the other is the send key. Shift+Enter deliberately does
 * not: it falls through to the textarea's own handling and inserts a newline, which is
 * the only way to type a multi-line message here.
 *
 * A keystroke closing an IME candidate window never sends. The browser reports that
 * Enter as `isComposing`, and it means "accept this candidate" - treating it as a send
 * posts a half-typed sentence for anyone composing in Japanese, Chinese or Korean.
 *
 * Shared by both composers so the two cannot drift into different send keys.
 */
export function isSendKey(event: KeyboardEvent<HTMLTextAreaElement>): boolean {
  if (event.key !== "Enter") return false;
  if (event.nativeEvent.isComposing) return false;
  if (event.ctrlKey || event.metaKey) return true;
  return !event.shiftKey;
}
