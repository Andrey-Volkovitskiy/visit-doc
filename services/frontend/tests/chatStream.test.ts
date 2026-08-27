import { describe, expect, it, vi } from "vitest";
import {
  askChat,
  createChat,
  fetchChatHistory,
  fetchChats,
  localNow,
  parseNdjsonStream,
  renameChatPatient,
  type ChatEvent,
} from "../src/lib/chatStream";

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
        answer_source: "faq",
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
      answer_source: "faq",
      citations: [{ entry_id: 1, chunk_index: 0, chunk_text: "Visiting hours are 8am to 5pm." }],
    });
  });

  it("carries an abstention message when not grounded", async () => {
    const response = fakeResponse([
      JSON.stringify({
        type: "done",
        grounded: false,
        citations: [],
        answer_source: "faq",
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

  it("carries a booking reply with no groundedness verdict and no citations", async () => {
    const response = fakeResponse([
      JSON.stringify({ type: "token", text: "You're booked for Tuesday at 9." }),
      JSON.stringify({
        type: "done",
        grounded: null,
        citations: [],
        answer_source: "booking",
      }),
    ]);

    const events: ChatEvent[] = [];
    for await (const event of parseNdjsonStream(response)) {
      events.push(event);
    }

    expect(events[events.length - 1]).toMatchObject({
      grounded: null,
      answer_source: "booking",
      citations: [],
    });
  });
});

describe("localNow", () => {
  it("renders the browser's wall-clock time with no offset and no Z", () => {
    expect(localNow(new Date(2026, 7, 14, 9, 5, 3))).toBe("2026-08-14T09:05:03");
  });

  it("does not convert to UTC, which would move the wall-clock time", () => {
    const rendered = localNow(new Date(2026, 7, 14, 23, 30, 0));
    expect(rendered).toBe("2026-08-14T23:30:00");
    expect(rendered).not.toContain("Z");
    expect(rendered).not.toMatch(/[+-]\d{2}:\d{2}$/);
  });
});

describe("askChat", () => {
  it("sends the chat id and a local_now taken from the browser's clock", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(fakeResponse([]));

    await askChat("01CHAT000000000000000000", "when can I visit?");

    const body = JSON.parse(fetchSpy.mock.calls[0][1]!.body as string);
    expect(body.chat_id).toBe("01CHAT000000000000000000");
    expect(body.message).toBe("when can I visit?");
    expect(body.local_now).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/);
    fetchSpy.mockRestore();
  });

  it("throws on an error response instead of parsing its body as an event", async () => {
    // A 404 body is valid JSON, so without the status check parseNdjsonStream yields
    // it as one event with no `type` — which the caller's terminal-event branch then
    // treats as a completed turn, showing an empty assistant bubble and no error.
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "chat not found" }), { status: 404 }),
    );

    await expect(
      askChat("01CHAT000000000000000000", "when can I visit?"),
    ).rejects.toThrow();
    fetchSpy.mockRestore();
  });
});

describe("fetchChats", () => {
  it("throws on an error response rather than returning a listing-shaped object", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ detail: "nope" }), { status: 500 }));

    await expect(fetchChats()).rejects.toThrow();
    fetchSpy.mockRestore();
  });
});

describe("createChat", () => {
  it("throws on an error response rather than yielding a chat with no id", async () => {
    // An undefined id slips past every `chatId === null` guard downstream.
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ detail: "nope" }), { status: 500 }));

    await expect(createChat()).rejects.toThrow();
    fetchSpy.mockRestore();
  });
});

describe("fetchChatHistory", () => {
  it("throws on an error response rather than returning undefined messages", async () => {
    // `data.messages` would be undefined, and the caller renders it with `.map`.
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ detail: "nope" }), { status: 404 }));

    await expect(fetchChatHistory("01CHAT000000000000000000")).rejects.toThrow();
    fetchSpy.mockRestore();
  });
});

describe("renameChatPatient", () => {
  it("sends the new name and returns what the server stored", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ chat_id: "a", patient_name: "Grace B. Hopper" }),
        { status: 200 },
      ),
    );

    const result = await renameChatPatient("a", "Grace Hopper");

    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("/chats/a/patient");
    expect(init!.method).toBe("PATCH");
    expect(JSON.parse(init!.body as string)).toEqual({ full_name: "Grace Hopper" });
    // The server owns the value: what it echoed is what the caller gets back.
    expect(result.patient_name).toBe("Grace B. Hopper");
    fetchSpy.mockRestore();
  });

  it("surfaces the server's own explanation for a refused name", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ detail: "another chat in this session already uses that name" }),
        { status: 409 },
      ),
    );

    await expect(renameChatPatient("a", "Ada")).rejects.toThrow(
      "another chat in this session already uses that name",
    );
    fetchSpy.mockRestore();
  });

  it("does not claim the name was saved when the outcome is unknown", async () => {
    // A 504 means the server stopped waiting for scheduling, which does not prove the
    // rename was not applied - so the message must not say it was not.
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ detail: "..." }), { status: 504 }));

    await expect(renameChatPatient("a", "Grace")).rejects.toThrow(/may not have been saved/);
    fetchSpy.mockRestore();
  });

  it("says nothing was renamed when scheduling was never reached", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ detail: "..." }), { status: 503 }));

    await expect(renameChatPatient("a", "Grace")).rejects.toThrow(/nothing was renamed/);
    fetchSpy.mockRestore();
  });

  it("falls back to a usable message when the error body is not JSON", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("<html>proxy error</html>", { status: 409 }));

    await expect(renameChatPatient("a", "Grace")).rejects.toThrow(
      "That name cannot be used for this chat.",
    );
    fetchSpy.mockRestore();
  });
});
