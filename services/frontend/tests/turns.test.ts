import { describe, expect, it } from "vitest";
import type { BookingAct, Message } from "../src/lib/chatStream";
import { evidenceByAnchor } from "../src/lib/turns";

function message(overrides: Partial<Message> = {}): Message {
  return {
    id: "01M",
    sender: "patient",
    content: "is anyone there?",
    request_outcomes: null,
    attention_mark: null,
    booking_acts: null,
    reply_to_message_ids: null,
    created_at: "2026-09-01T12:00:00",
    ...overrides,
  };
}

const UNANSWERED = {
  position: 0,
  question: "What is your late cancellation fee?",
  answer: null,
  verdict: "abstained_rerank_floor" as const,
  citations: [],
};

function act(overrides: Partial<BookingAct> = {}): BookingAct {
  return {
    operation: "book",
    outcome: "done",
    refusal_reason: null,
    practitioner_full_name: "Andreas Vesalius",
    starts_at: "2027-01-12T10:00:00",
    ends_at: "2027-01-12T10:30:00",
    previous_practitioner_full_name: null,
    previous_starts_at: null,
    ...overrides,
  };
}

describe("evidenceByAnchor: one marker per turn", () => {
  it("gathers a corpus gap's two halves onto the reply, leaving the question none", () => {
    // The defect this exists to remove: the mark on the question and the abstention on
    // the answer are one event, and two markers read as two problems.
    const question = message({
      id: "q",
      attention_mark: "corpus_could_not_answer",
    });
    const reply = message({
      id: "r",
      sender: "assistant",
      request_outcomes: [UNANSWERED],
      reply_to_message_ids: ["q"],
    });

    const evidence = evidenceByAnchor([question, reply]);

    expect(evidence.has("q")).toBe(false);
    expect(evidence.get("r")).toEqual({
      marked: true,
      requestOutcomes: [UNANSWERED],
      bookingActs: null,
    });
  });

  it("carries the acts stored on the question over to the reply that answered it", () => {
    // Acts are written to the patient message and outcomes to the reply. One marker
    // means one of them travels, and nothing may be dropped on the way.
    const booked = act();
    const question = message({ id: "q", booking_acts: [booked] });
    const reply = message({
      id: "r",
      sender: "assistant",
      reply_to_message_ids: ["q"],
    });

    const evidence = evidenceByAnchor([question, reply]);

    expect(evidence.get("r")?.bookingActs).toEqual([booked]);
    expect(evidence.has("q")).toBe(false);
  });

  it("folds a whole burst into the one reply that answered it", () => {
    // Three questions, one turn, one reply naming all three — the case that cannot be
    // read off row order, and the reason the server records the relation at all.
    const first = message({ id: "q1", attention_mark: null });
    const second = message({ id: "q2", attention_mark: "urgent_condition" });
    const third = message({ id: "q3", booking_acts: [act()] });
    const reply = message({
      id: "r",
      sender: "assistant",
      reply_to_message_ids: ["q1", "q2", "q3"],
    });

    const evidence = evidenceByAnchor([first, second, third, reply]);

    expect([...evidence.keys()]).toEqual(["r"]);
    expect(evidence.get("r")?.marked).toBe(true);
    expect(evidence.get("r")?.bookingActs).toHaveLength(1);
  });

  it("leaves a question no reply answers carrying its own evidence", () => {
    // A turn that failed before replying, one a staff member took over, one a newer
    // message cancelled. Absorbing it into a neighbour would take the only marker the
    // turns that most need one ever get.
    const question = message({
      id: "q",
      attention_mark: "assistant_failed",
      booking_acts: [act({ outcome: null })],
    });

    const evidence = evidenceByAnchor([question]);

    expect(evidence.get("q")).toEqual({
      marked: true,
      requestOutcomes: null,
      bookingActs: [act({ outcome: null })],
    });
  });

  it("anchors a reply that names nothing on itself", () => {
    // A row written before the server recorded the relation. It shows its own outcomes
    // rather than losing its marker to a migration nobody ran.
    const question = message({ id: "q", attention_mark: "corpus_could_not_answer" });
    const reply = message({
      id: "r",
      sender: "assistant",
      request_outcomes: [UNANSWERED],
      reply_to_message_ids: null,
    });

    const evidence = evidenceByAnchor([question, reply]);

    expect(evidence.get("r")?.requestOutcomes).toEqual([UNANSWERED]);
    expect(evidence.get("q")?.marked).toBe(true);
  });

  it("ignores an id the thread does not hold", () => {
    const reply = message({
      id: "r",
      sender: "assistant",
      request_outcomes: [UNANSWERED],
      reply_to_message_ids: ["a message that is not here"],
    });

    const evidence = evidenceByAnchor([reply]);

    expect(evidence.get("r")?.marked).toBe(false);
    expect(evidence.get("r")?.requestOutcomes).toEqual([UNANSWERED]);
  });

  it("gives a message claimed by two replies to the first of them", () => {
    // Not something the server writes. If it ever did, the question's acts would
    // otherwise be rendered under both markers, reporting one booking twice.
    const question = message({ id: "q", booking_acts: [act()] });
    const first = message({
      id: "r1",
      sender: "assistant",
      reply_to_message_ids: ["q"],
    });
    const second = message({
      id: "r2",
      sender: "assistant",
      reply_to_message_ids: ["q"],
    });

    const evidence = evidenceByAnchor([question, first, second]);

    expect(evidence.get("r1")?.bookingActs).toHaveLength(1);
    expect(evidence.get("r2")?.bookingActs).toBeNull();
  });

  it("leaves a staff message with nothing to show", () => {
    const staff = message({ id: "s", sender: "staff", content: "On it." });

    expect(evidenceByAnchor([staff]).get("s")).toEqual({
      marked: false,
      requestOutcomes: null,
      bookingActs: null,
    });
  });

  it("keeps every message addressable, so nothing renders unkeyed", () => {
    const question = message({ id: "q" });
    const reply = message({
      id: "r",
      sender: "assistant",
      reply_to_message_ids: ["q"],
    });
    const staff = message({ id: "s", sender: "staff" });

    const evidence = evidenceByAnchor([question, reply, staff]);

    // The absorbed question is deliberately absent rather than present-and-empty: the
    // thread reads a miss as "this message anchors no turn".
    expect(evidence.has("q")).toBe(false);
    expect(evidence.has("r")).toBe(true);
    expect(evidence.has("s")).toBe(true);
  });
});
