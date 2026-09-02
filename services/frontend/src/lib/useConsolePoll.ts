import { useEffect, useState } from "react";
import { fetchConsoleListing, type ConsoleConversation } from "./consoleApi";

/** How often the one console endpoint is read, in milliseconds. */
export const POLL_INTERVAL_MS = 2000;

export interface ConsolePoll {
  conversations: ConsoleConversation[];
  attentionTotal: number;
}

/**
 * Keep both panes in step by polling the one console endpoint.
 *
 * Polling rather than a pushed channel is a correctness choice, not a shortcut. Every
 * mark and every silence is stored state, so a poll reads the truth and self-heals: a
 * dropped answer costs one interval of staleness. A dropped push would leave a pane
 * wrong indefinitely with nothing to correct it — and being wrong about which
 * conversation needs a person is the failure this whole surface exists to prevent.
 *
 * A failed tick therefore keeps the last good answer and raises nothing at the user:
 * the next tick corrects it, and an error banner for a blip they cannot act on would
 * only teach them to ignore the one that matters.
 */
export function useConsolePoll(intervalMs: number = POLL_INTERVAL_MS): ConsolePoll {
  const [poll, setPoll] = useState<ConsolePoll>({
    conversations: [],
    attentionTotal: 0,
  });

  useEffect(() => {
    let current = true;

    const read = async (): Promise<void> => {
      try {
        const listing = await fetchConsoleListing();
        if (current) {
          setPoll({
            conversations: listing.conversations,
            attentionTotal: listing.attention_total,
          });
        }
      } catch {
        // Deliberately swallowed: see the docstring. The state already on screen stays
        // exactly as it was until a later tick replaces it.
      }
    };

    // Read once immediately, so the first paint is the session's actual state rather
    // than an empty list that fills in a couple of seconds later.
    void read();
    const timer = setInterval(() => void read(), intervalMs);
    return () => {
      current = false;
      clearInterval(timer);
    };
  }, [intervalMs]);

  return poll;
}
