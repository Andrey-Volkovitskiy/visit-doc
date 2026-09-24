/**
 * What every console admin section owes the shell around it.
 *
 * Its own module rather than a field of whichever section happened to declare it first:
 * `PractitionerAdmin` and `FaqAdmin` are siblings, and one importing the other's props
 * to describe itself makes a contract they share look like a detail of one of them.
 */

/** Reported up, and retracted on unmount, by a section holding unsaved work. */
export interface AdminSectionProps {
  /**
   * Whether this section now holds work that leaving it would lose (FR-035b).
   *
   * The *back* control out of an edit view is guarded inside the section that owns the
   * form. A **tab** switch cannot be: Radix destroys the inactive panel to perform one,
   * so by the time the section could notice, the typed text is already gone. Only the
   * shell that performs the switch can hold it back, and this is what tells it to.
   *
   * Optional, and defaulted to a no-op: a section rendered on its own — which is how
   * every one of its own tests renders it — has nobody to report to, and requiring the
   * prop would make the guard's shell a precondition for using the section at all.
   */
  onDirtyChange?: (dirty: boolean) => void;
}

/**
 * The default `onDirtyChange`, declared once at module scope.
 *
 * A `() => undefined` written in the parameter list is a *new function every render*,
 * and it is read by an editor's effect dependency list — so the effect that reports
 * dirtiness would tear down and re-run on every keystroke, retracting the flag and
 * setting it again, for no reason but the identity of a function that does nothing.
 */
export const NO_DIRTY_REPORT = (): void => undefined;
