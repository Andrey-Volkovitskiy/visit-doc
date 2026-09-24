import { useCallback, useEffect, useRef, useState } from "react";
import { ChatList } from "./components/ChatList";
import { ChatWindow } from "./components/ChatWindow";
import { FaqAdmin } from "./components/FaqAdmin";
import { PractitionerAdmin } from "./components/PractitionerAdmin";
import { StaffConsole } from "./components/StaffConsole";
import { StaffThread } from "./components/StaffThread";
import { Button } from "./components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogTitle,
} from "./components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./components/ui/tabs";
import { setAssistant } from "./lib/consoleApi";
import { useConsolePoll } from "./lib/useConsolePoll";
import {
  createChat,
  deleteChat,
  fetchChats,
  type ChatListing,
  type ChatSummary,
} from "./lib/chatStream";

/**
 * A region whose content has not arrived yet (FR-010a).
 *
 * It says so in words and names itself, because "waiting", "arrived empty" and "failed"
 * call for three different things from the reader and must not look alike. Deliberately
 * not a placeholder shape: a skeleton asserts a shape the server has not confirmed, and
 * is wrong precisely when the answer turns out to be nothing.
 */
function RegionLoading({ region, children }: { region: string; children: string }) {
  return (
    <p
      data-testid="region-loading"
      data-region={region}
      className="text-ink-muted p-4 text-sm"
    >
      {children}
    </p>
  );
}

/** The product's mark: a speech bubble with a cross in it, drawn rather than fetched. */
function Wordmark() {
  return (
    <h1 className="text-lg flex items-center gap-2.5 font-semibold tracking-tight">
      <svg width="26" height="26" viewBox="0 0 26 26" aria-hidden="true" className="block flex-none">
        <rect x="1" y="1" width="24" height="24" rx="6" className="fill-accent" />
        <path
          d="M8 17.5V9.5a1 1 0 0 1 1-1h8a1 1 0 0 1 1 1v5a1 1 0 0 1-1 1h-5.2L8 18.6v-1.1z"
          className="fill-surface"
        />
        <path
          d="M13 10.4v3.2M11.4 12h3.2"
          className="stroke-accent"
          strokeWidth="1.5"
          strokeLinecap="round"
        />
      </svg>
      AI Clinic Receptionist
    </h1>
  );
}

function App() {
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const [staffChatId, setStaffChatId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Which console section is open (FR-020). Owned here rather than by the console,
  // because the attention count sits in the console header *outside* the tabbed region
  // (FR-021) and both are the header's concern.
  const [staffTab, setStaffTab] = useState("chats");
  // Which console section currently holds work that leaving would lose, and the tab a
  // switch was asked for while it did (FR-035b).
  //
  // The *back* control out of an edit view is guarded inside the section that owns the
  // form. A **tab** switch cannot be: Radix destroys the inactive panel to perform one,
  // so by the time the section could notice, the typed text is already gone. Only the
  // shell that performs the switch can hold it back — which is why these two live here
  // and nowhere else.
  const [dirtyTab, setDirtyTab] = useState<string | null>(null);
  const [pendingTab, setPendingTab] = useState<string | null>(null);
  // Whether the chat listing has answered at all — so a pane that is waiting can say so
  // rather than render an empty result (FR-010a). Not inferred from `chats.length`,
  // which cannot tell waiting from a session that genuinely holds no chats.
  const [chatsLoaded, setChatsLoaded] = useState(false);
  // Whether this browser has a session yet, which is the precondition for every
  // session-scoped panel below. The session cookie is HttpOnly, so the SPA cannot look:
  // this is only ever the server's own answer, either `session_exists` on a listing or
  // the fact that a POST /chats came back.
  //
  // Monotonic on purpose. A session is minted once and never withdrawn while the page is
  // open, so this latches true and is never set back from a later listing — a panel that
  // unmounted on one transient answer would throw away whatever a staff member had typed
  // into it, and remounting is not a refresh of anything.
  const [sessionExists, setSessionExists] = useState(false);
  // One poll, every pane: the staff list renders it, and both open threads refetch when
  // their conversation's newest message advances past what they hold — which is what
  // makes a staff reply appear in the patient's thread, and a patient message appear in
  // the staff member's, with no channel of its own to keep in step.
  const poll = useConsolePoll();
  const activeConversation = poll.conversations.find(
    (c) => c.chat_id === activeChatId,
  );
  const activeLastMessageAt = activeConversation?.last_message_at ?? null;
  const staffConversation = poll.conversations.find(
    (c) => c.chat_id === staffChatId,
  );

  // A section reports its own name with its flag, and may only clear its own. One
  // section is mounted at a time so they cannot currently collide — but a flag that any
  // section could clear is one a future second mounted section could clear on somebody
  // else's behalf, and the bug that produces is a prompt that silently stops appearing.
  const markDirty = useCallback((tab: string, dirty: boolean): void => {
    setDirtyTab((previous) =>
      dirty ? tab : previous === tab ? null : previous,
    );
  }, []);
  const markPractitionersDirty = useCallback(
    (dirty: boolean) => markDirty("practitioners", dirty),
    [markDirty],
  );
  const markFaqDirty = useCallback(
    (dirty: boolean) => markDirty("faq", dirty),
    [markDirty],
  );

  /**
   * Switch section, unless the one being left is holding unsaved work.
   *
   * FR-035b permits confirming or making the discard explicit, and forbids doing
   * neither. This confirms, matching the back control's own guard rather than inventing
   * a second answer to the same question, and gates on dirtiness so that opening a
   * practitioner to look at them and going elsewhere — the common case — is not
   * interrupted.
   */
  function requestStaffTab(next: string): void {
    if (next !== staffTab && dirtyTab === staffTab) {
      setPendingTab(next);
      return;
    }
    setStaffTab(next);
  }

  // The poll re-reads the switch's position a moment later anyway, so nothing is
  // cached from the response here - which is what stops two tabs disagreeing about a
  // conversation one of them just took.
  function handleSetAssistant(enabled: boolean): void {
    if (staffChatId === null) return;
    setError(null);
    void setAssistant(staffChatId, enabled).catch((err: unknown) => {
      setError(
        err instanceof Error
          ? err.message
          : "Could not change the assistant for this conversation.",
      );
    });
  }

  // The server returns the list already ordered - chats holding messages first,
  // newest message first - so "the chat I was last talking in" is simply the first
  // one, and the rule lives in one place rather than being re-derived per render.
  const load = useCallback(async (): Promise<ChatListing> => {
    const listing = await fetchChats();
    setChats(listing.chats);
    setChatsLoaded(true);
    return listing;
  }, []);

  const create = useCallback(async (): Promise<void> => {
    setError(null);
    // The response already *is* the new row, so the list is extended rather than
    // refetched — the server's ordering puts a brand-new, message-less chat first
    // among its peers, which is exactly where prepending puts it.
    const created = await createChat();
    // A returned chat proves a session: this call either minted one and set its cookie,
    // or was made under one that was already there. Either way the panels that need one
    // may now mount, and this is the only signal a first arrival ever gets — the
    // provisioning POST is the very thing that creates what they read.
    setSessionExists(true);
    setChats((prev) => [created, ...prev]);
    setActiveChatId(created.id);
  }, []);

  // A first arrival must provision exactly one session, and this effect can run twice
  // before either POST resolves — StrictMode double-invokes it in development, and both
  // requests would go out cookie-less and mint a separate session, chat, patient and
  // practitioner. Only the last `Set-Cookie` survives, stranding the other set.
  const provisioning = useRef(false);

  useEffect(() => {
    void load()
      .then((listing) => {
        // A first arrival is given a session, a chat, a patient, and a practitioner. An
        // *emptied* session looks identical from the list alone but must be left alone,
        // so the server's own answer decides it rather than a guess from `chats.length`.
        if (!listing.session_exists) {
          if (provisioning.current) return;
          provisioning.current = true;
          void create().catch((err: unknown) => {
            provisioning.current = false;
            setError(
              err instanceof Error ? err.message : "Could not start a chat.",
            );
          });
          return;
        }
        setSessionExists(true);
        setActiveChatId((current) => current ?? listing.chats[0]?.id ?? null);
      })
      .catch((err: unknown) => {
        // Without this the whole first paint fails silently: no chats, no active chat,
        // and ChatWindow's empty state showing indefinitely with nothing explaining it.
        setChatsLoaded(true);
        setError(
          err instanceof Error ? err.message : "Could not load your chats.",
        );
      });
  }, [load, create]);

  async function handleDelete(chatId: string): Promise<void> {
    setError(null);
    try {
      await deleteChat(chatId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete this chat.");
      return;
    }
    try {
      const remaining = await load();
      if (activeChatId === chatId) {
        // A session with zero chats is a valid state, so this legitimately lands on
        // null rather than provisioning a replacement.
        setActiveChatId(remaining.chats[0]?.id ?? null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load your chats.");
    }
  }

  // Both sides at once, with no way in and out: this is a single-visitor
  // demonstration, so the staff member and the patient are the same person in two
  // panes. There is no authentication in this phase and no prompt for one.
  //
  // Everything structural below renders unconditionally, before any request has
  // returned (FR-010a): the header, both panes, their headings and the tab set depend
  // on no answer, so waiting for one would leave the visitor looking at nothing.
  return (
    <>
      <header className="bg-surface border-rule border-b">
        <div className="mx-auto flex max-w-[1440px] items-center gap-3 px-6 py-4">
          <Wordmark />
        </div>
      </header>
      <main className="panes:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] mx-auto grid max-w-[1440px] grid-cols-1 items-start gap-4 p-4 sm:gap-6 sm:p-6">
        <section
          data-testid="patient-pane"
          aria-label="Patient messenger"
          className="bg-surface border-rule panes:h-[720px] flex h-[640px] min-w-0 flex-col rounded-md border"
        >
          <h2 className="border-rule-soft text-ink-muted border-b px-4 py-3 text-sm font-semibold">
            Patient messenger
          </h2>
          {/*
            The page-level error: failures that belong to the page rather than to a pane
            — loading or changing the chat list — reported once, here, and visually
            distinct from everything around it (FR-010).
          */}
          {error !== null && (
            <p
              data-testid="chat-list-error"
              role="alert"
              className="text-attention bg-attention-wash border-attention/30 m-3 rounded-md border px-3 py-2 text-sm"
            >
              {error}
            </p>
          )}
          {chatsLoaded ? (
            <ChatList
              chats={chats}
              activeChatId={activeChatId}
              onSelect={setActiveChatId}
              onCreate={() =>
                void create().catch((err: unknown) => {
                  setError(
                    err instanceof Error ? err.message : "Could not start a chat.",
                  );
                })
              }
              onDelete={(chatId) => void handleDelete(chatId)}
            />
          ) : (
            <RegionLoading region="chats">Loading your chats…</RegionLoading>
          )}
          <ChatWindow
            chatId={activeChatId}
            // Only the sidebar's ordering depends on a completed turn, and only when the
            // active chat was not already first — refetching the whole list after every
            // turn re-runs a join over every message in the session to learn that nothing
            // moved. A failed refresh leaves the order stale, which is not worth an error
            // banner over a reply the patient can already see.
            onTurnComplete={() => {
              if (activeChatId !== null && chats[0]?.id === activeChatId) return;
              void load().catch(() => undefined);
            }}
            lastMessageAt={activeLastMessageAt}
            // One more field off a row already in hand, and FR-017's only data path: no
            // endpoint, no request, no new state (research.md Decision 3). `?? true`
            // because a brand-new chat has no poll row yet.
            assistantMayReply={activeConversation?.assistant_may_reply ?? true}
            // And how many times that poll has answered, so a refetch of this thread that
            // failed is attempted again on the next tick rather than waiting on a value
            // that has already stopped changing.
            pollTick={poll.tick}
          />
        </section>
        <section
          data-testid="staff-pane"
          aria-label="Staff console"
          className="bg-surface border-rule panes:h-[720px] flex h-[640px] min-w-0 flex-col rounded-md border"
        >
          <div className="border-rule-soft flex items-center gap-3 border-b px-4 py-3">
            <h2 className="text-ink-muted text-sm font-semibold">Staff console</h2>
            {/*
              Outside the tabbed region on purpose (FR-021): inside it, the count would
              vanish the moment staff opened Practitioners, and a signal you have to
              navigate back to is not one. Rendered even at zero — a missing badge and a
              badge reading zero say different things.
            */}
            <p className="text-ink-muted ml-auto text-sm">
              Needs a person:{" "}
              <strong data-testid="attention-total" className="text-ink">
                {poll.attentionTotal}
              </strong>
            </p>
          </div>
          <Tabs
            value={staffTab}
            onValueChange={requestStaffTab}
            className="flex min-h-0 flex-1 flex-col gap-0"
          >
            {/*
              The gap and padding shrink below `sm`, and the strip scrolls rather than
              widening the page. Measured, not guessed: at 375px the three no-wrap
              labels plus `gap-6` and `px-4` came to one pixel more than the pane had,
              and one pixel of horizontal scroll is still a horizontal scrollbar
              (FR-009). `overflow-x-auto` is the part that holds for a label longer than
              these three.
            */}
            <TabsList className="bg-bubble-them border-rule w-full gap-3 overflow-x-auto border-b px-2 sm:gap-6 sm:px-4">
              <TabsTrigger value="chats">Conversations</TabsTrigger>
              <TabsTrigger value="practitioners">Practitioners</TabsTrigger>
              <TabsTrigger value="faq">FAQ</TabsTrigger>
            </TabsList>
            <TabsContent
              value="chats"
              className="panes:grid-cols-[208px_minmax(0,1fr)] grid min-h-0 grid-cols-1"
            >
              <StaffConsole
                conversations={poll.conversations}
                activeChatId={staffChatId}
                onSelect={setStaffChatId}
                loaded={poll.tick > 0}
              />
              <StaffThread
                chatId={staffChatId}
                assistantMayReply={staffConversation?.assistant_may_reply ?? true}
                pauseSecondsRemaining={
                  staffConversation?.pause_seconds_remaining ?? null
                }
                // The same poll, serving the staff side's open thread as it already serves
                // the patient's: a patient message arriving into the conversation a staff
                // member is reading appears there without them clicking away and back.
                lastMessageAt={staffConversation?.last_message_at ?? null}
                pollTick={poll.tick}
                onSetAssistant={handleSetAssistant}
              />
            </TabsContent>
            {/*
              Not rendered until a session exists, which is what makes the wrong state
              unrepresentable rather than recovered from. Each of these panels reads
              something owned by the session and fetches it once, on mount: their effect
              has no reason to run a second time, so a fetch made before the session
              existed is the only answer they would ever have. On a first arrival that
              answer is a 401 from GET /console/practitioners, and the panel went on
              showing "no session" over an empty roster while the session it was minted
              beside sat there holding practitioners.

              Withholding them until `sessionExists` means the mount that performs the
              fetch cannot happen too early — there is no early state to repair, no second
              fetch to schedule, and nothing in the network layer that has to know about
              any of this. FaqAdmin joins them not because it misbehaves today but because
              it has the identical shape: GET /faq answering 200-with-an-empty-list for a
              session-less caller is the endpoint's choice, and a panel that is only
              correct while that choice holds is a panel resting on someone else's status
              code.

              The tab makes that mount *later* as well as earlier — a panel opened for the
              first time five minutes in mounts then — which does not weaken the gate: a
              panel that mounts before a session exists still holds the refusal forever,
              and that is the whole of what this guards.
            */}
            <TabsContent value="practitioners" className="min-h-0 overflow-y-auto">
              {sessionExists ? (
                <PractitionerAdmin onDirtyChange={markPractitionersDirty} />
              ) : (
                <RegionLoading region="practitioners">
                  Waiting for this browser&apos;s session before reading the roster.
                </RegionLoading>
              )}
            </TabsContent>
            <TabsContent value="faq" className="min-h-0 overflow-y-auto">
              {sessionExists ? (
                <FaqAdmin onDirtyChange={markFaqDirty} />
              ) : (
                <RegionLoading region="faq">
                  Waiting for this browser&apos;s session before reading the FAQ.
                </RegionLoading>
              )}
            </TabsContent>
          </Tabs>
          {/*
            The tab-switch half of FR-035b. Same hook and the same wording as the back
            control's confirmation, because it is the same question — and the two can
            never be open at once, since one is raised by the back control and the other
            only by a switch away from the section that control lives in.
          */}
          <Dialog
            open={pendingTab !== null}
            onOpenChange={(open) => {
              if (!open) setPendingTab(null);
            }}
          >
            <DialogContent data-testid="discard-confirm">
              <DialogTitle>Leave without saving?</DialogTitle>
              <DialogDescription>
                What you typed has not been sent to the clinic&apos;s records. Opening
                another section discards it.
              </DialogDescription>
              <DialogFooter>
                <Button variant="outline" onClick={() => setPendingTab(null)}>
                  Keep editing
                </Button>
                <Button
                  onClick={() => {
                    const next = pendingTab;
                    setPendingTab(null);
                    // The flag is not cleared here. The section that set it is about to
                    // unmount and retract it itself, and clearing it from both places
                    // would mean two owners for one value — the second of which cannot
                    // see whether the first still needs it.
                    if (next !== null) setStaffTab(next);
                  }}
                >
                  Discard
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </section>
      </main>
    </>
  );
}

export default App;
