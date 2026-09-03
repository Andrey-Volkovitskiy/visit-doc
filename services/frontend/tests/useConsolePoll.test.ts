import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as consoleApi from "../src/lib/consoleApi";
import { useConsolePoll } from "../src/lib/useConsolePoll";
import type { ConsoleListing } from "../src/lib/consoleApi";

function listing(overrides: Partial<ConsoleListing> = {}): ConsoleListing {
  return { attention_total: 0, conversations: [], ...overrides };
}

function conversation(
  overrides: Partial<ConsoleListing["conversations"][number]> = {},
): ConsoleListing["conversations"][number] {
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

function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
} {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("useConsolePoll", () => {
  it("reads the listing once immediately, so the first paint is not blank", async () => {
    const fetchListing = vi
      .spyOn(consoleApi, "fetchConsoleListing")
      .mockResolvedValue(listing({ attention_total: 1 }));

    const { result } = renderHook(() => useConsolePoll());

    await vi.waitFor(() => expect(fetchListing).toHaveBeenCalledTimes(1));
    await vi.waitFor(() => expect(result.current.attentionTotal).toBe(1));
  });

  it("polls the one endpoint every two seconds", async () => {
    // One endpoint serves both panes, so the recurring cost of the console is bounded
    // by the interval rather than by how much is happening.
    const fetchListing = vi
      .spyOn(consoleApi, "fetchConsoleListing")
      .mockResolvedValue(listing());

    renderHook(() => useConsolePoll());
    await vi.waitFor(() => expect(fetchListing).toHaveBeenCalledTimes(1));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(fetchListing).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(fetchListing).toHaveBeenCalledTimes(3);
  });

  it("keeps the last good answer when a poll fails, and corrects itself next tick", async () => {
    // A poll reads stored state, so it self-heals: a dropped answer costs one interval
    // of staleness, where a dropped push would leave the pane wrong forever with
    // nothing to correct it. That is the whole argument for polling here, so a failed
    // tick must not blank the pane or raise an error at the user.
    const fetchListing = vi
      .spyOn(consoleApi, "fetchConsoleListing")
      .mockResolvedValueOnce(listing({ attention_total: 2 }))
      .mockRejectedValueOnce(new Error("network blip"))
      .mockResolvedValue(listing({ attention_total: 3 }));

    const { result } = renderHook(() => useConsolePoll());
    await vi.waitFor(() => expect(result.current.attentionTotal).toBe(2));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(result.current.attentionTotal).toBe(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    await vi.waitFor(() => expect(result.current.attentionTotal).toBe(3));
    expect(fetchListing).toHaveBeenCalledTimes(3);
  });

  it("counts the answers it got, not the ticks it attempted", async () => {
    // The panes that refetch an open thread watch this to know a tick happened at all:
    // a read of theirs that failed leaves the message it was fetching the newest one, so
    // the value they actually compare stops changing and could never wake them again. A
    // failed tick corrects nothing and must not pass for one of those answers.
    const fetchListing = vi
      .spyOn(consoleApi, "fetchConsoleListing")
      .mockResolvedValueOnce(listing())
      .mockRejectedValueOnce(new Error("network blip"))
      .mockResolvedValue(listing());

    const { result } = renderHook(() => useConsolePoll());
    await vi.waitFor(() => expect(result.current.tick).toBe(1));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(result.current.tick).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    await vi.waitFor(() => expect(result.current.tick).toBe(2));
    expect(fetchListing).toHaveBeenCalledTimes(3);
  });

  it("keeps the newer answer when an older tick lands after it", async () => {
    // The interval fires on the clock, not on the previous read, so a slow tick can
    // answer after a quicker later one. Applying it would roll the console back to what
    // was true seconds ago: a countdown climbing again, and a `last_message_at` moving
    // back behind the value the panes have already filed as handled — which makes a
    // thread read they finished look owed all over again.
    const slow = deferred<ConsoleListing>();
    const quick = deferred<ConsoleListing>();
    const fetchListing = vi
      .spyOn(consoleApi, "fetchConsoleListing")
      .mockReturnValueOnce(slow.promise)
      .mockReturnValueOnce(quick.promise)
      .mockResolvedValue(listing());

    const { result } = renderHook(() => useConsolePoll());
    await vi.waitFor(() => expect(fetchListing).toHaveBeenCalledTimes(1));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(fetchListing).toHaveBeenCalledTimes(2);

    await act(async () => {
      quick.resolve(
        listing({
          attention_total: 1,
          conversations: [
            conversation({
              chat_id: "a",
              last_message_at: "2026-01-01T10:00:05",
              pause_seconds_remaining: 20,
            }),
          ],
        }),
      );
    });
    expect(result.current.attentionTotal).toBe(1);
    const tickAfterQuick = result.current.tick;

    await act(async () => {
      slow.resolve(
        listing({
          attention_total: 4,
          conversations: [
            conversation({
              chat_id: "a",
              last_message_at: "2026-01-01T10:00:01",
              pause_seconds_remaining: 60,
            }),
          ],
        }),
      );
    });

    expect(result.current.attentionTotal).toBe(1);
    expect(result.current.conversations[0].last_message_at).toBe(
      "2026-01-01T10:00:05",
    );
    expect(result.current.conversations[0].pause_seconds_remaining).toBe(20);
    // An answer that was dropped corrected nothing, so it must not wake the panes into
    // re-reading a thread against a console they were never shown.
    expect(result.current.tick).toBe(tickAfterQuick);
  });

  it("goes on polling while a read hangs, and ignores it if it ever answers", async () => {
    // Ordering answers must not be paid for with the poll's own liveness: a read that
    // never settles cannot be allowed to hold up the ticks after it, or the pane sits on
    // stale state indefinitely with nothing left to correct it — the exact failure
    // polling was chosen over a pushed channel to avoid.
    const hung = deferred<ConsoleListing>();
    const fetchListing = vi
      .spyOn(consoleApi, "fetchConsoleListing")
      .mockReturnValueOnce(hung.promise)
      .mockResolvedValue(listing({ attention_total: 3 }));

    const { result } = renderHook(() => useConsolePoll());
    await vi.waitFor(() => expect(fetchListing).toHaveBeenCalledTimes(1));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    await vi.waitFor(() => expect(result.current.attentionTotal).toBe(3));
    expect(result.current.tick).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    await vi.waitFor(() => expect(result.current.tick).toBe(2));
    expect(fetchListing).toHaveBeenCalledTimes(3);

    await act(async () => {
      hung.resolve(listing({ attention_total: 9 }));
    });
    expect(result.current.attentionTotal).toBe(3);
    expect(result.current.tick).toBe(2);
  });

  it("carries the conversations through unchanged", async () => {
    vi.spyOn(consoleApi, "fetchConsoleListing").mockResolvedValue(
      listing({
        attention_total: 1,
        conversations: [conversation({ chat_id: "a", emphasized: true })],
      }),
    );

    const { result } = renderHook(() => useConsolePoll());

    await vi.waitFor(() =>
      expect(result.current.conversations.map((c) => c.chat_id)).toEqual(["a"]),
    );
  });

  it("stops polling once nothing is reading it", async () => {
    const fetchListing = vi
      .spyOn(consoleApi, "fetchConsoleListing")
      .mockResolvedValue(listing());

    const { unmount } = renderHook(() => useConsolePoll());
    await vi.waitFor(() => expect(fetchListing).toHaveBeenCalledTimes(1));

    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });

    expect(fetchListing).toHaveBeenCalledTimes(1);
  });
});
