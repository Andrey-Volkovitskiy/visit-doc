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
