import { useRef, useState } from "react";

export interface BusyLatch {
  /** Whether a gesture for `key` is in flight — for painting its control disabled. */
  isBusy: (key: string) => boolean;
  /**
   * Run `work` for `key`, unless one for the same key is already out.
   *
   * Returns whether it ran. `work` owns its own errors: a rejection releases the latch
   * and propagates, so a caller that wants a banner still writes its own `catch`.
   */
  run: (key: string, work: () => Promise<void>) => Promise<boolean>;
}

/**
 * Stop a second click doing a thing twice, keyed by what the thing is about.
 *
 * Every write surface here needs this and each was hand-rolling it, which is why the
 * latch was on `handleCreate` in both admin panes and on neither `Save` nor `Delete`:
 * a double-clicked FAQ Save ran two full revision writes — chunked, embedded and
 * indexed twice — and the second publish then failed its own staleness guard over an
 * entry that had in fact saved.
 *
 * **Keyed, not global.** A latch shared across a whole pane belongs to whichever
 * gesture used it last, so saving one row disabled every other row's button and a post
 * to one conversation gated the next. The key says what a gesture is about — a chat
 * id, `save:<id>`, or a plain `create` where there is only one — and two gestures about
 * different things never see each other's latch.
 *
 * **A ref and state, and the two are not alternatives.** Two clicks dispatched in one
 * React batch both run against the render that preceded them: the state the first set
 * has not committed, so a guard reading it lets the second through, and the button is
 * still painted enabled for the same reason. The ref is what refuses the second call;
 * the state is what repaints the button, which a ref cannot do.
 */
export function useBusyLatch(): BusyLatch {
  const busyRef = useRef<Set<string>>(new Set());
  const [busy, setBusy] = useState<readonly string[]>([]);

  function publish(): void {
    setBusy([...busyRef.current]);
  }

  async function run(key: string, work: () => Promise<void>): Promise<boolean> {
    if (busyRef.current.has(key)) return false;
    busyRef.current.add(key);
    publish();
    try {
      await work();
      return true;
    } finally {
      // Released however this ended, the failure included: whatever was typed is still
      // on screen by design, and a latch left closed would leave a staff member holding
      // a change they can no longer make.
      busyRef.current.delete(key);
      publish();
    }
  }

  return { isBusy: (key) => busy.includes(key), run };
}
