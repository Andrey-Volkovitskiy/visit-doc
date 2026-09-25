import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  MAX_TRAIL_MS,
  TYPING_CPS,
  createTypingPacer,
  revealRate,
} from "../src/lib/typing";

// `tests/setup.ts` runs every component test at an unbounded pace; this file tests the
// pace itself, so it needs the real module.
vi.unmock("../src/lib/typing");

describe("revealRate: a steady pace, raised only to keep up", () => {
  it("types at the base rate when little is waiting", () => {
    expect(revealRate(10, 0)).toBe(TYPING_CPS);
  });

  it("rises so that a long backlog is shown within the trail limit", () => {
    // 900 characters in 5s is 180 a second, three times the base rate.
    expect(revealRate(900, 0)).toBe((900 * 1000) / MAX_TRAIL_MS);
  });

  it("never drops below the rate already in use", () => {
    // A reply that sped up to clear a burst does not slow down again partway through:
    // a slowdown mid-sentence reads as a stall.
    expect(revealRate(1, 150)).toBe(150);
  });

  it("takes its base rate and trail as numbers, so each has one home", () => {
    expect(revealRate(10, 0, 20)).toBe(20);
    expect(revealRate(100, 0, 20, 1000)).toBe(100);
  });
});

describe("createTypingPacer: reading and showing, kept apart", () => {
  let clock = 0;

  beforeEach(() => {
    clock = 0;
    vi.useFakeTimers();
    vi.spyOn(performance, "now").mockImplementation(() => clock);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  /** Advance both clocks together: the pacer reads one and is woken by the other. */
  function advance(ms: number): void {
    for (let left = ms; left > 0; left -= 1) {
      clock += 1;
      vi.advanceTimersByTime(1);
    }
  }

  it("types a reply that arrived in one chunk, rather than showing it whole", () => {
    // The shape that motivated the steady rate: a booking reply, a hand-off or the
    // abstention sentence arrives as one token, and replaying arrival showed it at once.
    const shown: string[] = [];
    const pacer = createTypingPacer((text) => shown.push(text), { cps: 30 });

    pacer.arrived("a".repeat(30));

    // At most one step's worth straight away (33ms at 30/s is under one character),
    // then steadily on.
    expect((shown[shown.length - 1] ?? "").length).toBeLessThanOrEqual(1);
    advance(500);
    expect(shown[shown.length - 1]!.length).toBeGreaterThanOrEqual(15);
    expect(shown[shown.length - 1]!.length).toBeLessThan(30);
    advance(600);
    expect(shown[shown.length - 1]).toHaveLength(30);
  });

  it("grows one small step at a time, never in a jump", () => {
    const shown: string[] = [];
    const pacer = createTypingPacer((text) => shown.push(text), { cps: 60 });

    pacer.arrived("x".repeat(120));
    advance(3000);

    const lengths = shown.map((text) => text.length);
    const jumps = lengths.slice(1).map((length, i) => length - lengths[i]!);
    // 60/s over ~33ms steps is two characters a step; never more than a step's worth.
    expect(Math.max(...jumps)).toBeLessThanOrEqual(3);
    expect(lengths[lengths.length - 1]).toBe(120);
  });

  it("keeps the same pace when the rest of a reply lands in a burst", () => {
    // A model stream: a few spaced tokens, then everything else at once. The burst is
    // typed at the rate the reply started at, not dropped in.
    const shown: string[] = [];
    const pacer = createTypingPacer((text) => shown.push(text), { cps: 60 });

    pacer.arrived("ab");
    advance(200);
    pacer.arrived("ab" + "c".repeat(58));
    const atBurst = shown[shown.length - 1]!.length;

    advance(100);
    expect(shown[shown.length - 1]!.length - atBurst).toBeLessThanOrEqual(8);
    advance(1000);
    expect(shown[shown.length - 1]).toHaveLength(60);
  });

  it("clears a long backlog within the trail limit", () => {
    const shown: string[] = [];
    const pacer = createTypingPacer((text) => shown.push(text), {
      cps: 10,
      maxTrailMs: 1000,
    });

    pacer.arrived("z".repeat(200));
    advance(1100);

    expect(shown[shown.length - 1]).toHaveLength(200);
  });

  it("does not bank time spent waiting for the next token", () => {
    // Caught up, then a long pause, then more text: the new text is typed from the
    // moment it arrives, not shown at once on the strength of the idle wait.
    const shown: string[] = [];
    const pacer = createTypingPacer((text) => shown.push(text), { cps: 60 });

    pacer.arrived("ab");
    advance(100);
    expect(shown[shown.length - 1]).toBe("ab");
    advance(5000);
    pacer.arrived("ab" + "c".repeat(40));

    expect(shown[shown.length - 1]!.length).toBeLessThanOrEqual(4);
  });

  it("settles once the reveal has caught up, and not before", async () => {
    const shown: string[] = [];
    const pacer = createTypingPacer((text) => shown.push(text), { cps: 60 });
    pacer.arrived("a".repeat(30));

    let settled = false;
    const waiting = pacer.settled().then(() => {
      settled = true;
    });

    await vi.advanceTimersByTimeAsync(0);
    expect(settled).toBe(false);

    advance(1000);
    await waiting;
    expect(shown[shown.length - 1]).toHaveLength(30);
  });

  it("settles immediately when everything is already shown", async () => {
    const pacer = createTypingPacer(() => undefined);
    pacer.arrived("");

    await expect(pacer.settled()).resolves.toBeUndefined();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("stops revealing, and releases whoever was waiting, when the turn is abandoned", async () => {
    // A cancelled turn removes its bubble; a pacer still running would paint text into
    // it, and a `settled()` that never resolved would strand the loop awaiting it.
    const shown: string[] = [];
    const pacer = createTypingPacer((text) => shown.push(text));
    pacer.arrived("a".repeat(100));
    const waiting = pacer.settled();

    pacer.stop();

    await expect(waiting).resolves.toBeUndefined();
    expect(vi.getTimerCount()).toBe(0);
    const last = shown.length;
    advance(1000);
    expect(shown).toHaveLength(last);
  });

  it("ignores text arriving after it was stopped", () => {
    const shown: string[] = [];
    const pacer = createTypingPacer((text) => shown.push(text));

    pacer.stop();
    pacer.arrived("Hello");

    expect(shown).toEqual([]);
  });
});
