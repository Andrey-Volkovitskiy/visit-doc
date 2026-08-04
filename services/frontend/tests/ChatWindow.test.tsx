import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChatWindow } from "../src/components/ChatWindow";
import * as chatStream from "../src/lib/chatStream";
import type { ChatEvent } from "../src/lib/chatStream";

async function* fakeEvents(events: ChatEvent[]): AsyncGenerator<ChatEvent> {
  for (const event of events) {
    yield event;
  }
}

describe("ChatWindow", () => {
  it("renders streamed tokens and citations for a grounded answer", async () => {
    vi.spyOn(chatStream, "askChat").mockResolvedValue(
      fakeEvents([
        { type: "token", text: "Visiting " },
        { type: "token", text: "hours are 8am to 5pm." },
        {
          type: "done",
          grounded: true,
          citations: [
            { entry_id: 1, chunk_index: 0, chunk_text: "Visiting hours are 8am to 5pm." },
          ],
        },
      ]),
    );

    render(<ChatWindow />);
    fireEvent.change(screen.getByLabelText("question"), {
      target: { value: "when can I visit?" },
    });
    fireEvent.click(screen.getByText("Send"));

    await waitFor(() => {
      expect(screen.getByTestId("answer").textContent).toBe("Visiting hours are 8am to 5pm.");
    });
    expect(screen.getByTestId("citations").textContent).toContain(
      "Visiting hours are 8am to 5pm.",
    );
  });

  it("renders the abstention message when not grounded", async () => {
    vi.spyOn(chatStream, "askChat").mockResolvedValue(
      fakeEvents([
        {
          type: "done",
          grounded: false,
          citations: [],
          message: "I don't have a confident answer to that.",
        },
      ]),
    );

    render(<ChatWindow />);
    fireEvent.change(screen.getByLabelText("question"), {
      target: { value: "what's the weather?" },
    });
    fireEvent.click(screen.getByText("Send"));

    await waitFor(() => {
      expect(screen.getByTestId("answer").textContent).toBe(
        "I don't have a confident answer to that.",
      );
    });
    expect(screen.queryByTestId("citations")).toBeNull();
  });
});
