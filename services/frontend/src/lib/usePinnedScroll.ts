import { useLayoutEffect, useRef, type RefObject } from "react";
import { PINNED_THRESHOLD, isPinnedToBottom } from "./scroll";

/** What a thread pane wires into its scroll container. */
export interface PinnedScroll {
  /** The scroll container. */
  ref: RefObject<HTMLDivElement | null>;
  /** The container's `onScroll`: records whether the reader is at the bottom. */
  onScroll: () => void;
  /**
   * Follow the next content to the bottom, wherever the reader had scrolled to.
   *
   * For the two moments new content is not unbidden: the reader just sent something,
   * or just opened a different conversation, which lands at its most recent message
   * (FR-015) exactly as the first one opened did.
   */
  pin: () => void;
}

/**
 * Follow new content to the bottom of a thread, but only for a reader already there
 * (FR-015a) - one rule for the patient thread and the staff thread (FR-015b), so the
 * two cannot drift into answering it differently.
 *
 * Whether the reader was at the bottom is recorded as they scroll, into a ref rather
 * than state: it is an answer about the view *before* the next content arrives, a
 * re-render is the very thing that would change it, and a reader scrolls without any
 * React event firing. It starts pinned, so a thread opened for the first time lands at
 * its most recent message.
 *
 * The follow runs in a layout effect after every render, so the browser never paints
 * new content at the old position first, and it assigns `scrollTop` because
 * `scrollIntoView` is `undefined` in this repository's jsdom.
 */
export function usePinnedScroll(): PinnedScroll {
  const ref = useRef<HTMLDivElement | null>(null);
  const wasPinnedRef = useRef(true);

  useLayoutEffect(() => {
    const el = ref.current;
    if (el === null) return;
    if (!wasPinnedRef.current) return;
    el.scrollTop = el.scrollHeight - el.clientHeight;
  });

  return {
    ref,
    onScroll: () => {
      const el = ref.current;
      if (el === null) return;
      wasPinnedRef.current = isPinnedToBottom(
        el.scrollTop,
        el.scrollHeight,
        el.clientHeight,
        PINNED_THRESHOLD,
      );
    },
    pin: () => {
      wasPinnedRef.current = true;
    },
  };
}
