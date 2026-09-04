import { useEffect, useState } from "react";
import { fetchConsoleListing, type ConsoleConversation } from "./consoleApi";
import { READ_TIMEOUT_MS } from "./useThreadReads";

/** How often the one console endpoint is read, in milliseconds. */
export const POLL_INTERVAL_MS = 2000;

export interface ConsolePoll {
  conversations: ConsoleConversation[];
  attentionTotal: number;
  /**
   * How many times the endpoint has answered.
   *
   * The panes that refetch a thread when its newest message advances need something that
   * changes on a tick where *nothing* changed, or a read of theirs that failed has
   * nothing left to wake it: the message it was fetching stays the newest one, so the
   * value it watches is reported unchanged from then on. Counting answers gives them
   * that, and counts answers rather than attempts so a failed tick — which corrects
   * nothing and is already invisible here — does not pass for one. An answer dropped for
   * arriving late is counted no more than a failed one, for the same reason: it changed
   * nothing here, and waking a pane to re-read a thread on the strength of a console it
   * was never shown is asking it to chase state that does not exist.
   */
  tick: number;
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
 *
 * A *late* answer is a different thing from a failed one, and is the one case where a
 * poll can do harm rather than nothing: the interval fires on the clock, not on the
 * previous read, so a slow tick can land after a quicker later one and overwrite it with
 * what was true seconds ago — a countdown jumping back up, a mark reappearing after
 * staff cleared it, and `last_message_at` rewinding to a time the panes have already
 * filed as handled, which makes a thread read they completed look owed again. So each
 * read carries the number it was issued as, and an answer is applied only if nothing
 * newer has been applied already; an older one is dropped exactly as a failed one is.
 *
 * Sequencing rather than skipping a tick while a read is outstanding: the two are not
 * the same trade. Skipping makes the poll's own liveness depend on every read
 * completing, so a read that hangs — the very case this is about — stops the poll for as
 * long as it hangs, and one that never settles stops it for good. That is precisely the
 * self-healing this whole surface is built on, spent to save requests against one
 * endpoint whose cost is already bounded by the interval. Every tick is still issued
 * here; only losers are discarded.
 */
export function useConsolePoll(intervalMs: number = POLL_INTERVAL_MS): ConsolePoll {
  const [poll, setPoll] = useState<ConsolePoll>({
    conversations: [],
    attentionTotal: 0,
    tick: 0,
  });

  useEffect(() => {
    let current = true;
    // What the reads are ordered by. `issued` numbers each read as it goes out;
    // `applied` is the number of the newest one whose answer reached the state, so a read
    // that comes back below it is describing a console older than the one on screen.
    // A read that failed leaves `applied` alone: it put nothing on screen, so an
    // in-between answer arriving after it is still a correction and not a rewind.
    let issued = 0;
    let applied = 0;

    // Every read still out, so unmounting gives its socket back rather than leaving one
    // per interval held until the browser decides otherwise.
    const inFlight = new Set<AbortController>();

    const read = async (): Promise<void> => {
      const sequence = ++issued;
      // The same deadline the thread panes give their reads, and for the same reason:
      // this is a timer-driven read, so one that never settles is not a slow tick but a
      // socket held for good, and the interval adds another every couple of seconds
      // until the browser's per-origin cap is full - at which point the panes' own reads
      // and the next POST queue behind requests that will never answer.
      const controller = new AbortController();
      inFlight.add(controller);
      const deadline = setTimeout(() => controller.abort(), READ_TIMEOUT_MS);
      try {
        const listing = await fetchConsoleListing(controller.signal);
        if (current && sequence > applied) {
          // Recorded outside the updater, which React may run more than once for a
          // single update; the ordering decision must be made exactly as often as an
          // answer arrives.
          applied = sequence;
          setPoll((previous) => ({
            conversations: listing.conversations,
            attentionTotal: listing.attention_total,
            tick: previous.tick + 1,
          }));
        }
      } catch {
        // Deliberately swallowed: see the docstring. The state already on screen stays
        // exactly as it was until a later tick replaces it. An abort - this read's own
        // deadline, or the poll being torn down - arrives here too, and is the same
        // thing to this function: a tick that corrected nothing.
      } finally {
        clearTimeout(deadline);
        inFlight.delete(controller);
      }
    };

    // Read once immediately, so the first paint is the session's actual state rather
    // than an empty list that fills in a couple of seconds later.
    void read();
    const timer = setInterval(() => void read(), intervalMs);
    return () => {
      current = false;
      clearInterval(timer);
      for (const controller of inFlight) controller.abort();
      inFlight.clear();
    };
  }, [intervalMs]);

  return poll;
}
