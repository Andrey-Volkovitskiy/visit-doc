import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

// Every component test runs the reply reveal at an effectively unbounded rate, so a
// streamed reply is on screen the moment it arrives. The reveal's pace is real time
// (tens of characters a second), and a component test asserting on streamed text would
// otherwise be racing it against `waitFor`'s one-second limit — passing for a short
// reply, failing for a longer one, and never testing the component at all. The pace
// itself is tested in `typing.test.ts`, which unmocks this module.
vi.mock("../src/lib/typing", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/lib/typing")>();
  return {
    ...actual,
    createTypingPacer: (reveal: (text: string) => void) =>
      actual.createTypingPacer(reveal, { cps: Number.MAX_SAFE_INTEGER }),
  };
});
