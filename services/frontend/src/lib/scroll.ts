/**
 * Whether a scroll container is at its bottom, within a tolerance.
 *
 * Pure on purpose, and taking four numbers rather than an element (`research.md`
 * Decision 4): jsdom reports `scrollHeight` and `clientHeight` as 0 for every element,
 * so a version of this rule that read them off a node would answer `true`
 * unconditionally under test. The hold-position branch would then never execute while
 * the test covering it passed — the vacuous pass the spec's Assumptions section
 * requires the plan to prevent. Here every case is a real one, and the two component
 * tests that stub the measurements prove the predicate is actually consulted.
 *
 * `threshold` is not a fudge factor. A container scrolled fully to its end can sit a
 * fraction of a pixel short of `scrollHeight - clientHeight` at fractional zoom levels,
 * and a reader who is at the bottom means it in whole pixels. A container with nothing
 * to scroll answers `true`: there is only one place to be, and answering `false` would
 * withhold the follow from every short thread.
 */
export function isPinnedToBottom(
  scrollTop: number,
  scrollHeight: number,
  clientHeight: number,
  threshold: number,
): boolean {
  return scrollHeight - clientHeight - scrollTop <= threshold;
}

/**
 * How far from the bottom still counts as being at it, in pixels.
 *
 * One declaration so the patient thread and the staff thread cannot drift into
 * answering FR-015a differently (FR-015b puts them on the same terms).
 */
export const PINNED_THRESHOLD = 24;
