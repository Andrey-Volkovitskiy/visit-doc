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
    attention_since: null,
    assistant_may_reply: true,
    pause_seconds_remaining: null,
    booking_acts_version: 0,
    ...overrides,
  };
}

function renderConsole(
  conversations: ConsoleConversation[],
  onSelect = vi.fn(),
) {
  render(
    <StaffConsole
      conversations={conversations}
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

// The three attention-total tests that stood here now live in `App.test.tsx`.
//
// The total moved out of this component and into the console header, outside the tabbed
// region, so that it stays visible when staff open Practitioners (FR-021). The
// properties it protected are unchanged and are re-expressed there, against the owner
// that now renders it: a zero renders as zero, and the number shown is the server's
// total rather than a count of the rows on screen (FR-038).

describe("StaffConsole: marking without relying on colour (FR-022, FR-006)", () => {
  it("lists every conversation in the server's order, marked or not", () => {
    renderConsole([
      conversation({ chat_id: "a", patient_name: "First", emphasized: true }),
      conversation({ chat_id: "b", patient_name: "Second" }),
      conversation({ chat_id: "c", patient_name: "Third", emphasized: true }),
    ]);

    // The words a marked row carries for a screen reader are part of its text, so the
    // name is read with them taken out.
    const names = screen
      .getAllByTestId("staff-conversation")
      .map((el) =>
        el.textContent?.replace("Needs a person", "").split("\n")[0]?.trim(),
      );
    expect(names).toEqual(["First", "Second", "Third"]);
  });

  it("marks a conversation needing a person by something that is not colour", () => {
    // SC-006: sender and attention state survive without colour perception. The mark
    // has to be readable from the DOM with every stylesheet thrown away — so it is an
    // element and a word, not a class name that happens to paint something red.
    renderConsole([
      conversation({ chat_id: "a", patient_name: "Quiet" }),
      conversation({
        chat_id: "b",
        patient_name: "Needs help",
        emphasized: true,
      }),
    ]);

    const [quiet, needsHelp] = screen.getAllByTestId("staff-conversation");
    expect(needsHelp).toHaveAttribute("data-emphasized", "true");
    expect(quiet).toHaveAttribute("data-emphasized", "false");

    // Text, present in one row and absent from the other.
    expect(needsHelp!.textContent).toMatch(/\S/);
    expect(needsHelp!.textContent).not.toBe(quiet!.textContent);
    expect(needsHelp!.querySelector("svg")).not.toBeNull();
    expect(quiet!.querySelector("svg")).toBeNull();
  });

  it("says to a screen reader, and not on screen, that a row needs a person", () => {
    // The list marks a conversation; the thread one click away explains it. So the
    // words are carried in the accessibility tree alone — dropping them from there too
    // would leave a screen-reader user with a row indistinguishable from a quiet one,
    // since the glyph beside it is a picture with no name.
    renderConsole([
      conversation({ chat_id: "a", patient_name: "Quiet" }),
      conversation({ chat_id: "b", patient_name: "Ada", emphasized: true }),
    ]);

    const [quiet, needsHelp] = screen.getAllByTestId("staff-conversation");
    const words = screen.getByText("Needs a person");
    expect(needsHelp).toContainElement(words);
    // jsdom loads no stylesheet, so `sr-only` is what says "in the tree, off the
    // screen" — there is nothing else here that could say it.
    expect(words).toHaveClass("sr-only");
    expect(quiet!.querySelector(".sr-only")).toBeNull();
  });

  it("tells a rail that has not heard back from one that arrived empty", () => {
    // FR-010a. The two ask different things of the reader, so they must not render the
    // same thing.
    const { unmount } = render(
      <StaffConsole
        conversations={[]}
        activeChatId={null}
        onSelect={vi.fn()}
        loaded={false}
      />,
    );
    expect(screen.getByTestId("region-loading")).toHaveAttribute(
      "data-region",
      "conversations",
    );
    expect(screen.queryByTestId("staff-no-conversations")).toBeNull();
    unmount();

    renderConsole([]);
    expect(screen.getByTestId("staff-no-conversations")).toBeInTheDocument();
    expect(screen.queryByTestId("region-loading")).toBeNull();
  });
});

describe("StaffConsole renders no times (FR-010b)", () => {
  it("shows no conversation's waiting time, however long it has waited", () => {
    renderConsole([
      conversation({
        chat_id: "a",
        patient_name: "Ada",
        emphasized: true,
        attention_since: "2026-09-01T09:41:00",
        last_message_at: "2026-09-01T09:41:00",
      }),
    ]);

    const rail = screen.getByTestId("staff-console").textContent ?? "";
    expect(rail).not.toMatch(/\d{1,2}:\d{2}/);
    expect(rail).not.toMatch(/2026-09-01/);
    expect(rail).not.toMatch(/\bago\b|\bwaiting for\b|\bminutes?\b/i);
  });
});
