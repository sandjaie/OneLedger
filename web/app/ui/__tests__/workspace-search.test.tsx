// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { WorkspaceSearch } from "@/app/ui/workspace-search";
import type { apiFetch } from "@/lib/api/client";

type Request = typeof apiFetch;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

const atlas = {
  results: [
    { kind: "account", resourceId: "a1", primaryLabel: "Atlas Reserve", secondaryLabel: "Synthetic Bank", route: "/accounts", date: null, status: "active" },
    { kind: "transaction", resourceId: "t1", primaryLabel: "Atlas payment", secondaryLabel: "Reserve · expense", route: "/transactions?transactionId=t1", date: "2026-02-03", status: null },
  ],
};

describe("workspace search", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it("does not request a short trimmed query and clears earlier results", async () => {
    const request = vi.fn().mockResolvedValue(atlas) as Request;
    render(<WorkspaceSearch navigate={vi.fn()} request={request} />);
    const input = screen.getByRole("combobox", { name: "Search OneLedger" });

    fireEvent.change(input, { target: { value: "Atlas" } });
    await act(() => vi.advanceTimersByTimeAsync(250));
    expect(screen.getByText("Atlas Reserve")).toBeTruthy();

    fireEvent.change(input, { target: { value: " x " } });
    await act(() => vi.advanceTimersByTimeAsync(250));
    expect(request).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Atlas Reserve")).toBeNull();
  });

  it("groups results, supports keyboard selection, and returns focus after Escape", async () => {
    const navigate = vi.fn();
    render(<WorkspaceSearch navigate={navigate} request={vi.fn().mockResolvedValue(atlas) as Request} />);
    const input = screen.getByRole("combobox", { name: "Search OneLedger" });
    fireEvent.change(input, { target: { value: "Atlas" } });
    await act(() => vi.advanceTimersByTimeAsync(250));

    const results = screen.getByRole("listbox", { name: "Search results" });
    expect(within(results).getByRole("group", { name: "Accounts" })).toBeTruthy();
    expect(within(results).getByRole("group", { name: "Transactions" })).toBeTruthy();
    expect(document.querySelector("[aria-live='polite']")?.textContent).toContain("2 results");

    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(navigate).toHaveBeenCalledWith("/transactions?transactionId=t1");

    fireEvent.change(input, { target: { value: "Atlas" } });
    await act(() => vi.advanceTimersByTimeAsync(250));
    fireEvent.keyDown(input, { key: "Escape" });
    expect(document.activeElement).toBe(input);
    expect(screen.queryByRole("listbox", { name: "Search results" })).toBeNull();
    expect((input as HTMLInputElement).value).toBe("");
  });

  it("does not let a stale response replace the latest query", async () => {
    const old = deferred<typeof atlas>();
    const latest = deferred<typeof atlas>();
    const request = vi.fn()
      .mockReturnValueOnce(old.promise)
      .mockReturnValueOnce(latest.promise) as Request;
    render(<WorkspaceSearch navigate={vi.fn()} request={request} />);
    const input = screen.getByRole("combobox", { name: "Search OneLedger" });

    fireEvent.change(input, { target: { value: "Atlas" } });
    await act(() => vi.advanceTimersByTimeAsync(250));
    fireEvent.change(input, { target: { value: "Beacon" } });
    await act(() => vi.advanceTimersByTimeAsync(250));

    await act(async () => latest.resolve({ results: [{ ...atlas.results[0], resourceId: "b1", primaryLabel: "Beacon Reserve" }] }));
    expect(screen.getByText("Beacon Reserve")).toBeTruthy();
    await act(async () => old.resolve(atlas));
    expect(screen.queryByText("Atlas Reserve")).toBeNull();
    expect(screen.getByText("Beacon Reserve")).toBeTruthy();
  });
});
