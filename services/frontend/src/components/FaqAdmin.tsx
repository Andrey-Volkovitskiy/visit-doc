import { useCallback, useEffect, useState } from "react";
import {
  createFaqEntry,
  deleteFaqEntry,
  fetchFaqEntries,
  updateFaqEntry,
  type FaqEntry,
} from "../lib/consoleApi";
import { useBusyLatch } from "../lib/useBusyLatch";

/**
 * Add, edit and delete what the assistant may answer from.
 *
 * Every entry listed here is one the assistant can answer from, and there is
 * deliberately nothing on screen saying so. An entry owns a live revision or it cannot
 * be stored, so a per-entry retrievability indicator could only ever read "yes" — and a
 * signal that can never fire is worse than none, because it teaches a staff member to
 * rely on a warning that would not come.
 *
 * A refused save changes nothing, so what was typed stays where it was typed: the reply
 * says why, and the text is still there to correct.
 */
export function FaqAdmin() {
  const [entries, setEntries] = useState<FaqEntry[]>([]);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  // One latch for every gesture on this pane, keyed by what the gesture is about, so a
  // second click on any of them is refused rather than only on Add. See `useBusyLatch`.
  const latch = useBusyLatch();

  const report = useCallback((err: unknown, fallback: string): void => {
    setError(err instanceof Error ? err.message : fallback);
  }, []);

  useEffect(() => {
    void fetchFaqEntries()
      .then(setEntries)
      // Without this the screen sits empty with nothing explaining why.
      .catch((err: unknown) => report(err, "Could not load the clinic's documents."));
  }, [report]);

  async function handleCreate(): Promise<void> {
    if (!draft.trim()) return;
    // A staff member who clicks again because nothing appeared to happen must not add
    // the entry twice. The box is only cleared once the create lands, so a second call
    // before then reads the very same text and passes the very same guard - and each
    // copy is separately chunked, embedded and indexed, and each counts against the
    // session's entry cap.
    await latch.run("create", async () => {
      setError(null);
      try {
        const created = await createFaqEntry(draft);
        setEntries((prev) => [...prev, created]);
        setDraft("");
      } catch (err) {
        report(err, "Could not add that entry.");
      }
    });
  }

  async function handleSave(entry: FaqEntry): Promise<void> {
    // Latched for a harder reason than the create's. A save is a whole revision write -
    // the entry is chunked, embedded and indexed again - so a double click does that
    // twice, and the second publish then fails its own staleness guard against the
    // revision the first one had already published, reporting a conflict over an entry
    // that saved perfectly well.
    await latch.run(`save:${entry.id}`, async () => {
      setError(null);
      try {
        // The response *is* the stored entry, so what is shown is what the assistant
        // will answer from.
        const saved = await updateFaqEntry(entry.id, entry.content);
        setEntries((prev) => prev.map((e) => (e.id === saved.id ? saved : e)));
      } catch (err) {
        report(err, "Could not save that entry.");
      }
    });
  }

  async function handleDelete(entry: FaqEntry): Promise<void> {
    // The second delete of an entry the first one removed is a 404, reported as a
    // failure the staff member cannot act on - for a delete that worked.
    await latch.run(`delete:${entry.id}`, async () => {
      setError(null);
      try {
        await deleteFaqEntry(entry.id);
        setEntries((prev) => prev.filter((e) => e.id !== entry.id));
      } catch (err) {
        report(err, "Could not delete that entry.");
      }
    });
  }

  return (
    <div data-testid="faq-admin">
      <h3>Clinic documents</h3>
      <label>
        New entry
        <textarea
          aria-label="New entry"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Something the assistant should be able to answer from..."
        />
      </label>
      {/* Disabled while the create is out so the wait is visible; the handler's own
          latch is what makes a second click harmless either way. */}
      <button onClick={() => void handleCreate()} disabled={latch.isBusy("create")}>
        Add entry
      </button>

      {entries.length === 0 ? (
        <p data-testid="no-faq-entries">Nothing here yet.</p>
      ) : (
        <ul>
          {entries.map((entry) => (
            <li key={entry.id} data-testid="faq-entry">
              <textarea
                aria-label={`Entry ${entry.id}`}
                value={entry.content}
                onChange={(e) =>
                  setEntries((prev) =>
                    prev.map((row) =>
                      row.id === entry.id
                        ? { ...row, content: e.target.value }
                        : row,
                    ),
                  )
                }
              />
              <button
                onClick={() => void handleSave(entry)}
                disabled={latch.isBusy(`save:${entry.id}`)}
              >
                Save
              </button>
              <button
                aria-label={`Delete entry ${entry.id}`}
                onClick={() => void handleDelete(entry)}
                disabled={latch.isBusy(`delete:${entry.id}`)}
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}
      {error && <p data-testid="faq-error">{error}</p>}
    </div>
  );
}

export default FaqAdmin;
