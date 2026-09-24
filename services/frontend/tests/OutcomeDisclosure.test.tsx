import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OutcomeDisclosure } from "../src/components/OutcomeDisclosure";
import type {
  AttentionMark,
  BookingAct,
  RequestOutcome,
} from "../src/lib/chatStream";

const CITED = [
  { entry_id: 7, chunk_index: 0, chunk_text: "Bring your referral letter." },
];

function answered(
  position: number,
  question: string,
  verdict: RequestOutcome["verdict"] = "answered",
): RequestOutcome {
  return {
    position,
    question,
    answer: "Yes — bring your referral letter.",
    verdict,
    citations: CITED,
  };
}

function abstained(
  position: number,
  question: string,
  verdict: RequestOutcome["verdict"] = "abstained_empty_pool",
): RequestOutcome {
  return { position, question, answer: null, verdict, citations: [] };
}

function renderDisclosure(
  outcomes: RequestOutcome[] | null,
  mark: AttentionMark | null = null,
  acts: BookingAct[] | null = null,
) {
  return render(
    <OutcomeDisclosure requestOutcomes={outcomes} mark={mark} bookingActs={acts} />,
  );
}

/** One act, as the wire carries it: a booking made with a named practitioner. */
function act(overrides: Partial<BookingAct> = {}): BookingAct {
  return {
    operation: "book",
    outcome: "done",
    refusal_reason: null,
    practitioner_full_name: "Andreas Vesalius",
    starts_at: "2027-01-12T10:00:00",
    ends_at: "2027-01-12T11:00:00",
    previous_practitioner_full_name: null,
    previous_starts_at: null,
    ...overrides,
  };
}

/** A reschedule from 09:00 to 10:00 on the same day, with the same practitioner. */
function moved(overrides: Partial<BookingAct> = {}): BookingAct {
  return act({
    operation: "reschedule",
    previous_practitioner_full_name: "Andreas Vesalius",
    previous_starts_at: "2027-01-12T09:00:00",
    ...overrides,
  });
}

function marker(): HTMLElement {
  return screen.getByTestId("outcome-marker");
}

describe("OutcomeDisclosure: opening and closing (FR-027, FR-028)", () => {
  it("starts closed, showing the marker and nothing else", () => {
    renderDisclosure([answered(0, "do I need a referral?")]);

    expect(marker()).toBeInTheDocument();
    expect(marker()).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("request-outcome")).toBeNull();
  });

  it("expands on activation and reports that it is expanded", () => {
    renderDisclosure([answered(0, "do I need a referral?")]);

    fireEvent.click(marker());

    expect(marker()).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("request-outcome")).toBeInTheDocument();
  });

  it("collapses on a second activation", () => {
    renderDisclosure([answered(0, "do I need a referral?")]);

    fireEvent.click(marker());
    fireEvent.click(marker());

    expect(marker()).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("request-outcome")).toBeNull();
  });

  it("opens on activation rather than on hover", () => {
    // FR-027 says activation, not hover: an evidence block that appears when the
    // pointer passes over a message is one a staff member cannot read without keeping
    // the pointer still, and one a keyboard cannot open at all.
    renderDisclosure([answered(0, "do I need a referral?")]);

    fireEvent.mouseOver(marker());
    fireEvent.mouseEnter(marker());

    expect(marker()).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("request-outcome")).toBeNull();
  });

  it("is a real button, so a keyboard reaches it", () => {
    renderDisclosure([answered(0, "do I need a referral?")]);

    expect(marker().tagName).toBe("BUTTON");
    expect(marker()).toHaveAccessibleName();
  });

  it("lets two disclosures be open at once, independently", () => {
    // FR-028. Each instance owns its own boolean, so this falls out rather than being
    // arranged — a `Set` held by the thread would be state in a component with no other
    // interest in it.
    render(
      <>
        <div data-testid="first">
          <OutcomeDisclosure
            requestOutcomes={[answered(0, "first question")]}
            mark={null}
            bookingActs={null}
          />
        </div>
        <div data-testid="second">
          <OutcomeDisclosure
            requestOutcomes={[answered(0, "second question")]}
            mark={null}
            bookingActs={null}
          />
        </div>
      </>,
    );

    const first = within(screen.getByTestId("first"));
    const second = within(screen.getByTestId("second"));

    fireEvent.click(first.getByTestId("outcome-marker"));
    expect(first.getByTestId("outcome-marker")).toHaveAttribute("aria-expanded", "true");
    expect(second.getByTestId("outcome-marker")).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(second.getByTestId("outcome-marker"));
    expect(first.getByTestId("outcome-marker")).toHaveAttribute("aria-expanded", "true");
    expect(second.getByTestId("outcome-marker")).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(first.getByTestId("outcome-marker"));
    expect(first.getByTestId("outcome-marker")).toHaveAttribute("aria-expanded", "false");
    expect(second.getByTestId("outcome-marker")).toHaveAttribute("aria-expanded", "true");
  });
});

describe("OutcomeDisclosure: when a marker exists at all (FR-026, FR-026a)", () => {
  it("marks an assistant reply holding answered outcomes as served", () => {
    renderDisclosure([answered(0, "do I need a referral?")]);

    expect(marker()).toHaveAttribute("data-outcome-state", "served");
  });

  it("marks a patient message holding an attention mark as needing a person", () => {
    // The two conditions fall on different messages, and that is not an inconsistency
    // to smooth over: outcomes describe what the assistant did, a mark is the thing a
    // person has to act on.
    renderDisclosure(null, "patient_asked_for_person");

    expect(marker()).toHaveAttribute("data-outcome-state", "needs-person");
  });

  it("marks an abstention as needing a person", () => {
    renderDisclosure([abstained(0, "what does it cost?")]);

    expect(marker()).toHaveAttribute("data-outcome-state", "needs-person");
  });

  it("marks a mixture of answered and unanswered as needing a person", () => {
    // Something in it is still owed to a person, so the weaker reading would be wrong.
    renderDisclosure([
      answered(0, "do I need a referral?"),
      abstained(1, "what does it cost?"),
    ]);

    expect(marker()).toHaveAttribute("data-outcome-state", "needs-person");
  });

  it("renders no marker for a message holding neither", () => {
    const { container } = renderDisclosure(null, null);

    expect(screen.queryByTestId("outcome-marker")).toBeNull();
    expect(container).toBeEmptyDOMElement();
  });

  it("renders a marker for a message holding both", () => {
    renderDisclosure([answered(0, "do I need a referral?")], "not_authorized");

    expect(marker()).toHaveAttribute("data-outcome-state", "needs-person");
  });

  it("states its state in words as well as in an attribute", () => {
    // FR-006: the attribute is for a test, the accessible name is for a reader. A
    // marker whose state lived only in its colour would satisfy neither.
    renderDisclosure([abstained(0, "what does it cost?")]);
    const needsPerson = marker().getAttribute("aria-label") ?? "";

    expect(needsPerson).toMatch(/\S/);

    screen.getByTestId("outcome-marker").remove();
    renderDisclosure([answered(0, "do I need a referral?")]);
    expect(marker().getAttribute("aria-label")).not.toBe(needsPerson);
  });
});

describe("OutcomeDisclosure: what a served marker is named (016 FR-017)", () => {
  it("names a message holding only acts as a booking record, not an answer", () => {
    // Acts ride on the patient's message, which answered nothing; "what this answer
    // drew on" would describe a reply (016 T049).
    renderDisclosure(null, null, [act()]);

    expect(
      screen.getByRole("button", { name: "What the assistant did to the schedule" }),
    ).toHaveAttribute("data-outcome-state", "served");
  });

  it("keeps the answer's name for a served reply", () => {
    renderDisclosure([answered(0, "do I need a referral?")]);

    expect(
      screen.getByRole("button", { name: "What this answer drew on" }),
    ).toHaveAttribute("data-outcome-state", "served");
  });

  it("names a message with an unknown act as needing a person", () => {
    renderDisclosure(null, null, [act({ outcome: "unknown" })]);

    expect(
      screen.getByRole("button", { name: "Why this message needs a person" }),
    ).toHaveAttribute("data-outcome-state", "needs-person");
  });
});

describe("OutcomeDisclosure: what an expanded block says (FR-029, FR-030; FR-031 retired by 016 FR-020)", () => {
  function expand(): void {
    fireEvent.click(marker());
  }

  it("lists each request in the order recorded", () => {
    renderDisclosure([
      answered(0, "do I need a referral?"),
      abstained(1, "what does it cost?"),
      answered(2, "what should I bring?"),
    ]);
    expand();

    const positions = screen
      .getAllByTestId("request-outcome")
      .map((el) => el.getAttribute("data-position"));
    expect(positions).toEqual(["0", "1", "2"]);
  });

  it("gives each request its question", () => {
    renderDisclosure([
      answered(0, "do I need a referral?"),
      abstained(1, "what does it cost?"),
    ]);
    expand();

    const questions = screen
      .getAllByTestId("outcome-question")
      .map((el) => el.textContent);
    expect(questions).toEqual(["do I need a referral?", "what does it cost?"]);
  });

  it("gives an answered request the documents it drew on", () => {
    renderDisclosure([answered(0, "do I need a referral?")]);
    expand();

    expect(screen.getByTestId("citations")).toHaveTextContent(
      "Bring your referral letter.",
    );
  });

  it("marks an unanswered request unanswered, and names its reason", () => {
    renderDisclosure([abstained(0, "what does it cost?")]);
    expand();

    const unanswered = screen.getByTestId("outcome-unanswered");
    expect(unanswered).toBeInTheDocument();
    expect(unanswered.textContent).toMatch(/\S/);
    expect(screen.queryByTestId("citations")).toBeNull();
  });

  it("distinguishes unanswered from answered by more than colour", () => {
    // FR-030, and SC-006 reached from the other direction. With every stylesheet thrown
    // away the two still have to read differently — so the difference is an element and
    // a word, not a class that happens to paint something.
    renderDisclosure([
      answered(0, "do I need a referral?"),
      abstained(1, "what does it cost?"),
    ]);
    expand();

    const [first, second] = screen.getAllByTestId("request-outcome");
    expect(first).toHaveAttribute("data-verdict", "answered");
    expect(second).toHaveAttribute("data-verdict", "abstained_empty_pool");
    expect(within(first!).queryByTestId("outcome-unanswered")).toBeNull();
    expect(within(second!).getByTestId("outcome-unanswered")).toBeInTheDocument();
  });

  it("names a differently-worded reason for each kind of abstention", () => {
    // Four verdicts mean four different things and call for four different fixes — an
    // empty corpus is somebody's cue to write an entry, a rerank floor is not. One
    // wording for all of them would put them back into the single uninformative value
    // spec 008 split them out of.
    const verdicts = [
      "abstained_empty_corpus",
      "abstained_empty_pool",
      "abstained_similarity_floor",
      "abstained_rerank_floor",
    ] as const;
    const wordings = new Set<string>();
    for (const verdict of verdicts) {
      const { unmount } = renderDisclosure([abstained(0, "what does it cost?", verdict)]);
      expand();
      wordings.add(screen.getByTestId("outcome-unanswered").textContent ?? "");
      unmount();
    }
    expect(wordings.size).toBe(verdicts.length);
  });

  it("still marks an answer produced without reranking", () => {
    renderDisclosure([answered(0, "do I need a referral?", "answered_unreranked")]);
    expand();

    expect(screen.getByTestId("verdict-mark")).toBeInTheDocument();
    expect(screen.getByTestId("citations")).toBeInTheDocument();
  });

  it("carries no booking line at all on a message without acts", () => {
    // 016 FR-020 retires 015's "not yet recorded" stub and forbids a replacement: the
    // absence of a booking section is what says no write was attempted. Checked on
    // each shape of block the stub used to appear in.
    const shapes: [RequestOutcome[] | null, AttentionMark | null][] = [
      [[answered(0, "do I need a referral?")], null],
      [[abstained(0, "what does it cost?")], null],
      [null, "patient_asked_for_person"],
    ];
    for (const [outcomes, mark] of shapes) {
      const { unmount } = renderDisclosure(outcomes, mark);
      expand();

      expect(screen.queryByTestId("booking-outcome-stub")).toBeNull();
      expect(screen.queryByTestId("booking-act")).toBeNull();
      expect(document.body.textContent).not.toMatch(/booking|booked|schedule/i);
      unmount();
    }
  });

  it("leaves the mark's own words to the message, and renders none itself", () => {
    // The mark stays unexpanded on the message: it is the one thing here a staff member
    // has to see while scanning a thread, and a second copy behind the marker would be
    // two elements carrying one hook.
    renderDisclosure(null, "urgent_condition");
    expand();

    expect(screen.queryByTestId("attention-mark")).toBeNull();
  });

  it("renders no request list for a message carrying only a mark", () => {
    // Null outcomes mean no FAQ half ran, which is not the same as one that ran and
    // answered nothing. Rendering an empty request list would say the second.
    renderDisclosure(null, "urgent_condition");
    expand();

    expect(screen.queryByTestId("request-outcome")).toBeNull();
    expect(screen.queryByTestId("citations")).toBeNull();
    expect(screen.queryByTestId("outcome-unanswered")).toBeNull();
  });
});

describe("OutcomeDisclosure: booking acts on a patient message (016 FR-017 to FR-020)", () => {
  function expand(): void {
    fireEvent.click(marker());
  }

  function actLines(): HTMLElement[] {
    return screen.getAllByTestId("booking-act");
  }

  it("shows a marker for a message holding acts and nothing else", () => {
    // FR-017. A patient message carries no FAQ outcomes, and a successful booking
    // raises no mark, so without this the one message that says what the assistant did
    // to the schedule would be the one message with nothing to open.
    renderDisclosure(null, null, [act()]);

    expect(marker()).toBeInTheDocument();
  });

  it.each(["done", "refused", "unchanged", "not_sent"] as const)(
    "is served when the only act's outcome is %s",
    (outcome) => {
      // FR-018: a refusal was already reported to the patient, and not-sent is known
      // to have changed nothing, so neither is on its own owed to a person.
      renderDisclosure(null, null, [
        act({
          outcome,
          refusal_reason: outcome === "refused" ? "practitioner_busy" : null,
        }),
      ]);

      expect(marker()).toHaveAttribute("data-outcome-state", "served");
    },
  );

  it.each(["unknown", null] as const)(
    "needs a person when an act's outcome is %s",
    (outcome) => {
      // FR-012b: an act never settled reads exactly as one settled as unknown.
      renderDisclosure(null, null, [act(), act({ outcome })]);

      expect(marker()).toHaveAttribute("data-outcome-state", "needs-person");
    },
  );

  it("still needs a person for a mark, whatever the acts say", () => {
    renderDisclosure(null, "assistant_failed", [act()]);

    expect(marker()).toHaveAttribute("data-outcome-state", "needs-person");
  });

  it("lists each act in the order attempted, with its operation and outcome", () => {
    renderDisclosure(null, null, [
      act({ operation: "cancel" }),
      act({ outcome: "refused", refusal_reason: "off_grid" }),
      moved({ outcome: null }),
    ]);
    expand();

    expect(
      actLines().map((el) => [
        el.getAttribute("data-operation"),
        el.getAttribute("data-outcome"),
      ]),
    ).toEqual([
      ["cancel", "done"],
      ["book", "refused"],
      // A null outcome is attributed as unknown, never as a sixth state.
      ["reschedule", "unknown"],
    ]);
  });

  it("says what a done booking, move and cancellation did, and when", () => {
    renderDisclosure(null, null, [
      act(),
      moved(),
      act({ operation: "cancel", starts_at: "2027-01-14T09:30:00" }),
    ]);
    expand();

    expect(actLines().map((el) => el.textContent)).toEqual([
      "Booked: Andreas Vesalius, Tuesday 12 January 2027 at 10:00",
      "Moved: Andreas Vesalius, Tuesday 12 January 2027 at 09:00 → Tuesday 12 January 2027 at 10:00",
      "Cancelled: Andreas Vesalius, Thursday 14 January 2027 at 09:30",
    ]);
  });

  it("names both practitioners when a move changed who the appointment is with", () => {
    renderDisclosure(null, null, [
      moved({ previous_practitioner_full_name: "Hildegard of Bingen" }),
    ]);
    expand();

    expect(actLines()[0]).toHaveTextContent(
      "Moved: Hildegard of Bingen, Tuesday 12 January 2027 at 09:00 → " +
        "Andreas Vesalius, Tuesday 12 January 2027 at 10:00",
    );
  });

  it("names the year on each side of a move across a new year", () => {
    // The 90-day horizon crosses years, and an act is a permanent record read long
    // after the fact: without the year, which January is meant (016 T048).
    renderDisclosure(null, null, [moved({ previous_starts_at: "2026-12-29T09:00:00" })]);
    expand();

    expect(actLines()[0]).toHaveTextContent(
      "Moved: Andreas Vesalius, Tuesday 29 December 2026 at 09:00 → " +
        "Tuesday 12 January 2027 at 10:00",
    );
  });

  it("says so when the record holds no practitioner name", () => {
    renderDisclosure(null, null, [act({ practitioner_full_name: null })]);
    expand();

    expect(actLines()[0]).toHaveTextContent(
      "Booked: a practitioner not named in the record, Tuesday 12 January 2027 at 10:00",
    );
  });

  it("names each end of a move separately when the record names neither", () => {
    // Two absent names say nothing about whether they are one person, and the wire
    // carries no ids to settle it. Folding them into one would present a move between
    // two practitioners - a roster that could not be read, say - as a move with one.
    renderDisclosure(null, null, [
      moved({
        outcome: "refused",
        refusal_reason: "practitioner_busy",
        practitioner_full_name: null,
        previous_practitioner_full_name: null,
      }),
    ]);
    expand();

    expect(actLines()[0]).toHaveTextContent(
      "a practitioner not named in the record, Tuesday 12 January 2027 at 09:00 → " +
        "a practitioner not named in the record, Tuesday 12 January 2027 at 10:00",
    );
  });

  it("says a change that was not needed changed nothing", () => {
    renderDisclosure(null, null, [act({ operation: "cancel", outcome: "unchanged" })]);
    expand();

    const line = actLines()[0]!.textContent ?? "";
    expect(line).toMatch(/^No change needed: /);
    expect(line).toContain("Andreas Vesalius, Tuesday 12 January 2027 at 10:00");
  });

  it("gives a refusal's reason in words, and says nothing was changed", () => {
    renderDisclosure(null, null, [
      act({ outcome: "refused", refusal_reason: "practitioner_busy" }),
    ]);
    expand();

    const line = actLines()[0]!.textContent ?? "";
    expect(line).toMatch(/^Refused \(.+\): /);
    expect(line).toMatch(/Nothing was changed\.$/);
    // The reason is rendered for a person, not left as the scheduler's code.
    expect(line).not.toContain("practitioner_busy");
  });

  it("words each refusal reason differently", () => {
    // Each reason calls for something different — a taken slot is not a closed day —
    // so a staff member reading the line must be able to tell them apart. The twelve
    // are the scheduler's closed set (shared_models.scheduling.ChangeFailureReason,
    // which includes booking's eight).
    const reasons = [
      "practitioner_busy",
      "patient_busy",
      "outside_schedule",
      "off_grid",
      "in_past",
      "beyond_horizon",
      "practitioner_not_found",
      "patient_not_found",
      "appointment_not_found",
      "already_cancelled",
      "already_started",
      "stale_confirmation",
    ];
    const lines = new Set<string>();
    for (const reason of reasons) {
      const { unmount } = renderDisclosure(null, null, [
        act({ outcome: "refused", refusal_reason: reason }),
      ]);
      expand();
      const line = actLines()[0]!.textContent ?? "";
      expect(line).not.toContain(reason);
      expect(line).not.toMatch(/undefined/);
      lines.add(line);
      unmount();
    }
    expect(lines.size).toBe(reasons.length);
  });

  it("still says it was refused for a reason this build does not know", () => {
    renderDisclosure(null, null, [
      act({ outcome: "refused", refusal_reason: "some_future_reason" }),
    ]);
    expand();

    const line = actLines()[0]!.textContent ?? "";
    expect(line).toMatch(/refused by the scheduler/i);
    expect(line).toMatch(/Nothing was changed\.$/);
    expect(line).not.toMatch(/undefined/);
  });

  it("says a request that was never sent changed nothing", () => {
    renderDisclosure(null, null, [act({ outcome: "not_sent" })]);
    expand();

    const line = actLines()[0]!.textContent ?? "";
    expect(line).toMatch(/^Not sent: /);
    expect(line).toMatch(/Nothing was changed\.$/);
  });

  it.each(["unknown", null] as const)(
    "says in words that a %s outcome may or may not have happened",
    (outcome) => {
      // FR-019: "unknown" is in the text, never carried by colour alone, and the line
      // must not claim or imply that nothing changed.
      renderDisclosure(null, null, [
        act({ outcome }),
        moved({ outcome }),
        act({ operation: "cancel", outcome }),
      ]);
      expand();

      const [booked, move, cancelled] = actLines().map((el) => el.textContent ?? "");
      expect(booked).toBe(
        "Outcome unknown: Andreas Vesalius, Tuesday 12 January 2027 at 10:00 " +
          "may or may not have been booked. Check the schedule.",
      );
      expect(move).toMatch(
        /^Outcome unknown: .* may or may not have been moved\. Check the schedule\.$/,
      );
      expect(cancelled).toMatch(
        /may or may not have been cancelled\. Check the schedule\.$/,
      );
      for (const line of [booked, move, cancelled]) {
        expect(line).not.toMatch(/nothing was changed/i);
      }
    },
  );

  it("adds no FAQ lines or retrieval note to a block holding only acts", () => {
    renderDisclosure(null, null, [act()]);
    expand();

    expect(screen.queryByTestId("request-outcome")).toBeNull();
    expect(document.body.textContent).not.toMatch(/retrieved/i);
    expect(screen.queryByTestId("booking-outcome-stub")).toBeNull();
  });
});
