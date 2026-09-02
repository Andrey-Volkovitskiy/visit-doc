import { useCallback, useEffect, useState } from "react";
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
    setError(null);
    try {
      const created = await createFaqEntry(draft);
      setEntries((prev) => [...prev, created]);
      setDraft("");
    } catch (err) {
      report(err, "Could not add that entry.");
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
      <button onClick={() => void handleCreate()}>Add entry</button>

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
