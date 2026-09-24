import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { charsRevealedBy, createTypingPacer } from "../src/lib/typing";

describe("charsRevealedBy: the arrival timeline, replayed slower", () => {
  const arrivals = [
    { at: 1000, chars: 5 },
    { at: 1100, chars: 10 },
    { at: 1300, chars: 20 },
  ];

  it("shows the first arrival whole, at the moment it arrives", () => {
    // Nothing is held back that is already on the wire when the reveal starts: the
    // first chunk is what the reveal has to replay *from*.
    expect(charsRevealedBy(arrivals, 1000)).toBe(5);
    expect(charsRevealedBy(arrivals, 900)).toBe(5);
  });

  it("reaches each arrival at twice its distance from the first", () => {
    // 1100 arrived 100ms in, so it is shown 200ms in; 1300 arrived 300ms in, 600ms.
    expect(charsRevealedBy(arrivals, 1200)).toBe(10);
    expect(charsRevealedBy(arrivals, 1600)).toBe(20);
  });

  it("interpolates between two arrivals rather than jumping at each one", () => {
    // Halfway (in replay time) between the 100ms and 300ms arrivals: half the
    // characters between 10 and 20. The model produced them *over* that interval, so
    // spreading them across it is both truer and steadier to read than landing the
    // whole chunk at its end.
    expect(charsRevealedBy(arrivals, 1400)).toBe(15);
  });

  it("never runs past the last arrival, however long it waits", () => {
    expect(charsRevealedBy(arrivals, 99999)).toBe(20);
  });

  it("shows everything at once when everything arrived at once", () => {
    // A reply short enough to come in one chunk — and every test's synchronous stream.
    // Replay time cannot be behind a timeline with no duration, so there is nothing to
    // pace and the pacer never schedules a thing.
    const together = [
      { at: 500, chars: 4 },
      { at: 500, chars: 9 },
    ];

    expect(charsRevealedBy(together, 500)).toBe(9);
  });

  it("shows nothing before anything has arrived", () => {
    expect(charsRevealedBy([], 1000)).toBe(0);
  });

  it("takes its slowdown as a number, so the factor has one home", () => {
    // Proof that the 2 is the caller's and not baked into the arithmetic: at the same
    // instant, a reveal running at arrival speed is further along than a slower one.
    expect(charsRevealedBy(arrivals, 1200, 1)).toBe(15);
    expect(charsRevealedBy(arrivals, 1200)).toBe(10);
    expect(charsRevealedBy(arrivals, 1200, 4)).toBe(7);
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
    clock += ms;
    vi.advanceTimersByTime(ms);
  }

  it("reveals a burst that arrived all at once without waiting", () => {
    const shown: string[] = [];
    const pacer = createTypingPacer((text) => shown.push(text));

    pacer.arrived("Hel");
    pacer.arrived("Hello");

    expect(shown[shown.length - 1]).toBe("Hello");
    expect(vi.getTimerCount()).toBe(0);
  });

  it("holds back text that arrived faster than it reads", () => {
    const shown: string[] = [];
    const pacer = createTypingPacer((text) => shown.push(text));

    pacer.arrived("aaaa");
    advance(100);
    pacer.arrived("aaaabbbb");

    // 100ms of arrival is 50ms of reveal: half of the second chunk is still to come.
    expect(shown[shown.length - 1]).toBe("aaaabb");
    advance(100);
    expect(shown[shown.length - 1]).toBe("aaaabbbb");
  });

  it("settles once the reveal has caught up, and not before", async () => {
    const shown: string[] = [];
    const pacer = createTypingPacer((text) => shown.push(text));
    pacer.arrived("aaaa");
    advance(100);
    pacer.arrived("aaaabbbb");

    let settled = false;
    const waiting = pacer.settled().then(() => {
      settled = true;
    });

    await vi.advanceTimersByTimeAsync(0);
    expect(settled).toBe(false);

    clock += 100;
    await vi.advanceTimersByTimeAsync(100);
    await waiting;
    expect(shown[shown.length - 1]).toBe("aaaabbbb");
  });

  it("settles immediately when everything is already shown", async () => {
    const pacer = createTypingPacer(() => undefined);
    pacer.arrived("Hello");

    await expect(pacer.settled()).resolves.toBeUndefined();
  });

  it("stops revealing, and releases whoever was waiting, when the turn is abandoned", async () => {
    // A cancelled turn removes its bubble; a pacer still running would paint text into
    // it, and a `settled()` that never resolved would strand the loop awaiting it.
    const shown: string[] = [];
    const pacer = createTypingPacer((text) => shown.push(text));
    pacer.arrived("aaaa");
    advance(100);
    pacer.arrived("aaaabbbb");
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
