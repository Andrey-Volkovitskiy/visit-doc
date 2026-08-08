export interface Citation {
  entry_id: number;
  chunk_index: number;
  chunk_text: string;
}

export interface ChatTokenEvent {
  type: "token";
  text: string;
}

export interface ChatDoneEvent {
  type: "done";
  grounded: boolean;
  citations: Citation[];
  message?: string;
}

export interface ChatCancelledEvent {
  type: "cancelled";
}

export type ChatEvent = ChatTokenEvent | ChatDoneEvent | ChatCancelledEvent;

export interface Message {
  id: string;
  sender: "patient" | "assistant";
  content: string;
  grounded: boolean | null;
  citations: Citation[] | null;
  created_at: string;
}

/** Parse a POST /chat NDJSON response into its stream of events. */
export async function* parseNdjsonStream(response: Response): AsyncGenerator<ChatEvent> {
  if (!response.body) {
    throw new Error("Response has no body");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let newlineIndex = buffer.indexOf("\n");
    while (newlineIndex !== -1) {
      const line = buffer.slice(0, newlineIndex);
      buffer = buffer.slice(newlineIndex + 1);
      if (line.trim() !== "") {
        yield JSON.parse(line) as ChatEvent;
      }
      newlineIndex = buffer.indexOf("\n");
    }
  }

  if (buffer.trim() !== "") {
    yield JSON.parse(buffer) as ChatEvent;
  }
}

/** POST `message` to /chat and return its parsed NDJSON event stream. */
export async function askChat(
  message: string,
  signal?: AbortSignal,
): Promise<AsyncGenerator<ChatEvent>> {
  const response = await fetch("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
    signal,
  });
  return parseNdjsonStream(response);
}

/** GET /chat and return the visitor's chat history, chronological (FR-002). */
export async function fetchChatHistory(): Promise<Message[]> {
  const response = await fetch("/chat");
  const data = (await response.json()) as { messages: Message[] };
  return data.messages;
}

/** DELETE /chat: permanently clear the visitor's current chat (FR-004/FR-005). */
export async function clearChat(): Promise<void> {
  await fetch("/chat", { method: "DELETE" });
}
