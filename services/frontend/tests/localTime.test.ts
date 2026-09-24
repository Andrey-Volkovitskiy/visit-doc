import { describe, expect, it } from "vitest";
import { clockTime, dayLabel, dayAndTime } from "../src/lib/localTime";

describe("dayLabel", () => {
  it("names a date by weekday, day and month", () => {
    expect(dayLabel("2026-09-24")).toBe("Thursday 24 September");
  });

  it("reads the date as local, never as UTC midnight", () => {
    // `new Date("2027-01-12")` is UTC midnight, which names the 11th anywhere west of
    // Greenwich. The label must be the date as written, whatever the runner's zone.
    expect(dayLabel("2027-01-12")).toBe("Tuesday 12 January");
  });
});

describe("clockTime", () => {
  it("takes HH:MM off a naive local time, as written", () => {
    expect(clockTime("2027-01-12T09:05:00")).toBe("09:05");
  });
});

describe("dayAndTime", () => {
  it("names the day with its year, then the clock time", () => {
    // An act is a permanent record, and the 90-day booking horizon crosses years, so a
    // date on one must say which year it means (016 T048). The week view's `dayLabel`
    // does not: a seven-day window is read beside its own dates.
    expect(dayAndTime("2027-01-12T10:00:00")).toBe("Tuesday 12 January 2027 at 10:00");
  });

  it("tells apart two moments either side of a new year", () => {
    expect(dayAndTime("2026-12-29T09:00:00")).toBe("Tuesday 29 December 2026 at 09:00");
    expect(dayAndTime("2027-12-28T09:00:00")).toBe("Tuesday 28 December 2027 at 09:00");
  });
});
