import type { ReactNode } from "react";
import { Button } from "./ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogTitle,
} from "./ui/dialog";

/**
 * "Delete this?" — the one prompt asked before a deletion nothing can undo.
 *
 * Three places ask it: the chat strip, the practitioner roster and the clinic
 * documents. It is one component for the same reason `DiscardDialog` is: the question
 * is the same question, and three copies of it would let the two buttons' wording,
 * order and variants drift apart between deletions that are equally irreversible.
 *
 * What differs is the caller's, and only the caller knows it: the name of the thing
 * being deleted, and the sentence saying what goes with it. Every deletion this app
 * offers takes more than the row it was clicked on — a chat takes its patient and their
 * appointments, a practitioner takes the appointments booked with them, an entry takes
 * what the assistant could answer from it — so the sentence is required rather than
 * optional.
 *
 * No `destructive` variant on either button. It maps onto `--color-attention`, which
 * FR-005 reserves for "a person is needed", and however irreversible a deletion is,
 * nobody is being summoned by it. The weight is carried by the sentence, which names
 * exactly what is lost.
 */
export function DeleteDialog({
  open,
  subject,
  onCancel,
  onConfirm,
  children,
}: {
  open: boolean;
  /** What is being deleted, named as the title's object: "Delete {subject}?". */
  subject: string;
  /**
   * Dismissed without deleting.
   *
   * Every way out that is not the Delete button arrives here — the button beside it,
   * Escape, the overlay and the content's own close control — because they all mean the
   * same thing, and a dialog that treated one of them as consent would perform the
   * deletion it was raised to hold back.
   */
  onCancel: () => void;
  onConfirm: () => void;
  /** What is lost with it, in this screen's own words. */
  children: ReactNode;
}) {
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onCancel();
      }}
    >
      {/* Radix renders this into a portal on document.body, so a test reaches it with
          `screen.*` and never with `within(container)`. */}
      <DialogContent data-testid="delete-confirm">
        <DialogTitle>Delete {subject}?</DialogTitle>
        <DialogDescription>{children}</DialogDescription>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            Cancel
          </Button>
          <Button onClick={onConfirm}>Delete</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default DeleteDialog;
