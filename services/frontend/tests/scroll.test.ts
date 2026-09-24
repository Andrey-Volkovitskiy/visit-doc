import { describe, expect, it } from "vitest";

import { isPinnedToBottom } from "../src/lib/scroll";

/**
 * The rule FR-015a rests on, exercised where it can actually be exercised.
 *
 * Under jsdom `scrollHeight` and `clientHeight` are always 0, so the condition inside a
 * component reads `0 - 0 <= threshold` → always true: the follow branch would be taken
 * in every component test while the hold-position branch never ran, and its test would
 * pass without ever executing it (`research.md` Decision 4). A pure predicate has no
 * such floor — every case below is a real one.
 */
describe("isPinnedToBottom", () => {
  it("is true exactly at the bottom", () => {
    expect(isPinnedToBottom(600, 1000, 400, 24)).toBe(true);
  });

  it("is true one pixel short of the bottom", () => {
    expect(isPinnedToBottom(599, 1000, 400, 24)).toBe(true);
  });

  it("is true at the far edge of the threshold", () => {
    // distance from the bottom is exactly 24, which the threshold admits
    expect(isPinnedToBottom(576, 1000, 400, 24)).toBe(true);
  });

  it("is false one pixel past the threshold", () => {
    expect(isPinnedToBottom(575, 1000, 400, 24)).toBe(false);
  });

  it("is false far above the bottom", () => {
    expect(isPinnedToBottom(0, 1000, 400, 24)).toBe(false);
  });

  it("is true for a container with no overflow at all", () => {
    // Nothing to scroll: the reader is at the bottom because there is only one place to
    // be. Answering `false` here would withhold the follow from every short thread.
    expect(isPinnedToBottom(0, 400, 400, 24)).toBe(true);
  });

  it("is true for a zero-height container", () => {
    // What jsdom reports for every element. Named here so that the reason a component
    // test always takes the follow branch is written down rather than discovered.
    expect(isPinnedToBottom(0, 0, 0, 24)).toBe(true);
  });

  it("is true when scrolled past the bottom, as elastic scrolling can report", () => {
    expect(isPinnedToBottom(650, 1000, 400, 24)).toBe(true);
  });

  it("admits only the exact bottom at a zero threshold", () => {
    expect(isPinnedToBottom(600, 1000, 400, 0)).toBe(true);
    expect(isPinnedToBottom(599, 1000, 400, 0)).toBe(false);
  });

  it("tolerates the fractional shortfall a zoomed container reports", () => {
    // A container scrolled fully to its end can sit a fraction of a pixel short at
    // fractional zoom levels. That is what the threshold is for, and it is why the
    // comparison is not an equality.
    expect(isPinnedToBottom(599.6, 1000, 400, 24)).toBe(true);
  });
});
