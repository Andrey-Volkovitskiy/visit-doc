import { useCallback, useEffect, useRef, useState } from "react";
import {
  createFaqEntry,
  deleteFaqEntry,
  fetchFaqEntries,
  updateFaqEntry,
  type FaqEntry,
} from "../lib/consoleApi";

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
  // Whether a create is in flight, for the button to show. `creating` repaints it;
  // `creatingRef` is what actually stops a second call, and the two are not
  // alternatives - see `handleCreate`.
  const [creating, setCreating] = useState(false);
  // The same latch as the handler can see it. A ref rather than reading `creating`,
  // because two clicks dispatched in one React batch both run against the render that
  // preceded them: the state the first set has not committed, so a guard reading it lets
  // the second through - and the button is still painted enabled for the same reason.
  const creatingRef = useRef(false);

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
    if (creatingRef.current) return;
    setError(null);
    creatingRef.current = true;
    setCreating(true);
    try {
      const created = await createFaqEntry(draft);
      setEntries((prev) => [...prev, created]);
      setDraft("");
    } catch (err) {
      report(err, "Could not add that entry.");
    } finally {
      // Released however this ended, including the failure above: the text is still in
      // the box by design, and a latch left closed would leave a staff member holding an
      // entry they can no longer add.
      creatingRef.current = false;
      setCreating(false);
    }
  }

  async function handleSave(entry: FaqEntry): Promise<void> {
    setError(null);
    try {
      // The response *is* the stored entry, so what is shown is what the assistant
      // will answer from.
      const saved = await updateFaqEntry(entry.id, entry.content);
      setEntries((prev) => prev.map((e) => (e.id === saved.id ? saved : e)));
    } catch (err) {
      report(err, "Could not save that entry.");
    }
  }

  async function handleDelete(entry: FaqEntry): Promise<void> {
    setError(null);
    try {
      await deleteFaqEntry(entry.id);
      setEntries((prev) => prev.filter((e) => e.id !== entry.id));
    } catch (err) {
      report(err, "Could not delete that entry.");
    }
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
      <button onClick={() => void handleCreate()} disabled={creating}>
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
              <button onClick={() => void handleSave(entry)}>Save</button>
              <button
                aria-label={`Delete entry ${entry.id}`}
                onClick={() => void handleDelete(entry)}
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
