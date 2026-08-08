import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ClearChatButton } from "../src/components/ClearChatButton";
import * as chatStream from "../src/lib/chatStream";

describe("ClearChatButton", () => {
  it("shows a confirmation dialog before clearing", () => {
    render(<ClearChatButton onCleared={() => {}} />);
    fireEvent.click(screen.getByText("Clear chat"));
    expect(
      screen.getByText("All messages in the chat will be deleted. Do you agree?"),
    ).toBeInTheDocument();
  });

  it("does not call DELETE /chat when cancelled", () => {
    const clearChatSpy = vi.spyOn(chatStream, "clearChat").mockResolvedValue(undefined);

    render(<ClearChatButton onCleared={() => {}} />);
    fireEvent.click(screen.getByText("Clear chat"));
    fireEvent.click(screen.getByText("Cancel"));

    expect(clearChatSpy).not.toHaveBeenCalled();
    expect(
      screen.queryByText("All messages in the chat will be deleted. Do you agree?"),
    ).toBeNull();
  });

  it("calls DELETE /chat and notifies the parent when confirmed", async () => {
    const clearChatSpy = vi.spyOn(chatStream, "clearChat").mockResolvedValue(undefined);
    const onCleared = vi.fn();

    render(<ClearChatButton onCleared={onCleared} />);
    fireEvent.click(screen.getByText("Clear chat"));
    fireEvent.click(screen.getByText("Clear"));

    await waitFor(() => {
      expect(clearChatSpy).toHaveBeenCalled();
    });
    expect(onCleared).toHaveBeenCalled();
  });
});
