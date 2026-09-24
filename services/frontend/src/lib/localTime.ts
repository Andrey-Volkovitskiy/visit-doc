/**
 * How the console writes a wall-clock time, in one place.
 *
 * Every time this system handles is a naive local `YYYY-MM-DDTHH:MM:SS` string with no
 * timezone anywhere, so nothing here converts: a date is read from its own parts and a
 * time is sliced off as written. Shared so the practitioner's week and the booking acts
 * on a thread name a moment the same way, rather than each keeping its own format.
 */

const WEEKDAY_AND_DATE = new Intl.DateTimeFormat("en-GB", {
  weekday: "long",
  day: "numeric",
  month: "long",
});

/**
 * "Thursday 24 September" for a `YYYY-MM-DD` date.
 *
 * Built from the date's own parts as a *local* date, never `new Date("2026-09-24")`,
 * which parses as UTC midnight and names the previous day anywhere west of Greenwich.
 */
export function dayLabel(date: string): string {
  const [year, month, day] = date.split("-").map(Number);
  return WEEKDAY_AND_DATE.format(new Date(year, month - 1, day));
}

/**
 * `HH:MM` off a naive `YYYY-MM-DDTHH:MM:SS` time, read as written.
 *
 * Sliced rather than parsed: there is no timezone anywhere in this system, so there is
 * nothing to convert.
 */
export function clockTime(localDateTime: string): string {
  return localDateTime.slice(11, 16);
}

/**
 * "Tuesday 12 January 2027 at 10:00" for a naive local date-time.
 *
 * Carries the year where `dayLabel` does not: this names the moment on a booking act,
 * which is a permanent record read long after the fact, and the booking horizon crosses
 * years. The week view is a seven-day window read beside its own dates, so it needs none.
 * The year is appended rather than asked of `Intl`, whose en-GB form with a year inserts a
 * comma after the weekday ("Tuesday, 12 January 2027").
 */
export function dayAndTime(localDateTime: string): string {
  const date = localDateTime.slice(0, 10);
  return `${dayLabel(date)} ${date.slice(0, 4)} at ${clockTime(localDateTime)}`;
}
