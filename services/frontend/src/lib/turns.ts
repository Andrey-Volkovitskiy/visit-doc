import type { BookingAct, Message, RequestOutcome } from "./chatStream";

/**
 * Everything one turn is owed a marker for, gathered onto the message that carries it.
 *
 * The three fields come from two different rows and describe one event: the mark from
 * the patient's message, because that is the thing a person has to act on; the outcomes
 * from the reply, because they describe what the assistant did; the acts from the
 * patient's message again, because a turn that booked and then failed before replying
 * still has to say so.
 */
export interface TurnEvidence {
  /** Whether anything in this turn called a person. Its *reason* stays on the message
   * that carries it, rendered there in words — this is the marker's state only. */
  marked: boolean;
  requestOutcomes: RequestOutcome[] | null;
  bookingActs: BookingAct[] | null;
}

/**
 * Which message carries the evidence marker for each turn, and what it holds.
 *
 * One marker per turn, not one per message. A corpus gap writes a mark on the question
 * and an abstention on the answer; rendered as two markers they read as two problems
 * needing two people, when there was one. So a turn's evidence is gathered onto one
 * message — the reply, when the turn has one, because it is the turn's last word and
 * exactly one row per turn — and the messages it absorbed carry no marker at all.
 *
 * **The grouping is the server's, never this module's guess.** `reply_to_message_ids`
 * is written with the reply and names every patient message it answers, in order. That
 * is what makes this safe where pairing by adjacency was not: a burst of three
 * questions merged into one turn is one reply naming three ids, a patient message still
 * waiting is named by nothing, and a superseded turn's reply never existed to name
 * anything. Each of those is read correctly here without a rule about row order.
 *
 * A message nothing names is its own anchor, carrying its own data — which is what
 * keeps a turn with no reply visible. That covers the ones that matter most: a turn
 * that failed before replying, one a staff member took over, one a newer message
 * cancelled. It also covers a reply written before this field existed, whose ids are
 * null: it anchors itself and shows its own outcomes, so no stored turn loses its
 * marker to a migration nobody ran.
 *
 * Pure, and its own module rather than a helper inside the thread, because it is the
 * whole of the rule and a reader should be able to see it without reading any markup.
 */
export function evidenceByAnchor(thread: Message[]): Map<string, TurnEvidence> {
  const byId = new Map(thread.map((message) => [message.id, message]));
  const absorbed = new Set<string>();
  const evidence = new Map<string, TurnEvidence>();

  for (const message of thread) {
    const answers = message.reply_to_message_ids ?? [];
    if (answers.length === 0) continue;
    // Only ids this thread actually holds, and only ones no earlier reply already took.
    // Two replies naming one message is not something the server writes; if it ever
    // did, first-wins keeps that message's acts from being rendered under both.
    const questions = answers
      .filter((id) => byId.has(id) && !absorbed.has(id))
      .map((id) => byId.get(id)!);
    for (const question of questions) absorbed.add(question.id);
    const acts = questions.flatMap((question) => question.booking_acts ?? []);
    evidence.set(message.id, {
      marked: questions.some((question) => question.attention_mark !== null),
      requestOutcomes: message.request_outcomes,
      // Null and [] are not the same thing anywhere else in this app and are not here:
      // null is "no act was attempted", which renders nothing at all.
      bookingActs: acts.length === 0 ? null : acts,
    });
  }

  for (const message of thread) {
    if (absorbed.has(message.id) || evidence.has(message.id)) continue;
    evidence.set(message.id, {
      marked: message.attention_mark !== null,
      requestOutcomes: message.request_outcomes,
      bookingActs: message.booking_acts,
    });
  }

  return evidence;
}

/** What a message with no evidence of its own renders: a marker, and nothing behind it. */
export const NO_EVIDENCE: TurnEvidence = {
  marked: false,
  requestOutcomes: null,
  bookingActs: null,
};
