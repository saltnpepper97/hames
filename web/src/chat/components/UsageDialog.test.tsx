import { cleanup, fireEvent, render, screen, waitFor } from "@solidjs/testing-library";
import { afterEach, expect, it, vi } from "vitest";
import { getPooledUsage } from "../../api/client";
import type { SessionUsage } from "../../api/types";
import { UsageDashboard } from "./UsageDialog";

vi.mock("../../api/client", () => ({ getPooledUsage: vi.fn() }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("refreshes Grok after rollover and keeps a labeled stale reading visible", async () => {
  const usage: SessionUsage = {
    estimated_input_tokens: 0, input_tokens: 0, output_tokens: 0, cached_input_tokens: 0,
    reasoning_tokens: 0, provider_reported_cost: 0, model_requests: 0,
    latest_context: null, account_rate_limits: null, account_rate_limits_error: "",
    grok_account_configured: true,
    grok_account_usage: { label: "Weekly limit", observed_at: 1,
      window: { used: 100, remaining: 0, reset_at: "2026-01-01T00:00:00Z", window_minutes: null } },
  };
  const next = { ...usage, grok_account_usage: { ...usage.grok_account_usage!,
    window: { used: 0, remaining: 100, reset_at: "2099-01-01T00:00:00Z", window_minutes: null } } };
  vi.mocked(getPooledUsage).mockResolvedValueOnce(usage).mockResolvedValueOnce(next)
    .mockResolvedValueOnce({ ...next, grok_account_usage_error: "Showing last known Grok usage; refresh failed." });
  const { unmount } = render(() => <UsageDashboard />);
  await waitFor(() => expect(screen.getByRole("progressbar", { name: "Weekly limit" })).toHaveAttribute("aria-valuenow", "100"));
  fireEvent(window, new Event("focus"));
  await waitFor(() => expect(screen.getByRole("progressbar", { name: "Weekly limit" })).toHaveAttribute("aria-valuenow", "0"));
  expect(screen.getByText(/Resets in/)).toBeInTheDocument();
  fireEvent(window, new Event("focus"));
  expect(await screen.findByText(/Showing last known Grok usage/)).toBeInTheDocument();
  expect(screen.getByRole("progressbar", { name: "Weekly limit" })).toHaveAttribute("aria-valuenow", "0");
  unmount();
  fireEvent(window, new Event("focus"));
  expect(getPooledUsage).toHaveBeenCalledTimes(3);
});
