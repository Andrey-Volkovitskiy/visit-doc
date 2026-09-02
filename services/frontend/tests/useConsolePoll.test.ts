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
