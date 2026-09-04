import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createFaqEntry,
  createPractitioner,
  deleteFaqEntry,
  deletePractitioner,
  fetchFaqEntries,
  fetchPractitioners,
  fetchSpecialties,
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
