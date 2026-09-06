// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Workspace } from "@/app/workspace";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type { HouseholdResponse } from "@/types/api";

const household: HouseholdResponse = {
  id: "synthetic-owner",
  baseCurrency: "XIN",
  secondaryCurrency: "XAE",
  currencies: [
    { code: "XIN", symbol: "₹", decimals: 2, numberFormat: "indian" },
    { code: "XAE", symbol: "AED", decimals: 2, numberFormat: "international" },
  ],
};

type Request = typeof apiFetch;

function requestThat(...results: Array<unknown | Error>): Request {
  const request = vi.fn(async () => {
    const result = results.shift();
    if (result instanceof Error) throw result;
    return result;
  });
  return request as Request;
}

describe("authenticated workspace", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("guards owner content with the household request and labels unavailable modules", async () => {
    let resolve!: (value: HouseholdResponse) => void;
    const request = vi.fn(() => new Promise<HouseholdResponse>((done) => (resolve = done))) as Request;

    render(<Workspace request={request} navigate={vi.fn()} />);
    expect(screen.getByText("Checking owner access…")).toBeTruthy();
    expect(screen.queryByText("Reporting currency")).toBeNull();

    resolve(household);
    expect(await screen.findByText("Reporting currency")).toBeTruthy();
    expect(screen.getByText("₹12,34,567.89")).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "Workspace" })).toBeTruthy();
    expect(screen.getByRole<HTMLButtonElement>("button", { name: /Settings.*unavailable/i }).disabled).toBe(true);
  });

  it.each([
    [401, "/login"], [403, "/unauthorized"],
  ])("routes a protected %i response to %s", async (status, destination) => {
    const navigate = vi.fn();
    render(
      <Workspace
        request={requestThat(new ApiError(status, "synthetic"))}
        navigate={navigate}
      />,
    );
    await waitFor(() => expect(navigate).toHaveBeenCalledWith(destination));
    expect(screen.queryByText("Reporting currency")).toBeNull();
  });

  it("offers retry after a service failure instead of exposing owner content", async () => {
    const request = requestThat(new TypeError("offline"), household);
    render(<Workspace request={request} navigate={vi.fn()} />);

    expect(await screen.findByRole("heading", { name: "OneLedger is unavailable" })).toBeTruthy();
    expect(screen.queryByText("Reporting currency")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("₹12,34,567.89")).toBeTruthy();
  });

  it("keeps the owner view on transient logout failure and clears it after retry succeeds", async () => {
    const navigate = vi.fn();
    const request = requestThat(household, new TypeError("offline"), undefined);
    render(<Workspace request={request} navigate={navigate} />);
    await screen.findByText("₹12,34,567.89");

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect((await screen.findByRole("alert")).textContent).toContain("could not confirm");
    expect(screen.getByText("₹12,34,567.89")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Retry sign out" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith("/login"));
    expect(screen.queryByText("₹12,34,567.89")).toBeNull();
    expect(screen.queryByRole("button", { name: "Sign out" })).toBeNull();
    expect(request).toHaveBeenLastCalledWith("/auth/logout", { method: "POST" });
  });

  it.each([
    [401, "/login"], [403, "/unauthorized"],
  ])("clears the owner view when logout returns %i and routes to %s", async (status, destination) => {
    const navigate = vi.fn();
    const request = requestThat(household, new ApiError(status, "synthetic"));
    render(<Workspace request={request} navigate={navigate} />);
    await screen.findByText("₹12,34,567.89");

    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith(destination));
    expect(screen.queryByText("₹12,34,567.89")).toBeNull();
    expect(screen.queryByRole("button", { name: "Sign out" })).toBeNull();
  });

  it("persists the configured toggle, falls back from obsolete choices, and tolerates storage errors", async () => {
    localStorage.setItem("oneledger.reportingCurrency", "obsolete");
    const first = render(<Workspace request={requestThat(household)} navigate={vi.fn()} />);
    expect(await screen.findByText("₹12,34,567.89")).toBeTruthy();

    const secondary = screen.getByRole("button", { name: "XAE" });
    expect(secondary.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(secondary);
    expect(screen.getByText("AED 1,234,567.89")).toBeTruthy();
    expect(localStorage.getItem("oneledger.reportingCurrency")).toBe("XAE");

    first.unmount();
    render(<Workspace request={requestThat(household)} navigate={vi.fn()} />);
    expect(await screen.findByText("AED 1,234,567.89")).toBeTruthy();

    cleanup();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    render(<Workspace request={requestThat(household)} navigate={vi.fn()} />);
    expect(await screen.findByText("₹12,34,567.89")).toBeTruthy();
  });
});
