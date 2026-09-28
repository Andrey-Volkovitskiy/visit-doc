import { Copy } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import {
  fetchConnectedApps,
  issuePairingCode,
  revokeConnectedApp,
  type ConnectedApp,
  type ConnectedAppsListing,
  type ConnectorUnavailableReason,
} from "../lib/consoleApi";
import { ErrorBanner } from "./ErrorBanner";
import { Button } from "./ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogTitle,
} from "./ui/dialog";

/** Why pairing is unavailable, in the words the tab shows (FR-004). */
const UNAVAILABLE_REASON: Record<ConnectorUnavailableReason, string> = {
  not_configured: "no public address is configured (PUBLIC_BASE_URL).",
  not_https: "the public address (PUBLIC_BASE_URL) is not an https:// address.",
  has_path:
    "the public address (PUBLIC_BASE_URL) must be an origin only, with no path.",
};

/** `m:ss`, the way every countdown on the console is written. */
function formatRemaining(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

/** How long ago, in the coarse words a list of paired apps needs. */
export function formatAgo(seconds: number): string {
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return `${Math.floor(seconds / 86400)} d ago`;
}

/**
 * A code this page asked for, and when its answer arrived.
 *
 * `receivedAt` is this page's own clock at the moment the server said how long the code
 * had left. The countdown is that lifetime minus time elapsed *on the page* — never a
 * server timestamp compared with the browser's clock, which is the one comparison that
 * goes wrong when the two disagree.
 */
interface IssuedCode {
  code: string;
  address: string;
  expiresInSeconds: number;
  receivedAt: number;
}

/** The same, for the listing's report of a live code this page was not shown. */
interface ActiveCode {
  expiresInSeconds: number;
  receivedAt: number;
}

/**
 * Whole seconds `code` has left at `now`.
 *
 * Elapsed time is floored at zero: the render that first shows a code runs with the
 * clock `useNow` last read, which is older than `receivedAt`, and would otherwise count
 * a second the code never had.
 */
function secondsLeft(
  code: { expiresInSeconds: number; receivedAt: number },
  now: number,
): number {
  const elapsed = Math.max(0, Math.floor((now - code.receivedAt) / 1000));
  return Math.max(0, code.expiresInSeconds - elapsed);
}

/** When `code` runs out, on this page's clock. */
function deadlineOf(code: ActiveCode): number {
  return code.receivedAt + code.expiresInSeconds * 1000;
}

/**
 * How far a live code's remaining time may exceed the shown one's and still be the same
 * code: a request's round trip plus the rounding of two whole-second counts. A code
 * replacing the shown one was issued later, so it outlives it by more than this.
 */
const SAME_CODE_SLACK_SECONDS = 5;

/**
 * Whether a listing says the code on screen can no longer be used.
 *
 * Only a listing whose request began after the code arrived speaks for it. It reports
 * no live code once the code was used, and a code outliving this one once another page
 * replaced it; either way the code on screen would be refused. A code about to run out
 * is left to the countdown, which says "Code expired" rather than dropping it.
 */
function isSuperseded(
  code: IssuedCode,
  live: { expires_in_seconds: number } | null,
  requestedAt: number,
  now: number,
): boolean {
  if (requestedAt < code.receivedAt) return false;
  const left = secondsLeft(code, now);
  if (left <= SAME_CODE_SLACK_SECONDS) return false;
  return (
    live === null || live.expires_in_seconds > left + SAME_CODE_SLACK_SECONDS
  );
}

/**
 * This page's clock, ticking once a second until `until`, and still when it is null.
 *
 * It stops at the deadline rather than when a code leaves the page: an expired code
 * stays on screen as "Code expired", with nothing left to count.
 */
function useNow(until: number | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (until === null) return undefined;
    const start = Date.now();
    setNow(start);
    if (start >= until) return undefined;
    const timer = setInterval(() => {
      const current = Date.now();
      setNow(current);
      if (current >= until) clearInterval(timer);
    }, 1000);
    return () => clearInterval(timer);
  }, [until]);
  return now;
}

/** Put `text` on the clipboard, when the browser lets this page have one. */
function copy(text: string): void {
  // Nothing to report if it fails: the text is on screen to copy by hand, and a banner
  // about a clipboard would be louder than the thing it failed to do.
  navigator.clipboard?.writeText(text).catch(() => undefined);
}

function CopyButton({ label, text }: { label: string; text: string }) {
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={label}
      title={label}
      onClick={() => copy(text)}
      className="text-ink-muted hover:text-ink"
    >
      <Copy aria-hidden="true" />
    </Button>
  );
}

function PairedApp({
  app,
  onRevoke,
}: {
  app: ConnectedApp;
  onRevoke: (app: ConnectedApp) => void;
}) {
  return (
    <li
      data-testid="connected-app"
      className="border-rule-soft flex flex-wrap items-center gap-x-3 gap-y-1 border-b py-3 last:border-b-0"
    >
      <span className="text-ink font-medium">{app.client_name}</span>
      <span className="text-ink-muted text-sm">
        Paired {formatAgo(app.paired_seconds_ago)}
      </span>
      <span className="text-ink-muted text-sm">
        {app.last_used_seconds_ago === null
          ? "Never used"
          : `Last used ${formatAgo(app.last_used_seconds_ago)}`}
      </span>
      <Button
        variant="outline"
        size="sm"
        className="ml-auto"
        onClick={() => onRevoke(app)}
      >
        Revoke
      </Button>
    </li>
  );
}

/**
 * "Revoke this app?" — asked before cutting a paired app off.
 *
 * Not `DeleteDialog`: nothing is deleted, and its "Delete" button would name the wrong
 * act. No `destructive` variant either — `--color-attention` means a person is needed,
 * and revoking an app summons nobody.
 */
function RevokeDialog({
  app,
  onCancel,
  onConfirm,
}: {
  app: ConnectedApp | null;
  onCancel: () => void;
  onConfirm: (app: ConnectedApp) => void;
}) {
  return (
    <Dialog
      open={app !== null}
      onOpenChange={(next) => {
        if (!next) onCancel();
      }}
    >
      <DialogContent>
        <DialogTitle>Revoke {app?.client_name}?</DialogTitle>
        <DialogDescription>
          Its next question will be refused. To use it again, pair it with a new
          code.
        </DialogDescription>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            Cancel
          </Button>
          <Button onClick={() => app !== null && onConfirm(app)}>Revoke</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/**
 * The console's Connected apps tab: pair the Claude app with this session, and see
 * which apps are paired.
 *
 * Mounted only while its tab is open, and re-reads on every console poll tick while it
 * is, so a pairing made on a phone appears here without a reload.
 */
export function ConnectedApps({ pollTick }: { pollTick: number }) {
  const [listing, setListing] = useState<ConnectedAppsListing | null>(null);
  const [active, setActive] = useState<ActiveCode | null>(null);
  const [issued, setIssued] = useState<IssuedCode | null>(null);
  const [issuing, setIssuing] = useState(false);
  // Two slots, because the poll's success must not clear a failed action's report: a
  // refused revoke would otherwise leave the screen within one tick of appearing.
  const [readError, setReadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<ConnectedApp | null>(null);

  const read = useCallback(() => {
    const requestedAt = Date.now();
    fetchConnectedApps()
      .then((next) => {
        const receivedAt = Date.now();
        setListing(next);
        setActive(
          next.pairing_code === null
            ? null
            : {
                expiresInSeconds: next.pairing_code.expires_in_seconds,
                receivedAt,
              },
        );
        // A code used on the phone, or replaced from another tab, leaves the page
        // rather than counting down as if it still worked.
        setIssued((current) =>
          current !== null &&
          isSuperseded(current, next.pairing_code, requestedAt, receivedAt)
            ? null
            : current,
        );
        setReadError(null);
      })
      .catch((e: unknown) => {
        setReadError(
          e instanceof Error ? e.message : "Could not load the connected apps.",
        );
      });
  }, []);

  useEffect(() => {
    read();
  }, [read, pollTick]);

  const deadlines = [issued, active]
    .filter((code): code is ActiveCode => code !== null)
    .map(deadlineOf);
  const now = useNow(deadlines.length === 0 ? null : Math.max(...deadlines));
  const issuedLeft = issued === null ? 0 : secondsLeft(issued, now);
  const activeLeft = active === null ? 0 : secondsLeft(active, now);

  function getCode(): void {
    setIssuing(true);
    issuePairingCode()
      .then((next) => {
        setIssued({
          code: next.code,
          address: next.address,
          expiresInSeconds: next.expires_in_seconds,
          receivedAt: Date.now(),
        });
        setActionError(null);
      })
      .catch((e: unknown) => {
        setActionError(
          e instanceof Error
            ? e.message
            : "Could not get a pairing code. Please try again.",
        );
      })
      .finally(() => setIssuing(false));
  }

  function revoke(app: ConnectedApp): void {
    setRevoking(null);
    revokeConnectedApp(app.id)
      .then(() => {
        // The row leaves on the answer rather than on the next poll: the server has
        // said it is gone, and a row offering a Revoke that already happened invites a
        // second click.
        setListing((current) =>
          current === null
            ? current
            : {
                ...current,
                grants: current.grants.filter((g) => g.id !== app.id),
              },
        );
        setActionError(null);
      })
      .catch((e: unknown) => {
        setActionError(
          e instanceof Error
            ? e.message
            : "Could not revoke that app. Please try again.",
        );
      });
  }

  const connector = listing?.connector ?? null;
  const error = actionError ?? readError;

  return (
    <div className="flex max-w-2xl flex-col gap-6 p-4">
      <p className="text-ink">
        Connect the Claude app to this console to ask, while you&apos;re away,
        how many conversations need attention and how many bookings changed
        recently. Claude can read these two counts and nothing else.
      </p>

      {error !== null && (
        <ErrorBanner testId="connected-apps-error" message={error} />
      )}

      <section
        aria-labelledby="pairing-heading"
        className="flex flex-col gap-3"
      >
        <h4 id="pairing-heading" className="text-md text-ink font-semibold">
          Pairing
        </h4>
        {connector === null ? (
          <p
            data-testid="region-loading"
            data-region="connected-apps"
            className="text-ink-muted text-sm"
          >
            Loading…
          </p>
        ) : !connector.available ? (
          <p className="text-ink-muted text-sm">
            Pairing is unavailable: {UNAVAILABLE_REASON[connector.reason]}
          </p>
        ) : issued !== null && issuedLeft > 0 ? (
          <div className="border-rule bg-surface-sunken flex flex-col gap-3 rounded-lg border p-4">
            <div className="flex flex-col gap-1">
              <span className="text-ink-muted text-sm">Connector address</span>
              <div className="flex items-center gap-1">
                <code
                  data-testid="connector-address"
                  className="text-ink text-sm break-all"
                >
                  {issued.address}
                </code>
                <CopyButton label="Copy address" text={issued.address} />
              </div>
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-ink-muted text-sm">Pairing code</span>
              <div className="flex items-center gap-1">
                <code
                  data-testid="pairing-code"
                  className="text-ink text-2xl font-semibold tracking-widest"
                >
                  {issued.code}
                </code>
                <CopyButton label="Copy code" text={issued.code} />
              </div>
              <p
                data-testid="pairing-countdown"
                className="text-ink-muted text-sm tabular-nums"
              >
                Expires in {formatRemaining(issuedLeft)}
              </p>
            </div>
            <ol className="text-ink list-decimal space-y-1 pl-5 text-sm">
              <li>In Claude, add a custom connector with this address.</li>
              <li>Press Connect.</li>
              <li>On the VisitDoc page that opens, enter this code.</li>
            </ol>
          </div>
        ) : (
          <div className="flex flex-col items-start gap-2">
            {issued !== null && (
              <p className="text-ink text-sm">Code expired</p>
            )}
            {issued === null && active !== null && activeLeft > 0 && (
              <p className="text-ink-muted text-sm tabular-nums">
                A code is active (expires in {formatRemaining(activeLeft)}). Get
                a new one to see it.
              </p>
            )}
            <Button onClick={getCode} disabled={issuing}>
              Get pairing code
            </Button>
          </div>
        )}
      </section>

      <section aria-labelledby="paired-heading" className="flex flex-col gap-2">
        <h4 id="paired-heading" className="text-md text-ink font-semibold">
          Paired apps
        </h4>
        {listing !== null &&
          (listing.grants.length === 0 ? (
            <p className="text-ink-muted text-sm">
              No apps are paired with this console.
            </p>
          ) : (
            <ul>
              {listing.grants.map((app) => (
                <PairedApp key={app.id} app={app} onRevoke={setRevoking} />
              ))}
            </ul>
          ))}
      </section>
      <RevokeDialog
        app={revoking}
        onCancel={() => setRevoking(null)}
        onConfirm={revoke}
      />
    </div>
  );
}

export default ConnectedApps;
