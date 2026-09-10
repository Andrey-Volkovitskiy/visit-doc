export interface Citation {
  entry_id: number;
  chunk_index: number;
  chunk_text: string;
}

/**
 * Which step produced the reply.
 *
 * `hand_off` is the one that produced no answer: a person now has this, so the turn
 * said so in fixed text and nothing was retrieved, booked or generated. *Why* a person
 * was fetched is the message's attention mark, not this field.
 *
 * `small_talk` is a reply to a message that asked for nothing - generated, but with
 * nothing retrieved behind it, so it carries no verdict and no citations.
 */
export type AnswerSource =
  | "faq"
  | "booking"
  | "small_talk"
  | "merged"
  | "hand_off";

export interface ChatTokenEvent {
  type: "token";
  text: string;
}

/**
 * What the FAQ half of a turn did, and for an abstention, where it stopped.
 *
 * The four abstentions are identical to the patient - same message, same handoff -
 * and differ only in the record, which is what says whether the corpus is empty, the
 * index is behind it, the similarity floor is too high, or the rerank floor is.
 */
export type FaqVerdict =
  | "answered"
  | "answered_unreranked"
  | "abstained_empty_corpus"
  | "abstained_empty_pool"
  | "abstained_similarity_floor"
  | "abstained_rerank_floor";

/**
 * What one request of a message got: its answer, its verdict, its evidence.
 *
 * The only place a verdict lives. A message may carry several requests, each retrieved
 * for and gated on its own, so a turn that answered one and abstained on another has no
 * single verdict to report - and nothing derives one.
 *
 * `question` is the request as the classifier restated it: what was retrieved for, not
 * the patient's own wording. `answer` is null exactly when the verdict is an abstention,
 * and `citations` is empty exactly then.
 */
export interface RequestOutcome {
  position: number;
  question: string;
  answer: string | null;
  verdict: FaqVerdict;
  citations: Citation[];
}

export interface ChatDoneEvent {
  type: "done";
  /**
   * One entry per request the FAQ half answered or abstained on, in position order.
   *
   * Null when no FAQ specialist ran, i.e. a booking-only reply, a hand-off or a
   * small-talk reply. Never `[]` - a half that ran had at least one request.
   */
  request_outcomes: RequestOutcome[] | null;
  /**
   * A reply to render *instead of* the accumulated tokens - today, a turn whose
   * every request abstained, and a turn routed as several parts that collapsed to one.
   *
   * Absent, null and empty all say the same thing: there is no such reply, so the
   * tokens are the answer. The server settles it the same way when it stores the
   * reply (`done_event.message or answer`), and the two sides must not disagree: a
   * bubble rendered from text the server did not store is one no later history read
   * can account for.
   */
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
  | "urgent_condition"
  | "distress"
  | "patient_asked_for_person"
  | "booking_for_another_person"
  | "not_authorized"
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
  /** Only ever set on an assistant message whose turn ran the FAQ half. */
  request_outcomes: RequestOutcome[] | null;
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

/**
 * Turn a failed deletion into the message the user sees.
 *
 * The 502/503/504 split is a contract about what each status *proves*, not three shades
 * of one apology: a 502 is scheduling having answered, with a rejection, so nothing was
 * written and the identical request is rejected identically — which is why it is the one
 * branch that does not offer a retry. A 503 is scheduling never having been reached, so
 * nothing was written and a retry is safe. A 504 leaves the outcome genuinely unknown, so
 * it states none, and offers the retry that deleting an already-absent patient makes safe.
 *
 * The 404 says the one thing this route answers it for — this chat is not reachable from
 * this session, whether it was deleted or was never ours.
 *
 * Takes the bare status, not a `Response`: none of these messages relay the server's own
 * `detail`, which is developer prose, so reading a body would only make this async for
 * nothing.
 */
function deleteErrorMessage(status: number): string {
  switch (status) {
    case 404:
      return "This chat no longer exists. Reload to see your chats.";
    case 502:
      return (
        "Scheduling refused this deletion, so nothing was deleted. " +
        "Sending it again will not help; please report this."
      );
    case 503:
      return "Scheduling is unavailable, so nothing was deleted. Try again shortly.";
    case 504:
      return (
        "Scheduling did not confirm this deletion, so this chat may not have been " +
        "deleted. Try again."
      );
    default:
      return "Could not delete this chat. Please try again.";
  }
}

/** DELETE /chats/{id}: remove the chat, its messages, its patient, and its bookings. */
export async function deleteChat(chatId: string): Promise<void> {
  const response = await fetch(`/chats/${chatId}`, { method: "DELETE" });
  // Not `ensureOk`: only some of these failures know whether anything was deleted,
  // and only some are worth retrying.
  if (!response.ok) {
    throw new Error(deleteErrorMessage(response.status));
  }
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

/**
 * GET /chats/{id}/messages: one chat's history, chronological.
 *
 * `signal` is how a caller gives the read a deadline and takes it back when the chat
 * it belongs to is closed. Without one a wedged socket holds a request that neither
 * resolves nor rejects, which is not a slow read but a permanent one — see
 * `useThreadReads`'s `READ_TIMEOUT_MS` for what that costs a page.
 */
export async function fetchChatHistory(
  chatId: string,
  signal?: AbortSignal,
): Promise<Message[]> {
  const response = await fetch(`/chats/${chatId}/messages`, { signal });
  ensureOk(response, "Could not load this chat's history.");
  const data = (await response.json()) as { messages: Message[] };
  return data.messages;
}
