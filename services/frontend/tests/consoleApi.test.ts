import { afterEach, describe, expect, it, vi } from "vitest";
import type { PractitionerAppointment } from "../src/lib/consoleApi";
import type { AttentionMark } from "../src/lib/chatStream";
import {
  ATTENTION_MARK_LABEL,
  createFaqEntry,
  createPractitioner,
  deleteFaqEntry,
  deletePractitioner,
  fetchFaqEntries,
  fetchPractitioners,
  fetchPractitionerWeek,
  fetchSpecialties,
  PractitionerWeekError,
  updateFaqEntry,
  updatePractitioner,
} from "../src/lib/consoleApi";

// A fresh `Response` per call: a body can only be read once, and these tests make
// several calls against one refusal.
function refuse(body: BodyInit, status = 409): void {
  vi.spyOn(globalThis, "fetch").mockImplementation(() =>
    Promise.resolve(new Response(body, { status })),
  );
}

// The spy replaces the global `fetch`, so it has to go back before the next file runs.
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

// The practitioner and FAQ writes relay the server's own `detail`, so these cases drive
// the reading of that field through a real caller rather than the helper directly - it
// is module-private, and the branch that matters is the one a caller sees.
//
// The admin component tests cannot reach any of this: they fake the network at the
// `consoleApi` seam (`vi.spyOn(consoleApi, "createPractitioner")`), which replaces the
// very functions that read the body. Faking `fetch` here is what leaves the reading in
// place, matching how `chatStream.test.ts` tests the other half of the network layer.
describe("reading the server's explanation off a refusal", () => {
  it("relays a JSON body's detail as the error the caller sees", async () => {
    refuse(
      JSON.stringify({ detail: "a practitioner with that name already exists" }),
    );

    await expect(createPractitioner({ full_name: "Dr. Ada Lovelace" })).rejects.toThrow(
      "a practitioner with that name already exists",
    );
  });

  it("falls back when the JSON body carries no detail at all", async () => {
    refuse(JSON.stringify({ error: "nope" }));

    await expect(createPractitioner({})).rejects.toThrow(
      "Could not save that. Please try again.",
    );
  });

  it("falls back when detail is present but is not a string", async () => {
    // FastAPI answers a validation error with a *list* of detail objects. Handing that
    // to `new Error` renders as "[object Object]" on the screen, which tells a staff
    // member nothing about what was refused.
    refuse(
      JSON.stringify({
        detail: [{ loc: ["body", "schedule"], msg: "ranges overlap" }],
      }),
      422,
    );

    const error = (await createPractitioner({}).catch((err: unknown) => err)) as Error;

    expect(error.message).toBe("Could not save that. Please try again.");
    expect(error.message).not.toMatch(/object Object/);
  });

  it("falls back when the body is not JSON", async () => {
    // A proxy in front of the service answers with its own error page, not the
    // service's JSON, so parsing throws rather than returning something unusable.
    refuse("<html>proxy error</html>");

    await expect(createPractitioner({})).rejects.toThrow(
      "Could not save that. Please try again.",
    );
  });

  it("falls back when the body is empty", async () => {
    refuse("", 502);

    await expect(createPractitioner({})).rejects.toThrow(
      "Could not save that. Please try again.",
    );
  });
});

describe("practitioner writes", () => {
  it("relays the server's wording from every practitioner call", async () => {
    refuse(JSON.stringify({ detail: "the scheduler said no" }));

    // One wording, whichever call was refused: the rule belongs to the service that
    // owns practitioners, and each of these is a way of asking it.
    for (const call of [
      () => fetchSpecialties(),
      () => fetchPractitioners(),
      () => createPractitioner({}),
      () => updatePractitioner("01PRACT0000000000000000000", {}),
      () => deletePractitioner("01PRACT0000000000000000000"),
    ]) {
      await expect(call()).rejects.toThrow("the scheduler said no");
    }
  });

  it("uses the practitioner fallback, not the FAQ one, when detail is unreadable", async () => {
    refuse("<html>proxy error</html>");

    const error = (await deletePractitioner("01PRACT0000000000000000000").catch(
      (err: unknown) => err,
    )) as Error;

    expect(error.message).toBe("Could not save that. Please try again.");
    expect(error.message).not.toMatch(/entry/);
  });
});

describe("FAQ writes", () => {
  it("relays the server's wording from every FAQ call", async () => {
    refuse(JSON.stringify({ detail: "that entry is too long to index" }));

    for (const call of [
      () => fetchFaqEntries(),
      () => createFaqEntry("Visiting hours are 8am to 5pm."),
      () => updateFaqEntry(1, "Visiting hours are 9am to 5pm."),
      () => deleteFaqEntry(1),
    ]) {
      await expect(call()).rejects.toThrow("that entry is too long to index");
    }
  });

  it("uses the FAQ fallback, distinct from the practitioner one", async () => {
    refuse("", 500);

    // Two fallbacks, not one shared sentence: a staff member seeing "could not save
    // that" while editing the corpus has no way to tell which screen failed.
    await expect(createFaqEntry("anything")).rejects.toThrow(
      "Could not save that entry. Please try again.",
    );
  });
});

describe("attention mark labels", () => {
  it("labels every kind of mark a message can carry", () => {
    // Exhaustive by type: a mark with no label would render an empty badge, which
    // teaches a staff member that "no label" means "nothing important".
    const marks: AttentionMark[] = [
      "patient_asked_for_person",
      "corpus_could_not_answer",
      "assistant_failed",
      "unanswered",
      "urgent_condition",
      "distress",
      "booking_for_another_person",
      "not_authorized",
    ];
    for (const mark of marks) {
      expect(ATTENTION_MARK_LABEL[mark]).toBeTruthy();
    }
  });

  it("gives each of the new causes a label of its own", () => {
    const labels = [
      ATTENTION_MARK_LABEL.urgent_condition,
      ATTENTION_MARK_LABEL.distress,
      ATTENTION_MARK_LABEL.booking_for_another_person,
      ATTENTION_MARK_LABEL.not_authorized,
    ];
    expect(new Set(labels).size).toBe(labels.length);
  });
});

describe("a practitioner's week", () => {
  const PRACTITIONER = "01PRACT0000000000000000000";

  const APPOINTMENTS: PractitionerAppointment[] = [
    {
      id: "01APPT00000000000000000001",
      patient_full_name: "Leo Tolstoy",
      starts_at: "2026-09-24T14:00:00",
      ends_at: "2026-09-24T15:00:00",
    },
  ];

  function answer(body: BodyInit, status = 200): void {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      Promise.resolve(new Response(body, { status })),
    );
  }

  /** The URL the one fetch was made to, parsed against a throwaway origin. */
  function requested(): URL {
    const [input] = vi.mocked(globalThis.fetch).mock.calls[0];
    return new URL(String(input), "http://origin.invalid");
  }

  /** What the read threw, so a test can check the kind and not only the wording. */
  async function failure(): Promise<PractitionerWeekError> {
    const error: unknown = await fetchPractitionerWeek(PRACTITIONER).catch(
      (err: unknown) => err,
    );
    expect(error).toBeInstanceOf(PractitionerWeekError);
    return error as PractitionerWeekError;
  }

  it("reads the console route for that practitioner, carrying the browser's local now", async () => {
    // The window is computed server-side from `local_now`, so the value sent is the one
    // `localNow()` builds - offset-free local wall-clock time, never `toISOString()`'s
    // UTC, which would move "today" for anyone not sitting on UTC.
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(2026, 8, 24, 9, 5, 3));
    answer(JSON.stringify({ appointments: [] }));

    await fetchPractitionerWeek(PRACTITIONER);

    const url = requested();
    expect(url.pathname).toBe(`/console/practitioners/${PRACTITIONER}/appointments`);
    expect(url.searchParams.get("local_now")).toBe("2026-09-24T09:05:03");
  });

  it("sends a local now it is given rather than reading the clock", async () => {
    answer(JSON.stringify({ appointments: [] }));

    await fetchPractitionerWeek(PRACTITIONER, "2026-09-24T23:59:59");

    expect(requested().searchParams.get("local_now")).toBe("2026-09-24T23:59:59");
  });

  it("returns the appointments list, not the envelope around it", async () => {
    answer(JSON.stringify({ appointments: APPOINTMENTS }));

    const page = await fetchPractitionerWeek(PRACTITIONER);

    expect(page.appointments.map((a) => a.patient_full_name)).toEqual([
      "Leo Tolstoy",
    ]);
  });

  it("returns an empty list as an answer, not as a failure", async () => {
    answer(JSON.stringify({ appointments: [] }));

    await expect(fetchPractitionerWeek(PRACTITIONER)).resolves.toEqual({
      appointments: [],
      hasMore: false,
    });
  });

  it("carries the route's own word on whether more are booked", async () => {
    answer(JSON.stringify({ appointments: APPOINTMENTS, has_more: true }));

    await expect(fetchPractitionerWeek(PRACTITIONER)).resolves.toMatchObject({
      hasMore: true,
    });
  });

  it("reads anything but a true as that being all of them", async () => {
    // A body with no `has_more`, or one carrying something that is not a boolean, has
    // not said there is more — and an ellipsis printed on that would be this layer
    // inventing a claim about the clinic's schedule.
    for (const body of [
      { appointments: APPOINTMENTS },
      { appointments: APPOINTMENTS, has_more: "yes" },
      { appointments: APPOINTMENTS, has_more: null },
    ]) {
      answer(JSON.stringify(body));
      await expect(fetchPractitionerWeek(PRACTITIONER)).resolves.toMatchObject({
        hasMore: false,
      });
    }
  });

  it("reports a 404 as a practitioner that no longer exists", async () => {
    // Deleted, or another session's id - the two answer identically, and neither is a
    // scheduler that could not be reached.
    answer(JSON.stringify({ detail: "practitioner not found" }), 404);

    const error = await failure();

    expect(error.kind).toBe("not_found");
    expect(error.message).toBe("This practitioner no longer exists.");
  });

  it.each([503, 504])(
    "reports a %i as appointments that could not be read",
    async (status) => {
      answer(
        JSON.stringify({
          detail: "scheduling is unavailable; the appointments could not be read",
        }),
        status,
      );

      const error = await failure();

      expect(error.kind).toBe("unreadable");
      expect(error.message).toBe("The appointments could not be read.");
    },
  );

  it("reports a request that never reached the server as unreadable, not as not-found", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));

    const error = await failure();

    expect(error.kind).toBe("unreadable");
    expect(error.message).toBe("The appointments could not be read.");
  });

  it("never hands an error body back as a week", async () => {
    // A 422 body is JSON with no `appointments`; returning it would put `undefined`
    // into a `.map` during render.
    answer(JSON.stringify({ detail: [{ msg: "bad local_now" }] }), 422);

    expect((await failure()).kind).toBe("unreadable");
  });

  it.each([JSON.stringify({}), JSON.stringify({ appointments: null }), "null"])(
    "never hands a success body without an appointments list back as a week (%s)",
    async (body) => {
      // A 200 is not a shape: returning `undefined` here would reach a `.map` during
      // render exactly as an error body would.
      answer(body);

      expect((await failure()).kind).toBe("unreadable");
    },
  );

  it("passes the caller's signal through, so a deadline can end the read", async () => {
    answer(JSON.stringify({ appointments: [] }));
    const controller = new AbortController();

    await fetchPractitionerWeek(PRACTITIONER, "2026-09-24T09:00:00", controller.signal);

    const [, init] = vi.mocked(globalThis.fetch).mock.calls[0];
    expect(init?.signal).toBe(controller.signal);
  });
});
