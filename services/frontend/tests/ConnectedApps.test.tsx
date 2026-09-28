import { act, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConnectedApps } from "../src/components/ConnectedApps";
import * as consoleApi from "../src/lib/consoleApi";
import type {
  ConnectedApp,
  ConnectedAppsListing,
  IssuedPairingCode,
} from "../src/lib/consoleApi";
import { press } from "./press";

const ADDRESS = "https://visitdoc.ngrok.app/mcp";

function listing(
  overrides: Partial<ConnectedAppsListing> = {},
): ConnectedAppsListing {
  return {
    connector: { available: true, address: ADDRESS },
    pairing_code: null,
    grants: [],
    ...overrides,
  };
}

function grant(overrides: Partial<ConnectedApp> = {}): ConnectedApp {
  return {
    id: "01GRANT0000000000000000000",
    client_name: "Claude",
    paired_seconds_ago: 7200,
    last_used_seconds_ago: 300,
    ...overrides,
  };
}

function issued(overrides: Partial<IssuedPairingCode> = {}): IssuedPairingCode {
  return {
    code: "K7QM-4XPD",
    expires_in_seconds: 600,
    address: ADDRESS,
    ...overrides,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("ConnectedApps: what the tab explains", () => {
  it("says what pairing an app gives, and that it reads counts only", async () => {
    vi.spyOn(consoleApi, "fetchConnectedApps").mockResolvedValue(listing());

    render(<ConnectedApps pollTick={1} />);

    // FR-002: shown whatever the connector's state, before anything is loaded.
    expect(
      screen.getByText(/Connect the Claude app to this console/),
    ).toHaveTextContent(/can read these two counts and nothing else/);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Get pairing code" }),
      ).toBeInTheDocument(),
    );
  });
});

describe("ConnectedApps: pairing unavailable (FR-004)", () => {
  it.each([
    ["not_configured", /no public address is configured \(PUBLIC_BASE_URL\)/],
    ["not_https", /not an https:\/\/ address/],
    ["has_path", /no path/],
  ] as const)(
    "names the reason %s and offers no button",
    async (reason, words) => {
      vi.spyOn(consoleApi, "fetchConnectedApps").mockResolvedValue(
        listing({ connector: { available: false, reason } }),
      );

      render(<ConnectedApps pollTick={1} />);

      await waitFor(() =>
        expect(screen.getByText(/Pairing is unavailable/)).toHaveTextContent(
          words,
        ),
      );
      expect(
        screen.queryByRole("button", { name: "Get pairing code" }),
      ).toBeNull();
    },
  );

  it("still lists the paired apps", async () => {
    vi.spyOn(consoleApi, "fetchConnectedApps").mockResolvedValue(
      listing({
        connector: { available: false, reason: "not_configured" },
        grants: [grant()],
      }),
    );

    render(<ConnectedApps pollTick={1} />);

    await waitFor(() =>
      expect(screen.getAllByTestId("connected-app")).toHaveLength(1),
    );
  });
});

describe("ConnectedApps: getting a code (FR-003)", () => {
  it("shows the code, the address, copy buttons and the countdown", async () => {
    vi.spyOn(consoleApi, "fetchConnectedApps").mockResolvedValue(listing());
    const issue = vi
      .spyOn(consoleApi, "issuePairingCode")
      .mockResolvedValue(issued());

    render(<ConnectedApps pollTick={1} />);
    press(await screen.findByRole("button", { name: "Get pairing code" }));

    await waitFor(() =>
      expect(screen.getByTestId("pairing-code")).toBeInTheDocument(),
    );
    expect(issue).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("pairing-code")).toHaveTextContent("K7QM-4XPD");
    expect(screen.getByTestId("connector-address")).toHaveTextContent(ADDRESS);
    expect(screen.getByTestId("pairing-countdown")).toHaveTextContent(
      "Expires in 10:00",
    );
    expect(
      screen.getByRole("button", { name: "Copy code" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Copy address" }),
    ).toBeInTheDocument();
    // The three steps, in order.
    const steps = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(steps).toHaveLength(3);
    expect(steps[0]).toMatch(/custom connector/i);
    expect(steps[1]).toMatch(/Connect/);
    expect(steps[2]).toMatch(/code/i);
    // The button gives way to the code while it is live.
    expect(
      screen.queryByRole("button", { name: "Get pairing code" }),
    ).toBeNull();
  });

  it("copies the code and the address", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    vi.spyOn(consoleApi, "fetchConnectedApps").mockResolvedValue(listing());
    vi.spyOn(consoleApi, "issuePairingCode").mockResolvedValue(issued());

    render(<ConnectedApps pollTick={1} />);
    press(await screen.findByRole("button", { name: "Get pairing code" }));
    press(await screen.findByRole("button", { name: "Copy code" }));
    press(screen.getByRole("button", { name: "Copy address" }));

    expect(writeText.mock.calls).toEqual([["K7QM-4XPD"], [ADDRESS]]);
  });

  it("counts down from the server's lifetime, then says the code expired", async () => {
    vi.spyOn(consoleApi, "fetchConnectedApps").mockResolvedValue(listing());
    vi.spyOn(consoleApi, "issuePairingCode").mockResolvedValue(
      issued({ expires_in_seconds: 90 }),
    );

    render(<ConnectedApps pollTick={1} />);
    const button = await screen.findByRole("button", {
      name: "Get pairing code",
    });
    vi.useFakeTimers();
    await act(async () => {
      press(button);
      await Promise.resolve();
    });
    expect(screen.getByTestId("pairing-countdown")).toHaveTextContent(
      "Expires in 1:30",
    );

    await act(async () => {
      vi.advanceTimersByTime(31_000);
    });
    expect(screen.getByTestId("pairing-countdown")).toHaveTextContent(
      "Expires in 0:59",
    );

    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    expect(screen.queryByTestId("pairing-code")).toBeNull();
    expect(screen.getByText("Code expired")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Get pairing code" }),
    ).toBeInTheDocument();
    // Nothing is left to count, so the page's clock has stopped ticking.
    expect(vi.getTimerCount()).toBe(0);
  });

  it("after a reload, says a code is active without showing one", async () => {
    vi.spyOn(consoleApi, "fetchConnectedApps").mockResolvedValue(
      listing({ pairing_code: { expires_in_seconds: 412 } }),
    );

    render(<ConnectedApps pollTick={1} />);

    await waitFor(() =>
      expect(
        screen.getByText(
          /A code is active \(expires in 6:52\)\. Get a new one to see it\./,
        ),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByTestId("pairing-code")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Get pairing code" }),
    ).toBeInTheDocument();
  });

  it("reports a failed request rather than showing nothing", async () => {
    vi.spyOn(consoleApi, "fetchConnectedApps").mockResolvedValue(listing());
    vi.spyOn(consoleApi, "issuePairingCode").mockRejectedValue(
      new Error("Could not get a pairing code. Please try again."),
    );

    render(<ConnectedApps pollTick={1} />);
    press(await screen.findByRole("button", { name: "Get pairing code" }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.queryByTestId("pairing-code")).toBeNull();
  });
});

describe("ConnectedApps: the paired apps (FR-005)", () => {
  it("lists each app with when it was paired and last used", async () => {
    vi.spyOn(consoleApi, "fetchConnectedApps").mockResolvedValue(
      listing({
        grants: [
          grant({
            id: "01A",
            paired_seconds_ago: 7200,
            last_used_seconds_ago: 300,
          }),
          grant({
            id: "01B",
            client_name: "Claude (phone)",
            paired_seconds_ago: 20,
            last_used_seconds_ago: null,
          }),
        ],
      }),
    );

    render(<ConnectedApps pollTick={1} />);

    const rows = await screen.findAllByTestId("connected-app");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Claude");
    expect(rows[0]).toHaveTextContent("Paired 2 h ago");
    expect(rows[0]).toHaveTextContent("Last used 5 min ago");
    expect(rows[1]).toHaveTextContent("Claude (phone)");
    expect(rows[1]).toHaveTextContent("Paired just now");
    expect(rows[1]).toHaveTextContent("Never used");
  });

  it("says when no app is paired rather than showing an empty table", async () => {
    vi.spyOn(consoleApi, "fetchConnectedApps").mockResolvedValue(listing());

    render(<ConnectedApps pollTick={1} />);

    await waitFor(() =>
      expect(
        screen.getByText("No apps are paired with this console."),
      ).toBeInTheDocument(),
    );
    expect(screen.queryAllByTestId("connected-app")).toHaveLength(0);
  });

  it("re-reads on the poll's tick, so a pairing made on the phone appears", async () => {
    const fetch = vi
      .spyOn(consoleApi, "fetchConnectedApps")
      .mockResolvedValueOnce(listing())
      .mockResolvedValue(listing({ grants: [grant()] }));

    const { rerender } = render(<ConnectedApps pollTick={1} />);
    await waitFor(() =>
      expect(
        screen.getByText("No apps are paired with this console."),
      ).toBeInTheDocument(),
    );

    rerender(<ConnectedApps pollTick={2} />);

    await waitFor(() =>
      expect(screen.getAllByTestId("connected-app")).toHaveLength(1),
    );
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(
      within(screen.getByTestId("connected-app")).getByText(/Claude/),
    ).toBeInTheDocument();
  });

  it("keeps a code on screen across a poll's re-read", async () => {
    vi.spyOn(consoleApi, "fetchConnectedApps").mockResolvedValue(
      listing({ pairing_code: { expires_in_seconds: 590 } }),
    );
    vi.spyOn(consoleApi, "issuePairingCode").mockResolvedValue(issued());

    const { rerender } = render(<ConnectedApps pollTick={1} />);
    press(await screen.findByRole("button", { name: "Get pairing code" }));
    await screen.findByTestId("pairing-code");

    rerender(<ConnectedApps pollTick={2} />);
    await waitFor(() =>
      expect(consoleApi.fetchConnectedApps).toHaveBeenCalledTimes(2),
    );

    expect(screen.getByTestId("pairing-code")).toHaveTextContent("K7QM-4XPD");
  });

  it("takes a code off the page once a re-read says it was used", async () => {
    // No live code after the phone spent it: the one on screen would be refused.
    vi.spyOn(consoleApi, "fetchConnectedApps").mockResolvedValue(listing());
    vi.spyOn(consoleApi, "issuePairingCode").mockResolvedValue(issued());

    const { rerender } = render(<ConnectedApps pollTick={1} />);
    press(await screen.findByRole("button", { name: "Get pairing code" }));
    await screen.findByTestId("pairing-code");

    rerender(<ConnectedApps pollTick={2} />);

    await waitFor(() => expect(screen.queryByTestId("pairing-code")).toBeNull());
    expect(
      screen.getByRole("button", { name: "Get pairing code" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Code expired")).toBeNull();
  });

  it("takes a code off the page once another tab replaced it", async () => {
    vi.spyOn(consoleApi, "fetchConnectedApps")
      .mockResolvedValueOnce(listing())
      .mockResolvedValue(listing({ pairing_code: { expires_in_seconds: 600 } }));
    vi.spyOn(consoleApi, "issuePairingCode").mockResolvedValue(
      issued({ expires_in_seconds: 300 }),
    );

    const { rerender } = render(<ConnectedApps pollTick={1} />);
    press(await screen.findByRole("button", { name: "Get pairing code" }));
    await screen.findByTestId("pairing-code");

    rerender(<ConnectedApps pollTick={2} />);

    await waitFor(() => expect(screen.queryByTestId("pairing-code")).toBeNull());
    expect(
      screen.getByText(/A code is active \(expires in 10:00\)/),
    ).toBeInTheDocument();
  });
});

describe("ConnectedApps: revoking an app (FR-021)", () => {
  it("asks first, then revokes and the row leaves", async () => {
    vi.spyOn(consoleApi, "fetchConnectedApps").mockResolvedValue(
      listing({
        grants: [
          grant({ id: "01KEEP", client_name: "Claude (laptop)" }),
          grant({ id: "01GONE", client_name: "Claude (phone)" }),
        ],
      }),
    );
    const revoke = vi
      .spyOn(consoleApi, "revokeConnectedApp")
      .mockResolvedValue(undefined);

    render(<ConnectedApps pollTick={1} />);
    const rows = await screen.findAllByTestId("connected-app");
    press(within(rows[1]).getByRole("button", { name: "Revoke" }));

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("Claude (phone)");
    expect(revoke).not.toHaveBeenCalled();
    press(within(dialog).getByRole("button", { name: "Revoke" }));

    await waitFor(() => expect(revoke).toHaveBeenCalledWith("01GONE"));
    await waitFor(() =>
      expect(screen.getAllByTestId("connected-app")).toHaveLength(1),
    );
    expect(screen.getByTestId("connected-app")).toHaveTextContent(
      "Claude (laptop)",
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("cancelling revokes nothing", async () => {
    vi.spyOn(consoleApi, "fetchConnectedApps").mockResolvedValue(
      listing({ grants: [grant()] }),
    );
    const revoke = vi
      .spyOn(consoleApi, "revokeConnectedApp")
      .mockResolvedValue(undefined);

    render(<ConnectedApps pollTick={1} />);
    press(
      within(await screen.findByTestId("connected-app")).getByRole("button", {
        name: "Revoke",
      }),
    );
    press(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Cancel",
      }),
    );

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(revoke).not.toHaveBeenCalled();
    expect(screen.getAllByTestId("connected-app")).toHaveLength(1);
  });

  it("keeps the row and reports it when the revoke fails", async () => {
    vi.spyOn(consoleApi, "fetchConnectedApps").mockResolvedValue(
      listing({ grants: [grant()] }),
    );
    vi.spyOn(consoleApi, "revokeConnectedApp").mockRejectedValue(
      new Error("Could not revoke that app. Please try again."),
    );

    render(<ConnectedApps pollTick={1} />);
    press(
      within(await screen.findByTestId("connected-app")).getByRole("button", {
        name: "Revoke",
      }),
    );
    press(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Revoke",
      }),
    );

    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
    expect(screen.getAllByTestId("connected-app")).toHaveLength(1);
  });

  it("keeps a failed revoke's report across a poll's re-read", async () => {
    const fetch = vi
      .spyOn(consoleApi, "fetchConnectedApps")
      .mockResolvedValue(listing({ grants: [grant()] }));
    vi.spyOn(consoleApi, "revokeConnectedApp").mockRejectedValue(
      new Error("Could not revoke that app. Please try again."),
    );

    const { rerender } = render(<ConnectedApps pollTick={1} />);
    press(
      within(await screen.findByTestId("connected-app")).getByRole("button", {
        name: "Revoke",
      }),
    );
    press(
      within(await screen.findByRole("dialog")).getByRole("button", {
        name: "Revoke",
      }),
    );
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());

    rerender(<ConnectedApps pollTick={2} />);
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Could not revoke that app.",
    );
  });
});
