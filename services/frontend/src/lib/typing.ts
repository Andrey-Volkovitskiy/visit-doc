/**
 * How a streamed reply is *shown*, as distinct from how fast it arrives.
 *
 * The server streams tokens as the model produces them, and the pane used to render
 * each one the moment it was read — so the reply appeared at exactly the model's own
 * pace, which is faster than it is comfortable to read.
 */

/** How much longer than its arrival a reply takes to appear. */
export const TYPING_SLOWDOWN = 2;

/** How often the reveal catches up while it is behind: ~30 times a second. */
const STEP_MS = 33;

/** Cumulative characters arrived, and when. */
export interface Arrival {
  at: number;
  chars: number;
}

/**
 * How many characters may be shown at `now`, replaying the arrivals at `1/slowdown`.
 *
 * The rule is a replay rather than a rate: the reply appears in the shape it arrived
 * in — the pauses where the model paused, the bursts where it burst — stretched over
 * twice the time. A fixed characters-per-second would have had to guess a number, and
 * would be slower than arrival on a fast reply and faster on a slow one, which is the
 * opposite of what a reader notices.
 *
 * Deliberately **not** measured as a gap between reads. Once the reveal lags, the
 * response body buffers and the next read returns instantly, so inter-read gaps
 * collapse to zero exactly when the pacing is doing its job — a rule built on them
 * stops slowing down the moment it starts working. This one is anchored to the first
 * arrival and is unaffected by how the reading loop is scheduled.
 *
 * Between two arrivals the count is interpolated, so characters appear steadily rather
 * than one chunk at a time.
 */
export function charsRevealedBy(
  arrivals: Arrival[],
  now: number,
  slowdown: number = TYPING_SLOWDOWN,
): number {
  const first = arrivals[0];
  if (first === undefined) return 0;
  const last = arrivals[arrivals.length - 1]!;
  // The instant in the arrival timeline the reveal has reached.
  const replay = first.at + (now - first.at) / slowdown;
  if (replay >= last.at) return last.chars;
  // The latest arrival the replay has reached, which is not always the first one it
  // meets: several tokens read in the same millisecond share a timestamp, and the last
  // of them is the one that has arrived.
  let from = first;
  let index = 0;
  for (let i = 1; i < arrivals.length && arrivals[i]!.at <= replay; i++) {
    from = arrivals[i]!;
    index = i;
  }
  const to = arrivals[index + 1]!;
  const span = to.at - from.at;
  // Clamped, so a clock that somehow reads before the first arrival shows that
  // arrival rather than a negative count. Two arrivals stamped at the same
  // millisecond have no span to interpolate over.
  const through = span <= 0 ? 1 : Math.min(Math.max((replay - from.at) / span, 0), 1);
  return Math.floor(from.chars + through * (to.chars - from.chars));
}

/** Reveals a streamed reply at a reading pace, and says when it has caught up. */
export interface TypingPacer {
  /** The whole reply as it stands, each time more of it has arrived. */
  arrived: (full: string) => void;
  /** Resolves once everything that arrived has been shown. */
  settled: () => Promise<void>;
  /** Abandon the reveal — the turn was cancelled, or nobody is waiting for it. */
  stop: () => void;
}

/**
 * Drive one turn's reveal, calling `reveal` with the text that may be shown so far.
 *
 * Reading and showing are deliberately separate: the caller keeps consuming the stream
 * at full speed and hands the text over, while this decides how much of it is on screen.
 * Tying the two together would apply backpressure to the network read, which is what
 * makes a paced reveal stop pacing (see `charsRevealedBy`).
 *
 * A reveal that is already caught up costs nothing and schedules nothing: when every
 * token arrives at once — a test's synchronous stream, or a reply short enough to come
 * in one chunk — the first call reveals all of it and no timer is ever set.
 */
export function createTypingPacer(
  reveal: (text: string) => void,
  slowdown: number = TYPING_SLOWDOWN,
): TypingPacer {
  const arrivals: Arrival[] = [];
  let full = "";
  let shown = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let settle: (() => void) | null = null;
  let stopped = false;

  function finish(): void {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    settle?.();
    settle = null;
  }

  function show(): void {
    if (stopped) return;
    const allowed = Math.min(charsRevealedBy(arrivals, now(), slowdown), full.length);
    if (allowed > shown) {
      shown = allowed;
      reveal(full.slice(0, shown));
    }
    if (shown >= full.length) {
      finish();
    } else if (timer === null) {
      timer = setTimeout(() => {
        timer = null;
        show();
      }, STEP_MS);
    }
  }

  return {
    arrived(next: string): void {
      if (stopped) return;
      full = next;
      arrivals.push({ at: now(), chars: next.length });
      show();
    },
    settled(): Promise<void> {
      if (stopped || shown >= full.length) return Promise.resolve();
      return new Promise<void>((resolve) => {
        settle = resolve;
        show();
      });
    },
    stop(): void {
      stopped = true;
      finish();
    },
  };
}

/**
 * The clock, wrapped once.
 *
 * `performance.now()` is monotonic, which `Date.now()` is not — a clock stepped
 * backwards mid-reply would rewind the replay and freeze the text on screen.
 */
function now(): number {
  return performance.now();
}
