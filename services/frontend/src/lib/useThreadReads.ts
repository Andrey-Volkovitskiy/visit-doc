import { useEffect, useRef } from "react";

/**
 * How long one thread read may run before it is abandoned.
 *
 * A read with no deadline is not slow, it is permanent: `fetch` on a wedged socket
 * neither resolves nor rejects, so the pane that issued it sits blank with no banner
 * and no retry, and the poll goes on issuing one more every interval until the
 * browser's per-origin socket cap is full — at which point the console listing, the
 * SSE stream and the next `POST` all queue behind reads that will never answer, and
 * the page wedges rather than degrading. Chosen well above a slow-but-real read and
 * well below the point where a person would have given up and reloaded.
 */
export const READ_TIMEOUT_MS = 8000;

/**
 * The most reads one pane will hold open at once.
 *
 * Not the "skip while one is outstanding" trade this hook otherwise refuses — that one
 * makes liveness depend on a read completing, and a read that hangs then stops the
 * retry for as long as it hangs. Every read here settles within `READ_TIMEOUT_MS`, so
 * the cap can only ever defer a tick by that much, and it is what keeps a slow backend
 * from spending this origin's whole socket budget on one pane.
 */
const MAX_IN_FLIGHT_READS = 3;

/**
 * What a read that ran past its deadline reports.
 *
 * Worded for either pane, because the hook does not know which one it is serving, and
 * worded as temporary because it is: the tick that follows issues another read.
 */
export const READ_TIMEOUT_MESSAGE = "This is taking too long — trying again.";

/**
 * A banner on a thread pane, and which of the two things that raise one raised it.
 *
 * `kind` exists because only one of them is disproved by a later read landing. Asking
 * "has any read landed yet" instead answered for both at once, and cleared a failed
 * send's banner while the message it could not send still sat in the box.
 */
export interface Banner {
  kind: "read" | "send";
  text: string;
}

export interface ThreadReadsOptions<T> {
  /** The conversation being read. Null when none is open. */
  chatId: string | null;
  /** The newest message time the console poll reports for it. */
  lastMessageAt: string | null | undefined;
  /** How many times that poll has answered — what makes a retry possible. */
  pollTick: number | undefined;
  /**
   * True while this pane must not replace what is on screen, so the tick is left
   * unhandled and retried rather than answered from before whatever is in progress.
   */
  paused: boolean;
  /** Fetch the thread. The signal carries this hook's deadline and its aborts. */
  read: (chatId: string, signal: AbortSignal) => Promise<T>;
  /** Clear the pane: the conversation changed, and nothing on screen belongs to it. */
  onReset: () => void;
  /**
   * A read landed and is newer than anything applied.
   *
   * It is also the signal that a banner `onOpenFailed` raised is disproved: this is
   * the thread that read could not load. Which banner that clears is the caller's
   * decision, not this hook's — a pane's banner can equally belong to something it
   * tried to send, and a hook that cleared "the banner" would clear that one too.
   */
  onLoaded: (data: T) => void;
  /** The read that opens a conversation failed, and the pane is empty because of it. */
  onOpenFailed: (error: unknown) => void;
}

/**
 * Own the reads of one conversation's thread, and decide which answers may be applied.
 *
 * Lives here, once, because both panes need exactly this and a copy each is what the
 * duplication cost: the same defects were present in both copies and had to be found
 * and fixed in both, with no test able to tell a one-sided fix from a whole one.
 * `ChatWindow` and `StaffThread` now differ only in what they read and what they do
 * with an answer.
 *
 * The staleness rule, in full. Every read is numbered as it is *issued*, and
 * `appliedThrough` is the number of the newest one whose answer the screen already
 * reflects. An answer numbered at or below it describes a thread older than what is
 * displayed and is dropped, because applying it would be a rewind. Two things move it:
 * a read landing (nothing issued before it is wanted after it) and a conversation being
 * opened, which retires every read still in flight — each answers about a thread no
 * longer on screen. A *number* rather than a comparison of chat ids, because leaving a
 * conversation and coming back is a second visit and not the same one: a read issued
 * during the first visit still names the conversation now open, and only its number
 * says it belongs to a thread this pane has since thrown away and reloaded. A read that
 * *failed* deliberately moves nothing: it put nothing on screen, so an answer issued
 * before it is still a correction rather than a rewind.
 *
 * What a read is *accounted for* by is captured when it goes out, never read when it
 * lands: by then the poll may be reporting a later message, and filing that value would
 * mark as handled a message the answer does not contain. It is filed on landing and
 * only on landing, so the marker means "accounted for" and never "attempted" — filing
 * on the strength of having asked is what left a pane blank behind a banner, with every
 * later tick reporting the very value already filed as handled.
 *
 * Every read carries a deadline and an abort, and both are structural rather than
 * defensive: they are what make "a read that hangs is retried" true, and what bounds
 * how much of the origin's socket budget one pane can hold.
 */
export function useThreadReads<T>(options: ThreadReadsOptions<T>): void {
  const { chatId, lastMessageAt, pollTick, paused } = options;

  // The callbacks are read through a ref so a caller need not memoize them: what these
  // effects depend on is the conversation and the poll, never the identity of a
  // function the component rebuilds every render.
  const latest = useRef(options);
  latest.current = options;

  // The poll value this pane has already read the thread for. Compared by identity,
  // never by clock arithmetic: these strings are the server's own, and turning them
  // into a comparison of instants would put a browser's clock in charge of whether a
  // patient's message is ever shown.
  //
  // `undefined` is "nothing accounted for yet" and is deliberately *not* the same as
  // `null`. A conversation with no messages polls as `last_message_at: null`, which is
  // a real answer this pane must be able to record as handled — collapsing the two
  // would make the first message ever written into an open, empty conversation look
  // like the value that described the thread already loaded, and it would never be
  // fetched.
  const handledLastMessageAtRef = useRef<string | null | undefined>(undefined);
  const issuedReadsRef = useRef(0);
  const appliedThroughRef = useRef(0);
  // The read that opens a conversation, while it is still outstanding, and the poll
  // value it will file if it lands — null once it has settled, however it settled. It
  // is what stops the refetch effect asking, on this conversation's very first tick,
  // the question already out. Keyed to the value rather than to "a read is in flight",
  // so a message arriving while that read runs is still fetched rather than held behind
  // an answer that may never come.
  const openingReadRef = useRef<{ accountsFor: string | null | undefined } | null>(
    null,
  );
  const inFlightRef = useRef<Set<AbortController>>(new Set());

  const abortInFlight = (): void => {
    for (const controller of inFlightRef.current) controller.abort();
    inFlightRef.current.clear();
  };

  const startRead = (
    chat: string,
    accountsFor: string | null | undefined,
    opening: boolean,
  ): void => {
    const generation = ++issuedReadsRef.current;
    const entry = opening ? { accountsFor } : null;
    if (entry !== null) openingReadRef.current = entry;

    const controller = new AbortController();
    inFlightRef.current.add(controller);
    let deadline: ReturnType<typeof setTimeout>;
    // The deadline is this hook's, not the fetch's. Aborting the signal is what frees
    // the socket, but a `read` that never looks at the signal - or a connection that
    // swallows the abort - would still leave a promise that neither resolves nor
    // rejects, and the whole latch-and-retry machinery below only runs when something
    // settles. So the race settles it here, whatever the read does.
    const answered = new Promise<T>((resolve, reject) => {
      deadline = setTimeout(() => {
        controller.abort();
        reject(new Error(READ_TIMEOUT_MESSAGE));
      }, READ_TIMEOUT_MS);
      latest.current.read(chat, controller.signal).then(resolve, reject);
    });

    // Runs first on both paths, before any staleness guard. A read that is retired, or
    // one that hangs until its deadline, still has to give the opening latch and its
    // socket back: released *after* the guard instead, a retired opening read left the
    // latch set forever and every later tick reporting the value it went out for
    // returned early, wedging the pane for the rest of the visit.
    const settle = (): void => {
      clearTimeout(deadline);
      inFlightRef.current.delete(controller);
      if (entry !== null && openingReadRef.current === entry) {
        openingReadRef.current = null;
      }
    };

    void answered
      .then((data) => {
        settle();
        if (generation <= appliedThroughRef.current) return;
        appliedThroughRef.current = generation;
        handledLastMessageAtRef.current = accountsFor;
        latest.current.onLoaded(data);
      })
      .catch((error: unknown) => {
        settle();
        if (generation <= appliedThroughRef.current) return;
        // Only the opening read says anything. A failed refetch leaves the thread as it
        // was and the tick unhandled so the next one genuinely does try again; a banner
        // for a blip nobody can act on would only teach them to ignore the one that
        // matters.
        if (opening) latest.current.onOpenFailed(error);
      });
  };

  useEffect(() => {
    // Aborted rather than merely retired: each answers about a thread no longer on
    // screen, and the socket it holds is one this pane wants back within a tick or two.
    abortInFlight();
    handledLastMessageAtRef.current = undefined;
    // Retired here, before the refetch effect below can run for the newly opened
    // conversation, since effects run in the order they are declared.
    appliedThroughRef.current = issuedReadsRef.current;
    openingReadRef.current = null;
    latest.current.onReset();
    if (chatId === null) return;
    // The value the poll is reporting as this conversation opens, which is what this
    // read will account for. Deliberately not a dependency of this effect — it is a
    // snapshot of the moment the read went out, and re-running the reset above on every
    // new poll value is the last thing a pane wants.
    startRead(chatId, latest.current.lastMessageAt, true);
  }, [chatId]);

  useEffect(() => {
    // `undefined` here means the caller is not feeding this pane the poll at all, which
    // is a different thing from a conversation the poll says is empty.
    if (chatId === null || lastMessageAt === undefined) return;
    if (handledLastMessageAtRef.current === lastMessageAt) return;
    const openingRead = openingReadRef.current;
    if (openingRead !== null && openingRead.accountsFor === lastMessageAt) {
      // Already out for exactly this value, and it files the value itself when it
      // lands. Asking again would only be the same question twice.
      return;
    }
    if (paused) return;
    if (inFlightRef.current.size >= MAX_IN_FLIGHT_READS) return;
    startRead(chatId, lastMessageAt, false);
  }, [chatId, lastMessageAt, pollTick, paused]);

  useEffect(() => {
    return () => {
      // Retired as well as aborted: an abort rejects, and a rejection reaching a
      // callback after the component is gone is a state update on nothing.
      appliedThroughRef.current = issuedReadsRef.current;
      abortInFlight();
    };
  }, []);
}
