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

    const names = screen
      .getAllByTestId("staff-conversation")
      .map((el) => el.textContent?.split("\n")[0]?.trim());
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
        escalated: true,
        escalation_reason: "patient_asked_for_person",
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

  it("names the reason a conversation needs a person", () => {
    renderConsole([
      conversation({
        chat_id: "a",
        patient_name: "Ada",
        emphasized: true,
        escalated: true,
        escalation_reason: "patient_asked_for_person",
      }),
    ]);

    expect(screen.getByTestId("staff-conversation").textContent).not.toBe("Ada");
  });

  it("names a corpus gap, which is a cause no message carries but the labels do cover", () => {
    renderConsole([
      conversation({
        chat_id: "a",
        patient_name: "Ada",
        emphasized: true,
        escalated: true,
        escalation_reason: "corpus_could_not_answer",
      }),
    ]);

    expect(screen.getByTestId("staff-conversation")).toHaveTextContent(
      "No answer in the clinic's documents",
    );
  });

  it("falls back to a plain statement for a cause it does not recognise", () => {
    // `escalation_reason` is typed `string`, not the mark union, so the server can name
    // a cause this build has never heard of — a later phase adding one is the ordinary
    // way that happens. Rendering `undefined` there, or inventing a wording for it,
    // would both be this screen claiming to know something it does not.
    renderConsole([
      conversation({
        chat_id: "a",
        patient_name: "Ada",
        emphasized: true,
        escalated: true,
        escalation_reason: "a_cause_this_build_has_never_heard_of",
      }),
    ]);

    const row = screen.getByTestId("staff-conversation");
    expect(row).toHaveTextContent("Needs a person");
    expect(row).not.toHaveTextContent("undefined");
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
        escalated: true,
        escalation_reason: "patient_asked_for_person",
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
