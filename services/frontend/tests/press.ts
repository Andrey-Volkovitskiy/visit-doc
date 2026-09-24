import { fireEvent } from "@testing-library/react";

/**
 * Press a control the way a pointer does: every event a real press produces, in order.
 *
 * `fireEvent.click` alone is not enough for the vendored Radix primitives and fails in a
 * way that looks like a missing element rather than a missing event — a tab does not
 * switch and a menu does not open, because Radix opens on `pointerdown`/`mousedown` and
 * never sees a lone synthetic `click`. Measured in this repo (T000, `research.md`
 * Decision 7): this sequence drives the tab set, the dropdown menu, the dialog and the
 * switch, and still activates a plain `<button>` exactly once — so a test never has to
 * know which kind of control it is holding.
 *
 * `fireEvent.click` is left alone everywhere it already works; this is for controls that
 * need it, not a migration.
 */
export function press(el: HTMLElement): void {
  fireEvent.pointerDown(el, { button: 0, ctrlKey: false });
  fireEvent.mouseDown(el, { button: 0, ctrlKey: false });
  fireEvent.pointerUp(el, { button: 0, ctrlKey: false });
  fireEvent.mouseUp(el, { button: 0, ctrlKey: false });
  fireEvent.click(el, { button: 0, ctrlKey: false });
}
