export interface Citation {
  entry_id: number;
  chunk_index: number;
  chunk_text: string;
}

/**
 * Which step produced the reply.
 *
 * `hand_off` is the one that produced no answer: the visitor asked for a person, so the
 * turn fetched one and said so, and nothing was retrieved or booked.
 */
export type AnswerSource = "faq" | "booking" | "merged" | "hand_off";

export interface ChatTokenEvent {
  type: "token";
  text: string;
}

export interface ChatDoneEvent {
  type: "done";
  /** Null when no FAQ specialist ran, i.e. a booking-only reply. */
  grounded: boolean | null;
  citations: Citation[];
  message?: string | null;
  answer_source: AnswerSource;
}

export interface ChatCancelledEvent {
  type: "cancelled";
}

/**
 * The assistant may not speak in this conversation, so nothing was generated.
 *
 * A third terminal value because the other two already mean something else:
 * `cancelled` says to discard a message that is in fact being kept, and an empty `done`
 * announces a reply that does not exist. The client renders nothing for it — the
 * patient's message simply stays in the thread.
 */
export interface ChatSilentEvent {
  type: "silent";
}

export type ChatEvent =
  | ChatTokenEvent
  | ChatDoneEvent
  | ChatCancelledEvent
  | ChatSilentEvent;

/** Why one patient message needs a person. Never set on any other sender's message. */
export type AttentionMark =
  | "patient_asked_for_person"
  | "corpus_could_not_answer"
  | "assistant_failed"
  | "unanswered";

/**
 * One message in a chat's history.
 *
 * There is deliberately no `staff_name`: `sender` already carries everything a label
 * states, and this system has no person behind a staff reply to name.
 */
export interface Message {
  id: string;
  sender: "patient" | "assistant" | "staff";
  content: string;
  grounded: boolean | null;
  citations: Citation[] | null;
  attention_mark: AttentionMark | null;
  created_at: string;
}

export interface ChatSummary {
  id: string;
  /** Null while this chat's patient record does not exist yet. */
  patient_name: string | null;
  created_at: string;
  last_message_at: string | null;
}

/** Parse a POST /chat NDJSON response into its stream of events. */
export async function* parseNdjsonStream(response: Response): AsyncGenerator<ChatEvent> {
  if (!response.body) {
    throw new Error("Response has no body");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let newlineIndex = buffer.indexOf("\n");
    while (newlineIndex !== -1) {
      const line = buffer.slice(0, newlineIndex);
      buffer = buffer.slice(newlineIndex + 1);
      if (line.trim() !== "") {
        yield JSON.parse(line) as ChatEvent;
      }
      newlineIndex = buffer.indexOf("\n");
    }
  }

  if (buffer.trim() !== "") {
    yield JSON.parse(buffer) as ChatEvent;
  }
}

/**
 * The browser's own local wall-clock time, as an offset-free ISO-8601 string.
 *
 * Sent on every turn and used for every "past"/"upcoming"/booking-horizon judgement
 * in the system. `toISOString()` is deliberately not used: it converts to UTC, which
 * would move the wall-clock time the assistant reasons about.
 */
export function localNow(now: Date = new Date()): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return (
    `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}` +
    `T${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`
  );
}

export interface ChatListing {
  /** Already in display order — the most recently active chat is first. */
  chats: ChatSummary[];
  /**
   * Whether the server recognized a session for this request.
   *
   * False means a genuine first arrival. It is the only thing that tells an empty
   * `chats` list apart from a session the user emptied, and the two need opposite
   * handling — a first arrival is given a chat, an emptied session is left alone. The
   * SPA cannot tell on its own: the session cookie is `HttpOnly`.
   */
  session_exists: boolean;
}

/**
 * Throw unless the response succeeded.
 *
 * Every wrapper below casts the parsed body straight to its success type, which for an
 * error response would hand the caller an object with none of the fields it declares —
 * an `undefined` id that slips past a `!== null` guard, or an `undefined` array that
 * throws inside `.map` during render. Failing here keeps that shape from ever existing.
 */
function ensureOk(response: Response, fallbackMessage: string): void {
  if (!response.ok) {
    throw new Error(fallbackMessage);
  }
}

/** GET /chats: the session's chats, and whether a session was recognized. */
export async function fetchChats(): Promise<ChatListing> {
  const response = await fetch("/chats");
  ensureOk(response, "Could not load your chats. Please try again.");
  return (await response.json()) as ChatListing;
}

/** POST /chats: create a chat (and, on a first visit, the session itself). */
export async function createChat(): Promise<ChatSummary> {
  const response = await fetch("/chats", { method: "POST" });
  ensureOk(response, "Could not start a chat. Please try again.");
  return (await response.json()) as ChatSummary;
}

/** DELETE /chats/{id}: remove the chat, its messages, its patient, and its bookings. */
export async function deleteChat(chatId: string): Promise<void> {
  const response = await fetch(`/chats/${chatId}`, { method: "DELETE" });
  if (!response.ok) {
    throw new Error("Could not delete this chat. Please try again.");
  }
}

/** What a rename changed: the chat, and the name now stored for its patient. */
export interface ChatPatient {
  chat_id: string;
  patient_name: string;
}

/**
 * Read the server's own explanation off an error response.
 *
 * Falls back to `fallback` when there is no readable `detail` — an empty body, a
 * non-JSON one, or a proxy's own error page.
 */
export async function detailOf(response: Response, fallback: string): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : fallback;
  } catch {
    return fallback;
  }
}

/**
 * Turn a failed rename into the message the user sees.
 *
 * A 504 deliberately claims nothing about the outcome: the server stopped waiting for
 * scheduling, which does not prove the rename was not applied. Renaming is idempotent,
 * so the honest instruction is to try again rather than to state what happened.
 */
async function renameErrorMessage(response: Response): Promise<string> {
  switch (response.status) {
    case 409:
      return await detailOf(response, "That name cannot be used for this chat.");
    case 404:
      return "This chat no longer exists. Reload to see your chats.";
    case 422:
      return "Enter a name of 1 to 200 characters.";
    case 503:
      return "Scheduling is unavailable, so nothing was renamed. Try again shortly.";
    case 504:
      return "Scheduling did not answer, so the new name may not have been saved. Try again.";
    default:
      return "Could not rename this chat. Please try again.";
  }
}

/** PATCH /chats/{id}/patient: rename this chat's patient. */
export async function renameChatPatient(
  chatId: string,
  fullName: string,
): Promise<ChatPatient> {
  const response = await fetch(`/chats/${chatId}/patient`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ full_name: fullName }),
  });
  // Not `ensureOk`: which failure this was is exactly what the user needs to know,
  // since only some of them say whether the name was saved.
  if (!response.ok) {
    throw new Error(await renameErrorMessage(response));
  }
  return (await response.json()) as ChatPatient;
}

/** POST `message` to /chat for one chat, and return its parsed NDJSON event stream. */
export async function askChat(
  chatId: string,
  message: string,
  signal?: AbortSignal,
): Promise<AsyncGenerator<ChatEvent>> {
  const response = await fetch("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, message, local_now: localNow() }),
    signal,
  });
  // Checked before parsing: an error body is JSON too, and parseNdjsonStream would
  // yield it as a single event carrying no `type` — which the caller's terminal-event
  // branch would then treat as a completed turn with an empty reply.
  ensureOk(response, "Something went wrong. Please try again.");
  return parseNdjsonStream(response);
}

/** GET /chats/{id}/messages: one chat's history, chronological. */
export async function fetchChatHistory(chatId: string): Promise<Message[]> {
  const response = await fetch(`/chats/${chatId}/messages`);
  ensureOk(response, "Could not load this chat's history.");
  const data = (await response.json()) as { messages: Message[] };
  return data.messages;
}
