/**
 * How a streamed reply is *shown*, as distinct from how fast it arrives.
 *
 * The server streams tokens as the model produces them, and the pane used to render
 * each one the moment it was read — so the reply appeared at exactly the model's own
 * pace, which is faster than it is comfortable to read.
 *
 * Arrival is not a shape worth reproducing either. Several paths send their whole reply
 * as one chunk — a booking reply, a hand-off, the abstention sentence, an answer to
 * several questions — and a model's own stream comes in bursts: a few spaced tokens,
 * then the rest in a rush. A reveal that replayed the arrival timeline (the previous
 * rule here) typed the first words and then dropped the remainder in at once, which is
 * exactly the moment a reader notices. So the reveal runs at a steady rate of its own.
 */

/** The reveal's pace when nothing is pushing it: characters per second. */
export const TYPING_CPS = 60;

/**
 * The longest the reveal may trail what has arrived, in milliseconds.
 *
 * A steady rate alone makes a long reply take as long as its length says — a
 * 900-character booking summary would type for fifteen seconds. When a backlog would
 * take longer than this to show at the current rate, the rate rises to clear it in
 * this time instead: still one character after another, only faster.
 */
export const MAX_TRAIL_MS = 5000;

/** How often the reveal advances while it is behind: ~30 times a second. */
const STEP_MS = 33;

/**
 * The rate to reveal at, given how much is waiting and the rate already in use.
 *
 * Never lower than the rate already in use, so a reply only ever types at one speed or
 * speeds up — slowing down partway through reads as a stall. Never lower than
 * `baseCps`, and high enough to show `backlog` characters within `maxTrailMs`.
 */
export function revealRate(
  backlog: number,
  current: number,
  baseCps: number = TYPING_CPS,
  maxTrailMs: number = MAX_TRAIL_MS,
): number {
  return Math.max(current, baseCps, (backlog * 1000) / maxTrailMs);
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

/** The pace a pacer reveals at. Both default to this module's constants. */
export interface TypingPace {
  cps?: number;
  maxTrailMs?: number;
}

/**
 * Drive one turn's reveal, calling `reveal` with the text that may be shown so far.
 *
 * Reading and showing are deliberately separate: the caller keeps consuming the stream
 * at full speed and hands the text over, while this decides how much of it is on screen.
 * Tying the two together would apply backpressure to the network read.
 *
 * The position advances by elapsed time × rate, measured from the clock at each step
 * rather than counted in steps, so a throttled timer (a background tab) delays the text
 * without slowing it down.
 */
export function createTypingPacer(
  reveal: (text: string) => void,
  pace: TypingPace = {},
): TypingPacer {
  const baseCps = pace.cps ?? TYPING_CPS;
  const maxTrailMs = pace.maxTrailMs ?? MAX_TRAIL_MS;
  let full = "";
  // Fractional, so a step that earns less than a whole character is not thrown away.
  let position = 0;
  let shown = 0;
  let rate = 0;
  let lastStep: number | null = null;
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

  function step(): void {
    if (stopped) return;
    const at = now();
    // A reveal starting from rest is given one step's worth straight away, so text
    // begins to appear the moment it arrives rather than one step later.
    const since = lastStep === null ? STEP_MS : at - lastStep;
    position = Math.min(position + (rate * since) / 1000, full.length);
    lastStep = at;
    const allowed = Math.floor(position);
    if (allowed > shown) {
      shown = allowed;
      reveal(full.slice(0, shown));
    }
    if (shown >= full.length) {
      // Caught up: the clock stops, so time spent waiting for the next token is not
      // banked and spent as a jump when it lands.
      lastStep = null;
      finish();
    } else if (timer === null) {
      timer = setTimeout(() => {
        timer = null;
        step();
      }, STEP_MS);
    }
  }

  return {
    arrived(next: string): void {
      if (stopped) return;
      full = next;
      rate = revealRate(full.length - shown, rate, baseCps, maxTrailMs);
      step();
    },
    settled(): Promise<void> {
      if (stopped || shown >= full.length) return Promise.resolve();
      return new Promise<void>((resolve) => {
        settle = resolve;
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
 * backwards mid-reply would move the reveal backwards and freeze the text on screen.
 */
function now(): number {
  return performance.now();
}
