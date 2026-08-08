import { useState } from "react";
import { clearChat } from "../lib/chatStream";

interface ClearChatButtonProps {
  onCleared: () => void;
}

/** Button + confirmation dialog for permanently clearing the chat (FR-004). */
export function ClearChatButton({ onCleared }: ClearChatButtonProps) {
  const [confirming, setConfirming] = useState(false);

  async function handleConfirm(): Promise<void> {
    setConfirming(false);
    await clearChat();
    onCleared();
  }

  return (
    <div>
      <button onClick={() => setConfirming(true)}>Clear chat</button>
      {confirming && (
        <div role="dialog">
          <p>All messages in the chat will be deleted. Do you agree?</p>
          <button onClick={() => void handleConfirm()}>Clear</button>
          <button onClick={() => setConfirming(false)}>Cancel</button>
        </div>
      )}
    </div>
  );
}

export default ClearChatButton;
