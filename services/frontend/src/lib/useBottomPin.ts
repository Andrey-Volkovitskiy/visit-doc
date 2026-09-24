import { useLayoutEffect, useRef, type RefObject } from "react";
import { PINNED_THRESHOLD, isPinnedToBottom } from "./scroll";

/** A scroll container that follows new content only for a reader already at its end. */
export interface BottomPin<T extends HTMLElement> {
  /** Attach to the scroll container. */
  ref: RefObject<T | null>;
  /** Attach to the container's `onScroll`. */
  onScroll: () => void;
  /**
   * Declare that the next content belongs at the bottom whatever the reader scrolled to.
   *
   * Two things say this, and they are not the same thing said twice. The reader *acted*
   * — sent a message, posted a reply — so their own words follow them down; and the
   * conversation *changed*, so there is no position to hold, only a new thread that has
   * to open at its most recent message (FR-015).
   */
  pin: () => void;
}

/**
 * Follow new content to the bottom, but only for a reader who was already there
 * (FR-015a), over one scroll container.
 *
 * Lives here, once, because both threads need exactly this and a copy each is what the
 * duplication cost: the first two copies were written together and both forgot to
 * re-pin when the conversation changed, so a reader who had scrolled up in one chat
 * opened the next one at its oldest message — the same defect, twice, with no test able
 * to tell a one-sided fix from a whole one. `useThreadReads` was extracted for exactly
 * this reason and says so.
 *
 * Whether the reader is at the bottom is read off the element *as they scroll* and kept
 * in a ref, never in state: a reader scrolls without any React event firing, so derived
 * state would go stale on the one interaction this rule exists to detect, and a render
 * is the very thing that would change the answer.
 *
 * `useLayoutEffect` rather than `useEffect`: the browser must not paint the new content
 * at the old scroll position first, which is a visible jump. It carries no dependency
 * array on purpose — the container's height changes with things no list of values names,
 * an expanded evidence block among them — so it re-checks after every commit and writes
 * only when the position is actually wrong. And the scroll is performed by *assigning*
 * `scrollTop`: `scrollIntoView` is `undefined` in this repository's jsdom and calling it
 * throws in every test that renders a thread (`research.md`).
 */
export function useBottomPin<T extends HTMLElement>(): BottomPin<T> {
  const ref = useRef<T | null>(null);
  const pinnedRef = useRef(true);

  function onScroll(): void {
    const el = ref.current;
    if (el === null) return;
    pinnedRef.current = isPinnedToBottom(
      el.scrollTop,
      el.scrollHeight,
      el.clientHeight,
      PINNED_THRESHOLD,
    );
  }

  function pin(): void {
    pinnedRef.current = true;
  }

  useLayoutEffect(() => {
    const el = ref.current;
    if (el === null) return;
    if (!pinnedRef.current) return;
    const bottom = el.scrollHeight - el.clientHeight;
    // Guarded rather than assigned unconditionally: this runs after every commit, and a
    // write that changes nothing still costs the browser a scroll it has to reconcile.
    if (el.scrollTop !== bottom) el.scrollTop = bottom;
  });

  return { ref, onScroll, pin };
}
