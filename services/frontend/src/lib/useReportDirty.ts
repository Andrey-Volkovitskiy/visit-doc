import { useEffect } from "react";

/**
 * Report whether a form holds work that leaving would lose, and retract it on unmount
 * (015 FR-035b).
 *
 * The retraction is the load-bearing half. Every form that reports is destroyed by the
 * very tab switch the flag guards — Radix unmounts an inactive panel — so a flag that
 * outlived it would sit in `App` describing a form that no longer exists, and the next
 * switch, from a section holding nothing, would be blocked by a prompt about work nobody
 * can see or answer for. Declared once so that no form can report without retracting.
 *
 * `onDirtyChange` should be stable: the report re-runs whenever its identity changes.
 */
export function useReportDirty(
  dirty: boolean,
  onDirtyChange: (dirty: boolean) => void,
): void {
  useEffect(() => {
    onDirtyChange(dirty);
    return () => onDirtyChange(false);
  }, [dirty, onDirtyChange]);
}
