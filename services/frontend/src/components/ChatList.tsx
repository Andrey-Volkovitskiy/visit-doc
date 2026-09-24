import { MoreHorizontal, Plus, X } from "lucide-react";
import { useState } from "react";
import type { ChatSummary } from "../lib/chatStream";
import { DeleteDialog } from "./DeleteDialog";
import { Button } from "./ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "./ui/dropdown-menu";

/**
 * How many chats are reachable as tabs without opening the overflow.
 *
 * Declared once and exported so its test references it rather than hard-coding a 4 in a
 * second place (FR-012). Deliberately *not* derived from a measurement of the rendered
 * strip: jsdom reports every element as having no width, so a measured rule could not be
 * exercised by the test tier at all — it would be a rule nothing checks.
 */
export const CHAT_TAB_BUDGET = 4;

/**
 * The label for a chat whose patient record does not exist yet.
 *
 * The server sends `patient_name: null` rather than inventing a placeholder, so the
 * label is built here from the chat's creation time — which is the only thing that
 * distinguishes one unnamed chat from another.
 *
 * This is one of the two places FR-010b permits a time on screen, and it is permitted
 * for that reason alone: it tells two otherwise identical labels apart and says nothing
 * about when anything happened.
 */
export function chatLabel(chat: ChatSummary): string {
  if (chat.patient_name !== null) return chat.patient_name;
  const created = new Date(chat.created_at);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `Unnamed · ${pad(created.getHours())}:${pad(created.getMinutes())}`;
}

/**
 * Split the chats into the ones shown as tabs and the ones behind the overflow.
 *
 * The server's order is kept (FR-012a) — it already puts the most recently active chat
 * first — with one exception: the open chat is always directly reachable, so when it
 * would fall past the budget it takes the last direct position and the tab it displaced
 * moves into the overflow, where it keeps its place in the server's order.
 *
 * Pure, and separate from the component, because it is the whole of FR-012a and a
 * reader should be able to see the rule without reading any markup.
 */
export function splitByBudget(
  chats: ChatSummary[],
  activeChatId: string | null,
): { direct: ChatSummary[]; overflow: ChatSummary[] } {
  if (chats.length <= CHAT_TAB_BUDGET) {
    return { direct: chats, overflow: [] };
  }
  const direct = chats.slice(0, CHAT_TAB_BUDGET);
  const overflow = chats.slice(CHAT_TAB_BUDGET);
  const activeIndex = chats.findIndex((c) => c.id === activeChatId);
  if (activeIndex < CHAT_TAB_BUDGET) {
    return { direct, overflow };
  }
  const active = chats[activeIndex]!;
  const displaced = direct[CHAT_TAB_BUDGET - 1]!;
  return {
    direct: [...direct.slice(0, CHAT_TAB_BUDGET - 1), active],
    // The displaced tab returns to the overflow at the position the server's order
    // gives it, rather than at the front: the overflow is the same list minus what is
    // on screen, not a history of what was pushed out of it.
    overflow: [displaced, ...overflow.filter((c) => c.id !== active.id)],
  };
}

interface ChatListProps {
  chats: ChatSummary[];
  activeChatId: string | null;
  onSelect: (chatId: string) => void;
  onCreate: () => void;
  onDelete: (chatId: string) => void;
}

export function ChatList({
  chats,
  activeChatId,
  onSelect,
  onCreate,
  onDelete,
}: ChatListProps) {
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const { direct, overflow } = splitByBudget(chats, activeChatId);
  const confirming = chats.find((c) => c.id === confirmingId) ?? null;

  return (
    <nav
      data-testid="chat-list"
      aria-label="Your chats"
      className="bg-bubble-them border-rule flex min-w-0 items-center gap-1 rounded-t-md border-b px-3 py-2"
    >
      <ul className="flex min-w-0 flex-1 items-center gap-1 overflow-hidden">
        {direct.map((chat) => {
          const label = chatLabel(chat);
          const isActive = chat.id === activeChatId;
          return (
            <li
              key={chat.id}
              data-testid="chat-list-item"
              data-chat-id={chat.id}
              className={[
                "flex min-w-0 max-w-[170px] items-center gap-2 rounded-md border px-2.5 py-1.5 text-sm",
                isActive
                  ? "bg-surface border-rule text-ink font-medium"
                  : "text-ink-muted border-transparent",
              ].join(" ")}
            >
              <button
                type="button"
                aria-current={isActive ? "true" : undefined}
                onClick={() => onSelect(chat.id)}
                className="min-w-0 truncate text-left"
              >
                {label}
              </button>
              <button
                type="button"
                aria-label={`Delete ${label}`}
                onClick={() => setConfirmingId(chat.id)}
                className="text-ink-muted hover:text-ink grid size-4.5 flex-none place-items-center rounded-xs"
              >
                <X className="size-3" aria-hidden="true" />
              </button>
            </li>
          );
        })}
      </ul>
      {overflow.length > 0 && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="outline"
              size="icon"
              data-testid="chat-overflow"
              aria-label={`More chats (${overflow.length})`}
              className="text-ink-muted hover:text-ink size-7.5 flex-none"
            >
              <MoreHorizontal aria-hidden="true" />
            </Button>
          </DropdownMenuTrigger>
          {/* Radix renders this into a portal on document.body, so a test reaches it
              with `screen.*` and never with `within(container)`. */}
          <DropdownMenuContent align="end">
            {overflow.map((chat) => (
              <DropdownMenuItem
                key={chat.id}
                data-testid="chat-overflow-item"
                onSelect={() => onSelect(chat.id)}
              >
                {chatLabel(chat)}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
      <Button
        variant="outline"
        size="icon"
        onClick={onCreate}
        aria-label="Start a new chat"
        className="border-accent/40 text-accent-dark hover:bg-accent-wash size-7.5 flex-none"
      >
        <Plus aria-hidden="true" />
      </Button>
      {/* The wording is unchanged from when this screen asked the question in its own
          markup: `contracts/tokens.md` draws the line at an error banner, "because a
          failed action is a thing needing a person", and a confirmation the reader
          asked for is not that. What moved is where it lives — `PractitionerAdmin` and
          `FaqAdmin` now ask the same question about their own records, and it is one
          component so the three cannot drift. */}
      <DeleteDialog
        open={confirming !== null}
        subject={confirming === null ? "this chat" : chatLabel(confirming)}
        onCancel={() => setConfirmingId(null)}
        onConfirm={() => {
          const target = confirmingId;
          setConfirmingId(null);
          if (target !== null) onDelete(target);
        }}
      >
        This deletes the chat, its messages, its patient, and that patient&apos;s
        appointments. Do you agree?
      </DeleteDialog>
    </nav>
  );
}

export default ChatList;
