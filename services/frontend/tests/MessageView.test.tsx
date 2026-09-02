import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MessageView } from "../src/components/MessageView";

describe("MessageView", () => {
  it("renders a patient message", () => {
    render(<MessageView sender="patient" content="When can I see Dr. Josh?" />);
    const message = screen.getByTestId("message");
    expect(message).toHaveAttribute("data-sender", "patient");
    expect(message).toHaveTextContent("When can I see Dr. Josh?");
  });

  it("renders an assistant message with citations", () => {
    render(
      <MessageView
        sender="assistant"
        content="Visiting hours are 8am to 5pm."
        citations={[
          { entry_id: 1, chunk_index: 0, chunk_text: "Visiting hours are 8am to 5pm." },
        ]}
      />,
    );
    expect(screen.getByTestId("message")).toHaveTextContent("Visiting hours are 8am to 5pm.");
    expect(screen.getByTestId("citations")).toHaveTextContent(
      "Visiting hours are 8am to 5pm.",
    );
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

describe("MessageView booking replies", () => {
  it("renders a booking reply with no citation block", () => {
    render(
      <MessageView
        sender="assistant"
        content="You're booked for Tuesday at 9."
        citations={[]}
        grounded={null}
      />,
    );

    expect(screen.getByText("You're booked for Tuesday at 9.")).toBeInTheDocument();
    expect(screen.queryByTestId("citations")).toBeNull();
    expect(screen.getByTestId("message")).not.toHaveAttribute("data-grounded");
  });

  it("marks a grounded FAQ reply distinctly from a booking one", () => {
    render(
      <MessageView
        sender="assistant"
        content="Visiting hours are 8am to 5pm."
        citations={[{ entry_id: 1, chunk_index: 0, chunk_text: "8am to 5pm." }]}
        grounded={true}
      />,
    );

    expect(screen.getByTestId("message")).toHaveAttribute("data-grounded", "true");
    expect(screen.getByTestId("citations")).toBeInTheDocument();
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
