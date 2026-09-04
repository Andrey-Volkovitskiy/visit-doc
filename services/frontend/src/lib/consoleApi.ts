import {
  detailOf,
  fetchChatHistory,
  type AttentionMark,
  type Message,
} from "./chatStream";

/**
 * The console's network layer.
 *
 * Separate from `chatStream.ts` because it is a different surface — the staff side of a
 * conversation, not the patient's — but held to the same rule: no component calls
 * `fetch` directly, so the wire contract lives in one reviewable place per surface.
 */

/**
 * One conversation as the staff side sees it.
 *
 * `emphasized`, `assistant_may_reply` and `pause_seconds_remaining` are derived
 * server-side from the state that decides them, so the switch shown here and the gate a
 * turn obeys are one answer. `pause_seconds_remaining` counts down server-side, which is
 * what makes two open tabs agree.
 */
export interface ConsoleConversation {
  chat_id: string;
  /** Null while this chat's patient record does not exist yet. */
  patient_name: string | null;
  last_message_at: string | null;
  emphasized: boolean;
  escalated: boolean;
  escalation_reason: string | null;
  attention_since: string | null;
  assistant_may_reply: boolean;
  pause_seconds_remaining: number | null;
}

/**
 * The one polled answer, serving both panes.
 *
 * `attention_total` counts conversations needing a person, once each however many marks
 * sit inside one. Today the server computes it as `emphasized` summed over the very
 * rows it returns, so a client counting them would get the same number — this field is
 * not carrying a rule the client could not apply. What it carries is *whose* rule it
 * is: the collapse from a conversation's marks into `emphasized`, and from those into a
 * total, belongs to the side that owns the marks, and a count derived here would be a
 * second place to change when the total stops being one row per emphasis — a listing
 * that pages, or a total taken over conversations this page does not hold.
 */
export interface ConsoleListing {
  attention_total: number;
  conversations: ConsoleConversation[];
}

/**
 * GET /console/conversations: every conversation in the session, in display order.
 *
 * `signal` carries the poll's deadline. This is the one read here that a timer issues
 * again every couple of seconds, so it is the one where a request that never settles
 * accumulates rather than merely disappointing whoever clicked.
 */
export async function fetchConsoleListing(
  signal?: AbortSignal,
): Promise<ConsoleListing> {
  const response = await fetch("/console/conversations", { signal });
  // Checked before parsing: an error body is JSON too, and casting it here would hand
  // the poll an object with no `conversations` array to render.
  if (!response.ok) {
    throw new Error("Could not load the conversations.");
  }
  return (await response.json()) as ConsoleListing;
}

/**
 * One conversation's whole thread — patient, assistant and staff alike.
 *
 * `signal` carries the caller's deadline and its aborts straight through; see
 * `fetchChatHistory`.
 */
export async function fetchThread(
  chatId: string,
  signal?: AbortSignal,
): Promise<Message[]> {
  return await fetchChatHistory(chatId, signal);
}

/**
 * Turn a failed staff post into the message the staff member sees.
 *
 * A 404 is the only one that says anything specific: the conversation is gone, or
 * belongs to a session this browser is not in, and the two are reported identically.
 */
function postErrorMessage(status: number): string {
  switch (status) {
    case 404:
      return "This conversation no longer exists. Reload to see the current list.";
    case 422:
      return "Enter a reply of 1 to 2000 characters.";
    default:
      return "Could not send that reply. Please try again.";
  }
}

/** POST /console/chats/{id}/messages: reply as staff, in the patient's own thread. */
export async function postStaffMessage(
  chatId: string,
  content: string,
): Promise<Message> {
  const response = await fetch(`/console/chats/${chatId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  // Checked before parsing, for the same reason: casting an error body to `Message`
  // would put an object with no `content` and no `sender` straight into the thread.
  if (!response.ok) {
    throw new Error(postErrorMessage(response.status));
  }
  return (await response.json()) as Message;
}

/** What each kind of mark says, for a staff member reading one on a message. */
export const ATTENTION_MARK_LABEL: Record<AttentionMark, string> = {
  patient_asked_for_person: "Asked for a person",
  corpus_could_not_answer: "No answer in the clinic's documents",
  assistant_failed: "The assistant could not complete this",
  unanswered: "Arrived while the assistant was silent",
};

/** What the assistant may do in one conversation, straight after a change to it. */
export interface AssistantState {
  assistant_may_reply: boolean;
  pause_seconds_remaining: number | null;
}

/** POST /console/chats/{id}/assistant: turn the assistant on or off in one chat. */
export async function setAssistant(
  chatId: string,
  enabled: boolean,
): Promise<AssistantState> {
  const response = await fetch(`/console/chats/${chatId}/assistant`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!response.ok) {
    throw new Error("Could not change the assistant for this conversation.");
  }
  return (await response.json()) as AssistantState;
}


// --- practitioners, proxied through this app's own backend --------------------------
//
// The scheduler owns practitioners, and the browser cannot address it: the session that
// authorizes a change lives in an `HttpOnly` cookie the page cannot read, and that
// surface wants it as a header. So every call here goes to this app's own origin, which
// carries it across.

/**
 * One working range, in the shape the scheduler both accepts and returns.
 *
 * `weekday` is Monday-based and numeric (0-6), matching the scheduler's own enum and
 * Python's `date.weekday()`. The wire carries the number; the day's name is a label
 * this app renders, not a value it sends.
 */
export interface WorkingRange {
  weekday: number;
  start_time: string;
  end_time: string;
}

export interface Practitioner {
  id: string;
  full_name: string;
  specialty: string;
  appointment_duration_minutes: number;
  schedule: WorkingRange[];
}

/** The fields a create or an edit may carry. Every one of them is optional. */
export interface PractitionerWrite {
  full_name?: string;
  specialty?: string;
  appointment_duration_minutes?: number;
  schedule?: WorkingRange[];
}

/**
 * Read the refusal's own wording off the response.
 *
 * Every rule here belongs to the service that owns practitioners, so its explanation is
 * relayed rather than restated: a duplicate name and overlapping ranges are its
 * judgements, and inventing a second wording for either would make the screen disagree
 * with the system it is editing.
 */
async function practitionerError(response: Response): Promise<Error> {
  return new Error(
    await detailOf(response, "Could not save that. Please try again."),
  );
}

/** GET /console/practitioners: the clinic's roster, as the scheduler renders it. */
export async function fetchPractitioners(): Promise<Practitioner[]> {
  const response = await fetch("/console/practitioners");
  if (!response.ok) throw await practitionerError(response);
  return (await response.json()) as Practitioner[];
}

/** POST /console/practitioners: create one. An empty body defaults every field. */
export async function createPractitioner(
  body: PractitionerWrite,
): Promise<Practitioner> {
  const response = await fetch("/console/practitioners", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw await practitionerError(response);
  return (await response.json()) as Practitioner;
}

/** PATCH /console/practitioners/{id}: edit one; omitted fields are left alone. */
export async function updatePractitioner(
  practitionerId: string,
  body: PractitionerWrite,
): Promise<Practitioner> {
  const response = await fetch(`/console/practitioners/${practitionerId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw await practitionerError(response);
  return (await response.json()) as Practitioner;
}

/** DELETE /console/practitioners/{id}: remove one, and their appointments with them. */
export async function deletePractitioner(practitionerId: string): Promise<void> {
  const response = await fetch(`/console/practitioners/${practitionerId}`, {
    method: "DELETE",
  });
  if (!response.ok) throw await practitionerError(response);
}


// --- the corpus the assistant answers from ------------------------------------------
//
// There is no retrievability field here, and none may be added: an entry owns a live
// revision or it cannot be stored, so every entry a listing returns is one the
// assistant can answer from. A state that can never be false is not a state.

export interface FaqEntry {
  id: number;
  content: string;
  created_at: string;
  updated_at: string;
}

async function faqError(response: Response): Promise<Error> {
  return new Error(
    await detailOf(response, "Could not save that entry. Please try again."),
  );
}

/** GET /faq: this session's corpus, oldest first. */
export async function fetchFaqEntries(): Promise<FaqEntry[]> {
  const response = await fetch("/faq");
  if (!response.ok) throw await faqError(response);
  return (await response.json()) as FaqEntry[];
}

/** POST /faq: add an entry, publishing a revision of its chunks with it. */
export async function createFaqEntry(content: string): Promise<FaqEntry> {
  const response = await fetch("/faq", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!response.ok) throw await faqError(response);
  return (await response.json()) as FaqEntry;
}

/** PUT /faq/{id}: replace an entry's text, publishing a new revision. */
export async function updateFaqEntry(
  entryId: number,
  content: string,
): Promise<FaqEntry> {
  const response = await fetch(`/faq/${entryId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!response.ok) throw await faqError(response);
  return (await response.json()) as FaqEntry;
}

/** DELETE /faq/{id}: remove an entry, making it unanswerable at that instant. */
export async function deleteFaqEntry(entryId: number): Promise<void> {
  const response = await fetch(`/faq/${entryId}`, { method: "DELETE" });
  if (!response.ok) throw await faqError(response);
}
