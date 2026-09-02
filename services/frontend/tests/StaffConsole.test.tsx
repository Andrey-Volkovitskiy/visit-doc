import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { StaffConsole } from "../src/components/StaffConsole";
import type { ConsoleConversation } from "../src/lib/consoleApi";

function conversation(
  overrides: Partial<ConsoleConversation> = {},
): ConsoleConversation {
  return {
    chat_id: "01CHAT",
    patient_name: "Ada Lovelace",
    last_message_at: null,
    emphasized: false,
    escalated: false,
    escalation_reason: null,
    attention_since: null,
    assistant_may_reply: true,
    pause_seconds_remaining: null,
    ...overrides,
  };
}

function renderConsole(
  conversations: ConsoleConversation[],
  attentionTotal = 0,
  onSelect = vi.fn(),
) {
  render(
    <StaffConsole
      conversations={conversations}
      attentionTotal={attentionTotal}
      activeChatId={null}
      onSelect={onSelect}
    />,
  );
  return onSelect;
}

describe("StaffConsole: the list", () => {
  it("renders every conversation, emphasized or not", () => {
    renderConsole([
      conversation({ chat_id: "a", patient_name: "Ada Lovelace" }),
      conversation({ chat_id: "b", patient_name: "Grace Hopper", emphasized: true }),
    ]);

    expect(screen.getAllByTestId("staff-conversation")).toHaveLength(2);
  });

  it("marks the ones needing a person as needing one", () => {
    // Every reason looks identical at this level: the list says a conversation needs a
    // person, and the message inside it says why.
    renderConsole([
      conversation({ chat_id: "a" }),
      conversation({ chat_id: "b", emphasized: true }),
    ]);

    const rows = screen.getAllByTestId("staff-conversation");
    expect(rows[0]).toHaveAttribute("data-emphasized", "false");
    expect(rows[1]).toHaveAttribute("data-emphasized", "true");
  });

  it("renders the server's order without re-sorting it", () => {
    // The ordering rule — emphasized first, longest wait first — lives in the one
    // query that can see every conversation. Re-deriving it here would be a second
    // copy that can disagree with the total beside it.
    renderConsole([
      conversation({ chat_id: "waiting-longest", patient_name: "First" }),
      conversation({ chat_id: "waiting-less", patient_name: "Second" }),
      conversation({ chat_id: "quiet", patient_name: "Third" }),
    ]);

    const names = screen
      .getAllByTestId("staff-conversation")
      .map((node) => node.textContent);
    expect(names).toEqual(["First", "Second", "Third"]);
  });

  it("opens the conversation that was clicked", () => {
    const onSelect = renderConsole([conversation({ chat_id: "01OPEN" })]);

    fireEvent.click(screen.getByTestId("staff-conversation"));

    expect(onSelect).toHaveBeenCalledWith("01OPEN");
  });

  it("says so plainly when the session holds no conversations", () => {
    renderConsole([]);

    expect(screen.getByTestId("staff-no-conversations")).toBeInTheDocument();
  });
});

describe("StaffConsole: the attention total", () => {
  it("renders how many conversations need a person", () => {
    renderConsole(
      [conversation({ chat_id: "a", emphasized: true })],
      1,
    );

    expect(screen.getByTestId("attention-total")).toHaveTextContent("1");
  });

  it("renders a zero total as plainly zero rather than hiding it", () => {
    // A missing badge and a badge reading zero say different things: one is "nothing
    // needs you", the other is "this may not be working".
    renderConsole([conversation({ chat_id: "a" })], 0);

    expect(screen.getByTestId("attention-total")).toHaveTextContent("0");
  });

  it("shows the server's total, not a count of the rows it happens to hold", () => {
    // The total counts a conversation once however many marks sit inside it, and the
    // server is the only thing that can see that.
    renderConsole(
      [
        conversation({ chat_id: "a", emphasized: true }),
        conversation({ chat_id: "b", emphasized: true }),
      ],
      2,
    );

    expect(screen.getByTestId("attention-total")).toHaveTextContent("2");
  });
});
