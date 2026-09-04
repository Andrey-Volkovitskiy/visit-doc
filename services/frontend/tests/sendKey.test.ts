import { describe, expect, it } from "vitest";
import type { KeyboardEvent } from "react";
import { isSendKey } from "../src/lib/sendKey";

interface KeyPress {
  key?: string;
  ctrlKey?: boolean;
  metaKey?: boolean;
  shiftKey?: boolean;
  isComposing?: boolean;
}

/** The parts of a React keydown event this decision actually reads. */
function press({
  key = "Enter",
  ctrlKey = false,
  metaKey = false,
  shiftKey = false,
  isComposing = false,
}: KeyPress = {}): KeyboardEvent<HTMLTextAreaElement> {
  return {
    key,
    ctrlKey,
    metaKey,
    shiftKey,
    nativeEvent: { isComposing },
  } as KeyboardEvent<HTMLTextAreaElement>;
}

describe("isSendKey", () => {
  it("sends on a bare Enter", () => {
    expect(isSendKey(press())).toBe(true);
  });

  it("sends on Ctrl+Enter and on Cmd+Enter", () => {
    expect(isSendKey(press({ ctrlKey: true }))).toBe(true);
    expect(isSendKey(press({ metaKey: true }))).toBe(true);
  });

  it("does not send on Shift+Enter, leaving the newline to the textarea", () => {
    expect(isSendKey(press({ shiftKey: true }))).toBe(false);
  });

  it("still sends when Shift is held alongside Ctrl or Cmd", () => {
    // Shift is what suppresses a *bare* Enter. Holding it while reaching for the
    // modifier chord should not turn a deliberate send into a newline.
    expect(isSendKey(press({ ctrlKey: true, shiftKey: true }))).toBe(true);
    expect(isSendKey(press({ metaKey: true, shiftKey: true }))).toBe(true);
  });

  it("does not send when the Enter is closing an IME candidate window", () => {
    expect(isSendKey(press({ isComposing: true }))).toBe(false);
    expect(isSendKey(press({ ctrlKey: true, isComposing: true }))).toBe(false);
  });

  it("ignores every other key", () => {
    expect(isSendKey(press({ key: "a" }))).toBe(false);
    expect(isSendKey(press({ key: "Escape" }))).toBe(false);
  });
});
