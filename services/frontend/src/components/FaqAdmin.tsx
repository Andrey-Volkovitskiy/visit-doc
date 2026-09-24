import { ArrowLeft, Pencil, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  createFaqEntry,
  deleteFaqEntry,
  fetchFaqEntries,
  updateFaqEntry,
  type FaqEntry,
} from "../lib/consoleApi";
import { useBusyLatch } from "../lib/useBusyLatch";
import { NO_DIRTY_REPORT, type AdminSectionProps } from "./adminSection";
import { Button } from "./ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogTitle,
} from "./ui/dialog";
import { Textarea } from "./ui/textarea";

/**
 * The shape the seeded corpus writes, and the one a staff member is invited to follow.
 *
 * Deliberately anchored and deliberately narrow: an entry is one question and its
 * answer *when it says so*, and anything else is a block of text this screen has no
 * grounds to split. Promoting a first line to a question because it looks like one
 * would be this screen asserting a shape the entry does not have — and the entry text
 * is what the assistant answers from, so a wrong split is a wrong claim about the
 * clinic in the one place staff go to check.
 */
const LABELLED = /^\s*Question:[ \t]*([\s\S]*?)\n[ \t]*Answer:[ \t]*([\s\S]*)$/i;

export interface SplitEntry {
  /** The entry's question, or null when it carries none. */
  question: string | null;
  /** Everything the entry says that is not its question label. */
  answer: string;
}

/**
 * Read an entry as a question and its answer, when it is written as one (FR-034).
 *
 * Pure and exported so the rule can be read — and tested — without any markup.
 */
export function splitEntry(content: string): SplitEntry {
  const match = LABELLED.exec(content);
  if (match === null) return { question: null, answer: content };
  return { question: (match[1] ?? "").trim(), answer: (match[2] ?? "").trim() };
}

/**
 * Which of the tab's three views is on screen (FR-035a, `data-model.md`).
 *
 * A discriminated union rather than two booleans and a nullable id, so "creating and
 * editing at once" and "editing with no id" are unrepresentable rather than guarded
 * against.
 */
type View = { mode: "list" } | { mode: "edit"; id: number } | { mode: "create" };

interface EditorProps {
  /** The entry being edited, or null while one is being written. */
  entry: FaqEntry | null;
  busy: boolean;
  onSubmit: (content: string) => void;
  onLeave: () => void;
  /**
   * Report whether this form now holds work that leaving would lose (FR-035b).
   *
   * Lifted no further than it has to be. The *back* control is guarded in here, where
   * the form is; a **tab** switch is guarded by `App`, because only `App` knows a switch
   * was asked for — Radix destroys this panel to perform one, so by the time this
   * component could notice, the work is already gone.
   */
  onDirtyChange: (dirty: boolean) => void;
}

/**
 * The view that replaces the list while one entry is being written (FR-035a).
 *
 * Rendered in the tab's own content, not in a dialog: the page header, the console
 * header and the tab set stay on screen and operable, which is what lets a staff member
 * leave an unfinished entry by choosing another tab.
 */
function FaqEditor({
  entry,
  busy,
  onSubmit,
  onLeave,
  onDirtyChange,
}: EditorProps) {
  const initial = entry === null ? "" : entry.content;
  const [draft, setDraft] = useState(initial);
  const [confirmingDiscard, setConfirmingDiscard] = useState(false);
  const writing = entry === null;
  const dirty = draft !== initial;

  // Reported up, and **retracted on unmount**. The retraction is the load-bearing half:
  // this component is destroyed by the very tab switch the flag guards, so a flag that
  // outlived it would sit in `App` describing a form that no longer exists — and the
  // next switch, from a section holding nothing, would be blocked by a prompt about work
  // nobody can see or answer for.
  useEffect(() => {
    onDirtyChange(dirty);
    return () => onDirtyChange(false);
  }, [dirty, onDirtyChange]);

  function leave(): void {
    // FR-035b: a box holding work asks before losing it; an untouched one does not
    // interrupt, which is the common case of opening an entry to read it.
    if (dirty) setConfirmingDiscard(true);
    else onLeave();
  }

  return (
    <div data-testid="faq-edit" className="flex flex-col gap-4 p-4">
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={leave}
          className="text-ink-muted hover:text-ink -ml-2"
        >
          <ArrowLeft aria-hidden="true" />
          Back to the documents
        </Button>
      </div>
      <h4 className="text-md text-ink font-semibold">
        {writing ? "New entry" : "Edit entry"}
      </h4>
      <div className="flex flex-col gap-1">
        <p className="text-ink-muted text-sm">
          Write a question and its answer on two lines, labelled{" "}
          <code className="text-ink">Question:</code> and{" "}
          <code className="text-ink">Answer:</code>, and the list reads them as the two
          things they are. Anything else is stored, and answered from, exactly as it is
          written.
        </p>
        <Textarea
          aria-label={writing ? "New entry" : `Entry ${String(entry.id)}`}
          className="min-h-40"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={
            writing
              ? "Something the assistant should be able to answer from..."
              : undefined
          }
        />
      </div>
      <div className="flex items-center gap-2">
        {/* Disabled while the write is out so the wait is visible; the section's own
            latch is what makes a second click harmless either way. */}
        <Button onClick={() => onSubmit(draft)} disabled={busy}>
          {writing ? "Add entry" : "Save"}
        </Button>
      </div>

      {/*
        Radix renders this into a portal on document.body, so a test reaches it with
        `screen.*` and never with `within(container)`.

        No `destructive` variant on either button: it maps onto --color-attention, which
        FR-005 reserves for "a person is needed". Losing what was typed here is not that
        claim, and a colour that means one thing must not be spent on another.
      */}
      <Dialog
        open={confirmingDiscard}
        onOpenChange={(open) => {
          if (!open) setConfirmingDiscard(false);
        }}
      >
        <DialogContent data-testid="discard-confirm">
          <DialogTitle>Leave without saving?</DialogTitle>
          <DialogDescription>
            What you typed here has not been stored, so the assistant cannot answer from
            it. Going back now discards it.
          </DialogDescription>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setConfirmingDiscard(false)}
            >
              Keep editing
            </Button>
            <Button
              onClick={() => {
                setConfirmingDiscard(false);
                onLeave();
              }}
            >
              Discard
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/**
 * Add, edit and delete what the assistant may answer from.
 *
 * Three views over one tab (FR-035a): the corpus as a readable list, one entry being
 * edited, and one being written. The list is a record rather than a set of boxes at
 * rest, so nothing on it is a half-typed change waiting for a Save nobody pressed.
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
export function FaqAdmin({
  onDirtyChange = NO_DIRTY_REPORT,
}: AdminSectionProps) {
  const [entries, setEntries] = useState<FaqEntry[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>({ mode: "list" });
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
      .catch((err: unknown) =>
        report(err, "Could not load the clinic's documents."),
      );
  }, [report]);

  async function handleCreate(content: string): Promise<void> {
    if (!content.trim()) return;
    // A staff member who clicks again because nothing appeared to happen must not add
    // the entry twice. The view is only left once the create lands, so a second call
    // before then reads the very same text and passes the very same guard - and each
    // copy is separately chunked, embedded and indexed, and each counts against the
    // session's entry cap.
    await latch.run("create", async () => {
      setError(null);
      try {
        const created = await createFaqEntry(content);
        setEntries((prev) => [...prev, created]);
        setView({ mode: "list" });
      } catch (err) {
        // The view is left as it is, holding what was typed: a failed save is the one
        // thing that must not ask for it to be typed again.
        report(err, "Could not add that entry.");
      }
    });
  }

  async function handleSave(entry: FaqEntry, content: string): Promise<void> {
    // Latched for a harder reason than the create's. A save is a whole revision write -
    // the entry is chunked, embedded and indexed again - so a double click does that
    // twice, and the second publish then fails its own staleness guard against the
    // revision the first one had already published, reporting a conflict over an entry
    // that saved perfectly well.
    await latch.run(`save:${String(entry.id)}`, async () => {
      setError(null);
      try {
        // The response *is* the stored entry, so what is shown is what the assistant
        // will answer from.
        const saved = await updateFaqEntry(entry.id, content);
        setEntries((prev) => prev.map((e) => (e.id === saved.id ? saved : e)));
        setView({ mode: "list" });
      } catch (err) {
        report(err, "Could not save that entry.");
      }
    });
  }

  async function handleDelete(entry: FaqEntry): Promise<void> {
    // The second delete of an entry the first one removed is a 404, reported as a
    // failure the staff member cannot act on - for a delete that worked.
    await latch.run(`delete:${String(entry.id)}`, async () => {
      setError(null);
      try {
        await deleteFaqEntry(entry.id);
        setEntries((prev) => prev.filter((e) => e.id !== entry.id));
      } catch (err) {
        report(err, "Could not delete that entry.");
      }
    });
  }

  // A view naming an entry the corpus no longer holds is not a view: the text it was
  // about is gone, so the tab falls back to the list rather than editing nothing.
  const editing =
    view.mode === "edit"
      ? (entries.find((e) => e.id === view.id) ?? null)
      : null;
  const editorOpen = view.mode === "create" || editing !== null;

  return (
    <div data-testid="faq-admin" className="flex min-h-0 flex-col gap-3 p-4">
      {editorOpen ? (
        <FaqEditor
          onDirtyChange={onDirtyChange}
          // Remounted per subject, so the box starts from the entry it is about rather
          // than from whatever the last one was left holding.
          key={editing === null ? "create" : editing.id}
          entry={editing}
          busy={latch.isBusy(
            editing === null ? "create" : `save:${String(editing.id)}`,
          )}
          onSubmit={(content) => {
            if (editing === null) void handleCreate(content);
            else void handleSave(editing, content);
          }}
          onLeave={() => setView({ mode: "list" })}
        />
      ) : (
        <>
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-md text-ink font-semibold">Clinic documents</h3>
            <Button
              size="sm"
              onClick={() => setView({ mode: "create" })}
            >
              <Plus aria-hidden="true" />
              Add entry
            </Button>
          </div>
          {entries.length === 0 ? (
            <p data-testid="no-faq-entries" className="text-ink-muted text-sm">
              Nothing here yet.
            </p>
          ) : (
            <ul className="flex flex-col gap-2">
              {entries.map((entry) => {
                const { question, answer } = splitEntry(entry.content);
                return (
                  <li
                    key={entry.id}
                    data-testid="faq-entry"
                    className="border-rule bg-surface flex items-start justify-between gap-3 rounded-md border p-3"
                  >
                    <div className="min-w-0">
                      {question !== null && (
                        <h4 className="text-ink text-base font-medium">
                          {question}
                        </h4>
                      )}
                      <p className="text-ink-muted text-sm whitespace-pre-wrap">
                        {answer}
                      </p>
                    </div>
                    <div className="flex flex-none items-center gap-1">
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`Edit entry ${String(entry.id)}`}
                        className="text-ink-muted hover:text-ink"
                        onClick={() => setView({ mode: "edit", id: entry.id })}
                      >
                        <Pencil aria-hidden="true" />
                      </Button>
                      {/* At rest this is an ordinary control: FR-005 keeps
                          --color-attention for "a person is needed", and a delete
                          button sitting in a list is not making that claim. */}
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`Delete entry ${String(entry.id)}`}
                        className="text-ink-muted hover:text-ink"
                        onClick={() => void handleDelete(entry)}
                        disabled={latch.isBusy(`delete:${String(entry.id)}`)}
                      >
                        <Trash2 aria-hidden="true" />
                      </Button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </>
      )}
      {error && (
        <p
          data-testid="faq-error"
          className="text-attention bg-attention-wash border-attention/30 rounded-md border px-3 py-2 text-sm"
        >
          {error}
        </p>
      )}
    </div>
  );
}

export default FaqAdmin;
