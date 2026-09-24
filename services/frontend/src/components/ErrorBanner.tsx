/**
 * The one way a surface here says something went wrong (FR-010).
 *
 * Six places raised one — both panes' shells, both threads' composers and both admin
 * sections — each with the same seven utility classes written out again. That is one
 * appearance with six homes: a change to what a failure looks like had to be made six
 * times, and a banner that quietly stopped matching the others looked exactly like one
 * that did.
 *
 * Its `role="alert"` is part of the contract rather than a per-caller choice. A failure
 * a reader has to notice by looking is not reported, and the two shells that already
 * carried the role were right about that while the four that did not were not.
 *
 * The test hook stays the caller's, because a hook names what a thing is and these are
 * six different things that failed — `chat-list-error` and `faq-error` are not
 * interchangeable names for one banner.
 */
export function ErrorBanner({
  testId,
  message,
  className = "",
}: {
  /** This surface's own hook, per the table in `.claude/CLAUDE.md`. */
  testId: string;
  message: string;
  /** Spacing the surrounding layout owes it, and nothing else. */
  className?: string;
}) {
  return (
    <p
      data-testid={testId}
      role="alert"
      className={`text-attention bg-attention-wash border-attention/30 rounded-md border px-3 py-2 text-sm ${className}`}
    >
      {message}
    </p>
  );
}

export default ErrorBanner;
