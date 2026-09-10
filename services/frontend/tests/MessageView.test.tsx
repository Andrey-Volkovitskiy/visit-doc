import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { AttentionMark, Citation, RequestOutcome } from "../src/lib/chatStream";
import { MessageView } from "../src/components/MessageView";

function chunk(entryId: number): Citation {
  return { entry_id: entryId, chunk_index: 0, chunk_text: `chunk ${entryId}` };
}

function answered(
  position: number,
  question: string,
  answer: string,
  entryId = 1,
): RequestOutcome {
  return {
    position,
    question,
    answer,
    verdict: "answered",
    citations: [{ entry_id: entryId, chunk_index: 0, chunk_text: `chunk ${entryId}` }],
  };
}

function abstained(position: number, question: string): RequestOutcome {
  return {
    position,
    question,
    answer: null,
    verdict: "abstained_empty_pool",
    citations: [],
  };
}

describe("MessageView", () => {
  it("renders a patient message", () => {
    render(<MessageView sender="patient" content="When can I see Dr. Josh?" />);
    const message = screen.getByTestId("message");
    expect(message).toHaveAttribute("data-sender", "patient");
    expect(message).toHaveTextContent("When can I see Dr. Josh?");
  });

  it("renders an assistant message with citations when asked to show them", () => {
    render(
      <MessageView
        sender="assistant"
        content="Visiting hours are 8am to 5pm."
        showOutcomes
        requestOutcomes={[answered(0, "when can I visit?", "Visiting hours are 8am to 5pm.")]}
      />,
    );
    expect(screen.getByTestId("message")).toHaveTextContent("Visiting hours are 8am to 5pm.");
    expect(screen.getByTestId("citations")).toHaveTextContent("chunk 1");
  });

  it("shows no derived 'unanswered' indicator for a patient message with no reply yet", () => {
    // A mid-burst patient message (FR-014) with no assistant reply yet is the normal
    // shape of a message, not a failure signal - MessageView must not editorialize
    // (research.md #8).
    render(<MessageView sender="patient" content="Dr. Josh?" />);
    expect(screen.queryByText(/unanswered|no reply|pending|failed/i)).toBeNull();
  });

  it("preserves newlines in message content as visible line breaks", () => {
    render(<MessageView sender="patient" content={"line one\nline two"} />);
    const paragraph = screen.getByText((_, element) => element?.tagName === "P" && element.textContent === "line one\nline two");
    expect(paragraph).toHaveStyle({ whiteSpace: "pre-wrap" });
  });

  it("renders no citations list when there are none", () => {
    render(
      <MessageView sender="assistant" content="I don't have a confident answer to that." />,
    );
    expect(screen.queryByTestId("citations")).toBeNull();
  });
});

describe("MessageView request outcomes", () => {
  it("renders a booking reply with no outcome block", () => {
    render(
      <MessageView
        sender="assistant"
        content="You're booked for Tuesday at 9."
        showOutcomes
        requestOutcomes={null}
      />,
    );

    expect(screen.getByText("You're booked for Tuesday at 9.")).toBeInTheDocument();
    expect(screen.queryByTestId("request-outcome")).toBeNull();
    expect(screen.queryByTestId("citations")).toBeNull();
  });

  it("renders one block per outcome, each carrying its own citations", () => {
    render(
      <MessageView
        sender="assistant"
        content="We are at 5 Oak Street, and hours are 8 to 5."
        showOutcomes
        requestOutcomes={[
          answered(0, "where are you?", "We are at 5 Oak Street.", 1),
          answered(1, "when are you open?", "Open 8 to 5.", 2),
        ]}
      />,
    );

    const blocks = screen.getAllByTestId("request-outcome");
    expect(blocks).toHaveLength(2);
    expect(blocks[0]).toHaveTextContent("where are you?");
    expect(blocks[0]).toHaveTextContent("chunk 1");
    expect(blocks[1]).toHaveTextContent("when are you open?");
    expect(blocks[1]).toHaveTextContent("chunk 2");
  });

  it("marks the degraded block, and marks nothing on the message", () => {
    // The marker was message-level because the verdict was. A message-level marker
    // would now be a claim about requests it does not describe.
    render(
      <MessageView
        sender="assistant"
        content="..."
        showOutcomes
        requestOutcomes={[
          answered(0, "where are you?", "We are at 5 Oak Street.", 1),
          {
            ...answered(1, "when are you open?", "Open 8 to 5.", 2),
            verdict: "answered_unreranked",
          },
        ]}
      />,
    );

    const blocks = screen.getAllByTestId("request-outcome");
    expect(within(blocks[0]).queryByTestId("verdict-mark")).toBeNull();
    expect(within(blocks[1]).getByTestId("verdict-mark")).toHaveAttribute(
      "title",
      expect.stringContaining("reranking"),
    );
    expect(screen.getByTestId("message")).not.toHaveAttribute("data-faq-verdict");
  });

  it.each([
    "answered",
    "abstained_empty_corpus",
    "abstained_empty_pool",
    "abstained_similarity_floor",
    "abstained_rerank_floor",
  ] as const)("leaves %s unmarked - a marker on every block marks nothing", (verdict) => {
    render(
      <MessageView
        sender="assistant"
        content="..."
        showOutcomes
        requestOutcomes={[{ ...abstained(0, "where are you?"), verdict, ...(verdict === "answered" ? { answer: "5 Oak Street.", citations: [chunk(1)] } : {}) }]}
      />,
    );

    expect(screen.queryByTestId("verdict-mark")).toBeNull();
  });

  it("draws nothing about the outcomes unless it is told to", () => {
    // The patient pane passes nothing; the staff console passes showOutcomes. The
    // outcomes are in the payload either way - this is what is drawn, not what is sent.
    render(
      <MessageView
        sender="assistant"
        content="Visiting hours are 8am to 5pm."
        requestOutcomes={[
          answered(0, "when can I visit?", "Visiting hours are 8am to 5pm.", 1),
        ]}
      />,
    );

    expect(screen.queryByTestId("request-outcome")).toBeNull();
    expect(screen.queryByTestId("citations")).toBeNull();
    expect(screen.queryByTestId("outcome-question")).toBeNull();
    expect(screen.queryByTestId("verdict-mark")).toBeNull();
  });

  it("keeps a degraded block distinguishable from an attention mark", () => {
    // One sits on an assistant message and means the answer above it is second-best;
    // the other sits on a patient message and means a person is needed.
    render(
      <MessageView
        sender="assistant"
        content="..."
        showOutcomes
        requestOutcomes={[
          {
            ...answered(0, "where are you?", "We are at 5 Oak Street.", 1),
            verdict: "answered_unreranked",
          },
        ]}
      />,
    );

    expect(screen.getByTestId("verdict-mark")).toBeInTheDocument();
    expect(screen.queryByTestId("attention-mark")).toBeNull();
  });
});

describe("MessageView unanswered requests", () => {
  it("renders an abstained request's question verbatim, and says nobody answered it", () => {
    render(
      <MessageView
        sender="assistant"
        content="We are at 5 Oak Street. I don't have the rest."
        showOutcomes
        requestOutcomes={[
          answered(0, "where are you?", "We are at 5 Oak Street.", 1),
          abstained(1, "what does a scan cost?"),
        ]}
      />,
    );

    const blocks = screen.getAllByTestId("request-outcome");
    // Verbatim: the classifier's restatement, which is what was retrieved for and what
    // a staff member is acting on.
    expect(blocks[1]).toHaveTextContent("what does a scan cost?");
    const unanswered = within(blocks[1]).getByTestId("outcome-unanswered");
    expect(unanswered).toHaveTextContent(/not answered/i);
    expect(unanswered).toHaveTextContent(/staff/i);
    // And only that one: the answered block says nothing of the kind.
    expect(within(blocks[0]).queryByTestId("outcome-unanswered")).toBeNull();
    expect(within(blocks[1]).queryByTestId("citations")).toBeNull();
  });

  it("draws the unanswered line nowhere in the patient pane", () => {
    render(
      <MessageView
        sender="assistant"
        content="We are at 5 Oak Street. I don't have the rest."
        requestOutcomes={[abstained(0, "what does a scan cost?")]}
      />,
    );

    expect(screen.queryByTestId("outcome-unanswered")).toBeNull();
    expect(screen.queryByText("what does a scan cost?")).toBeNull();
  });
});

// --- 007 (FR-023): three senders, two labels --------------------------------------
//
// With two senders, position and styling were enough and neither needed a label. With
// three, the patient has to be able to tell a human's reply from a generated one -
// which is what FR-021 is for, and which a role label does exactly.

describe("MessageView role labels", () => {
  it("labels a staff message 'Staff'", () => {
    render(<MessageView sender="staff" content="I've looked at your bill." />);

    const message = screen.getByTestId("message");
    expect(message).toHaveAttribute("data-sender", "staff");
    expect(message).toHaveTextContent("Staff");
  });

  it("labels an assistant message 'AI assistant'", () => {
    render(<MessageView sender="assistant" content="Visiting hours are 8am to 5pm." />);

    expect(screen.getByTestId("message")).toHaveTextContent("AI assistant");
  });

  it("leaves the patient's own messages unlabelled", () => {
    // They are the reader's own; a label would say nothing they do not already know.
    render(<MessageView sender="patient" content="When can I visit?" />);

    expect(screen.queryByTestId("role-label")).toBeNull();
  });

  it("names no person on any message", () => {
    // There is no staff member to name (research #10), and a human-sounding name would
    // invite the patient to believe there is one.
    const { container } = render(
      <MessageView sender="staff" content="I've got this one." />,
    );

    expect(container.textContent).toBe("StaffI've got this one.");
  });

  it("tells a staff reply apart from an assistant one at a glance", () => {
    const staff = render(<MessageView sender="staff" content="Same words." />);
    const staffLabel = staff.getByTestId("role-label").textContent;
    staff.unmount();
    const assistant = render(<MessageView sender="assistant" content="Same words." />);

    expect(assistant.getByTestId("role-label").textContent).not.toBe(staffLabel);
  });
});

describe("marks the assistant's new causes leave", () => {
  it("names which of them it is, never a generic 'needs attention'", () => {
    const causes: Array<[AttentionMark, string]> = [
      ["urgent_condition", "Urgent condition"],
      ["distress", "Patient in distress"],
      ["booking_for_another_person", "Booking for someone else"],
      ["not_authorized", "Not something the assistant may do"],
    ];
    for (const [mark, label] of causes) {
      const { unmount } = render(
        <MessageView sender="patient" content="anything" mark={mark} />,
      );
      expect(screen.getByTestId("attention-mark")).toHaveTextContent(label);
      unmount();
    }
  });
});
