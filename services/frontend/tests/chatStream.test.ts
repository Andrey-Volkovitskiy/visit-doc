import { describe, expect, it } from "vitest";
import { parseNdjsonStream, type ChatEvent } from "../src/lib/chatStream";

function fakeResponse(lines: string[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const encoder = new TextEncoder();
      for (const line of lines) {
        controller.enqueue(encoder.encode(line + "\n"));
      }
      controller.close();
    },
  });
  return new Response(body);
}

describe("parseNdjsonStream", () => {
  it("accumulates token text and carries the terminal done event", async () => {
    const response = fakeResponse([
      JSON.stringify({ type: "token", text: "Visiting " }),
      JSON.stringify({ type: "token", text: "hours are 8am to 5pm." }),
      JSON.stringify({
        type: "done",
        grounded: true,
        citations: [
          { entry_id: 1, chunk_index: 0, chunk_text: "Visiting hours are 8am to 5pm." },
        ],
      }),
    ]);

    const events: ChatEvent[] = [];
    for await (const event of parseNdjsonStream(response)) {
      events.push(event);
    }

    const text = events
      .filter((e): e is Extract<ChatEvent, { type: "token" }> => e.type === "token")
      .map((e) => e.text)
      .join("");
    const done = events[events.length - 1];

    expect(text).toBe("Visiting hours are 8am to 5pm.");
    expect(done).toEqual({
      type: "done",
      grounded: true,
      citations: [{ entry_id: 1, chunk_index: 0, chunk_text: "Visiting hours are 8am to 5pm." }],
    });
  });

  it("carries an abstention message when not grounded", async () => {
    const response = fakeResponse([
      JSON.stringify({
        type: "done",
        grounded: false,
        citations: [],
        message: "I don't have a confident answer to that.",
      }),
    ]);

    const events: ChatEvent[] = [];
    for await (const event of parseNdjsonStream(response)) {
      events.push(event);
    }

    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ type: "done", grounded: false });
  });
});
