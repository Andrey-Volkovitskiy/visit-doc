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
 * "Leave without saving?" — the one prompt FR-035b permits before typed work is lost.
 *
 * Three places ask it: the shell, when a tab switch would destroy the panel holding a
 * form, and each admin section's own back control. They asked it in three copies of the
 * same markup, differing only in one sentence — so the question had three homes, and
 * the two buttons' wording, order and variants could drift apart between the routes out
 * of a single form without any of them looking wrong on its own.
 *
 * The sentence stays the caller's, because that is the part that genuinely differs:
 * only the caller knows what is about to happen and what the typed text was for. No
 * `destructive` variant on either button — it maps onto `--color-attention`, which
 * FR-005 reserves for "a person is needed", and losing a draft is not that claim.
 */
export function DiscardDialog({
  open,
  onKeepEditing,
  onDiscard,
  children,
}: {
  open: boolean;
  /**
   * Dismissed without discarding.
   *
   * Every way out that is not the Discard button arrives here — the button beside it,
   * Escape, the overlay and the content's own close control — because they all mean the
   * same thing, and a dialog that treated one of them as consent would lose the work it
   * was raised to protect.
   */
  onKeepEditing: () => void;
  onDiscard: () => void;
  /** What is about to be lost, in this screen's own words. */
  children: ReactNode;
}) {
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onKeepEditing();
      }}
    >
      {/* Radix renders this into a portal on document.body, so a test reaches it with
          `screen.*` and never with `within(container)`. */}
      <DialogContent data-testid="discard-confirm">
        <DialogTitle>Leave without saving?</DialogTitle>
        <DialogDescription>{children}</DialogDescription>
        <DialogFooter>
          <Button variant="outline" onClick={onKeepEditing}>
            Keep editing
          </Button>
          <Button onClick={onDiscard}>Discard</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default DiscardDialog;
