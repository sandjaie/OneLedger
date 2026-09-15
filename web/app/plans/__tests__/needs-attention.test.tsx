// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { NeedsAttention } from "@/app/plans/needs-attention";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type { AccountRow, ActionRow, ActionsResponse, CurrencyRow } from "@/types/api";

type Request = typeof apiFetch;

const currencies: Array<CurrencyRow> = [
  { code: "XAA", name: "Synthetic Currency", symbol: "A", decimals: 2, numberFormat: "international" },
];

const account: AccountRow = {
  id: "00000000-0000-4000-8000-0000000000a1",
  name: "Synthetic account",
  institutionId: null,
  institutionName: null,
  accountType: "savings",
  accountClass: "asset",
  accountSubtype: null,
  currency: "XAA",
  country: "XZ",
  openedOn: "2026-01-01",
  closedOn: null,
  isActive: true,
  includeInNetWorth: true,
  isLiquid: true,
  openingBalance: "10000.0000",
  openingBalanceDate: "2026-01-01",
  openingBalanceNote: null,
  openingBalanceConfirmedAt: null,
  currentBalance: "10000.0000",
  currentBalanceAsOf: "2026-01-01T00:00:00Z",
  creditLimit: null,
  identifiers: [],
};

const commitmentItem: ActionRow = {
  actionKey: "commitment.00000000-0000-4000-8000-0000000000c1.2026-02-01",
  kind: "commitment",
  reason: "commitment_due",
  title: "Synthetic rent",
  dueDate: "2026-02-01",
  overdue: false,
  snoozedUntil: null,
  accountId: account.id,
  accountName: account.name,
  amount: "-1000.0000",
  remaining: "-1000.0000",
  partlyPaid: false,
  currency: "XAA",
  certainty: "confirmed",
  commitmentId: "00000000-0000-4000-8000-0000000000c1",
  cardStatementId: null,
  actionItemId: null,
};

const cardItem: ActionRow = {
  actionKey: "card.00000000-0000-4000-8000-0000000000s1",
  kind: "card_due",
  reason: "card_payment_unplanned",
  title: "Synthetic card",
  dueDate: "2026-02-10",
  overdue: false,
  snoozedUntil: null,
  accountId: "00000000-0000-4000-8000-0000000000cc",
  accountName: "Synthetic card",
  amount: "400.0000",
  remaining: "400.0000",
  partlyPaid: false,
  currency: "XAA",
  certainty: null,
  commitmentId: null,
  cardStatementId: "00000000-0000-4000-8000-0000000000s1",
  actionItemId: null,
};

const followUpItem: ActionRow = {
  actionKey: "follow-up.00000000-0000-4000-8000-0000000000f1",
  kind: "follow_up",
  reason: "follow_up",
  title: "Synthetic follow-up",
  dueDate: null,
  overdue: false,
  snoozedUntil: null,
  accountId: null,
  accountName: null,
  amount: null,
  remaining: null,
  partlyPaid: false,
  currency: null,
  certainty: null,
  commitmentId: null,
  cardStatementId: null,
  actionItemId: "00000000-0000-4000-8000-0000000000f1",
};

function emptyResponse(): ActionsResponse {
  return { asOf: "2026-02-01", items: [], snoozed: [], resolvedFollowUps: [] };
}

function requestFrom(handler: (path: string, init?: RequestInit) => unknown | Promise<unknown>): Request {
  return vi.fn(async (path: string, init?: RequestInit) => handler(path, init)) as Request;
}

const noop = () => {};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("needs attention", () => {
  it("shows the empty state when nothing needs attention", async () => {
    const request = requestFrom((path) => {
      if (path === "/actions") return emptyResponse();
      throw new ApiError(500, "unexpected");
    });
    render(
      <NeedsAttention
        accounts={[account]}
        currencies={currencies}
        onCommitmentResolved={noop}
        onPlanCardPayment={noop}
        onSettle={noop}
        request={request}
      />,
    );
    expect(await screen.findByText("Nothing needs attention right now.")).toBeTruthy();
  });

  it("shows the error state and lets the owner try again", async () => {
    const request = requestFrom(() => {
      throw new ApiError(500, "boom");
    });
    render(
      <NeedsAttention
        accounts={[account]}
        currencies={currencies}
        onCommitmentResolved={noop}
        onPlanCardPayment={noop}
        onSettle={noop}
        request={request}
      />,
    );
    expect(await screen.findByText("Needs attention could not be loaded.")).toBeTruthy();
  });

  it("calls onSettle with the commitment id", async () => {
    const request = requestFrom((path) => {
      if (path === "/actions") return { ...emptyResponse(), items: [commitmentItem] };
      throw new ApiError(500, "unexpected");
    });
    const onSettle = vi.fn();
    render(
      <NeedsAttention
        accounts={[account]}
        currencies={currencies}
        onCommitmentResolved={noop}
        onPlanCardPayment={noop}
        onSettle={onSettle}
        request={request}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Settle" }));
    expect(onSettle).toHaveBeenCalledWith(commitmentItem.commitmentId);
  });

  it("marks a commitment item done and refetches, telling the parent to resync", async () => {
    let actionsCall = 0;
    const bodies: Array<Record<string, unknown>> = [];
    const request = requestFrom((path, init) => {
      if (path === "/actions") {
        actionsCall += 1;
        return { ...emptyResponse(), items: actionsCall === 1 ? [commitmentItem] : [] };
      }
      if (path === `/commitments/${commitmentItem.commitmentId}/resolutions` && init?.method === "POST") {
        bodies.push(JSON.parse(String(init.body)));
        return {};
      }
      throw new ApiError(500, "unexpected");
    });
    const onCommitmentResolved = vi.fn();
    render(
      <NeedsAttention
        accounts={[account]}
        currencies={currencies}
        onCommitmentResolved={onCommitmentResolved}
        onPlanCardPayment={noop}
        onSettle={noop}
        request={request}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Mark done" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toMatchObject({ dueDate: "2026-02-01", kind: "completed" });
    expect(onCommitmentResolved).toHaveBeenCalled();
    await waitFor(() => expect(actionsCall).toBe(2));
  });

  it("dismisses a commitment item with a reason", async () => {
    const bodies: Array<Record<string, unknown>> = [];
    const request = requestFrom((path, init) => {
      if (path === "/actions") return { ...emptyResponse(), items: [commitmentItem] };
      if (path === `/commitments/${commitmentItem.commitmentId}/resolutions` && init?.method === "POST") {
        bodies.push(JSON.parse(String(init.body)));
        return {};
      }
      throw new ApiError(500, "unexpected");
    });
    render(
      <NeedsAttention
        accounts={[account]}
        currencies={currencies}
        onCommitmentResolved={noop}
        onPlanCardPayment={noop}
        onSettle={noop}
        request={request}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Dismiss" }));
    fireEvent.change(screen.getByLabelText(`Reason to dismiss ${commitmentItem.title}`), {
      target: { value: "no longer owed" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm dismissal" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toMatchObject({
      dueDate: "2026-02-01",
      kind: "dismissed",
      reason: "no longer owed",
    });
  });

  it("snoozes an item until a chosen date", async () => {
    const bodies: Array<Record<string, unknown>> = [];
    const request = requestFrom((path, init) => {
      if (path === "/actions") return { ...emptyResponse(), items: [commitmentItem] };
      if (path === `/actions/marks/${commitmentItem.actionKey}` && init?.method === "PUT") {
        bodies.push(JSON.parse(String(init.body)));
        return undefined;
      }
      throw new ApiError(500, "unexpected");
    });
    render(
      <NeedsAttention
        accounts={[account]}
        currencies={currencies}
        onCommitmentResolved={noop}
        onPlanCardPayment={noop}
        onSettle={noop}
        request={request}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Snooze" }));
    fireEvent.change(screen.getByLabelText(`Snooze ${commitmentItem.title} until`), {
      target: { value: "2026-02-05" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Confirm snooze" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toMatchObject({ until: "2026-02-05" });
  });

  it("hands a card item to Plan payment and offers Dismiss without Mark done", async () => {
    const request = requestFrom((path) => {
      if (path === "/actions") return { ...emptyResponse(), items: [cardItem] };
      throw new ApiError(500, "unexpected");
    });
    const onPlanCardPayment = vi.fn();
    render(
      <NeedsAttention
        accounts={[account]}
        currencies={currencies}
        onCommitmentResolved={noop}
        onPlanCardPayment={onPlanCardPayment}
        onSettle={noop}
        request={request}
      />,
    );
    expect(await screen.findByRole("button", { name: "Plan payment" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Mark done" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Plan payment" }));
    expect(onPlanCardPayment).toHaveBeenCalledWith(cardItem);
  });

  it("completes, dismisses and reopens a follow-up", async () => {
    let actionsCall = 0;
    const request = requestFrom((path, init) => {
      if (path === "/actions") {
        actionsCall += 1;
        return { ...emptyResponse(), items: actionsCall === 1 ? [followUpItem] : [] };
      }
      if (
        path === `/actions/follow-ups/${followUpItem.actionItemId}/complete` &&
        init?.method === "POST"
      ) {
        return {};
      }
      throw new ApiError(500, "unexpected");
    });
    render(
      <NeedsAttention
        accounts={[account]}
        currencies={currencies}
        onCommitmentResolved={noop}
        onPlanCardPayment={noop}
        onSettle={noop}
        request={request}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Complete" }));
    await waitFor(() => expect(actionsCall).toBe(2));
  });

  it("adds a follow-up with the owner's title", async () => {
    const bodies: Array<Record<string, unknown>> = [];
    const request = requestFrom((path, init) => {
      if (path === "/actions") return emptyResponse();
      if (path === "/actions/follow-ups" && init?.method === "POST") {
        bodies.push(JSON.parse(String(init.body)));
        return {};
      }
      throw new ApiError(500, "unexpected");
    });
    render(
      <NeedsAttention
        accounts={[account]}
        currencies={currencies}
        onCommitmentResolved={noop}
        onPlanCardPayment={noop}
        onSettle={noop}
        request={request}
      />,
    );
    await screen.findByText("Nothing needs attention right now.");
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Call the bank" } });
    fireEvent.click(screen.getByRole("button", { name: "Add follow-up" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toMatchObject({ title: "Call the bank", dueDate: null, accountId: null });
  });

  it("shows snoozed items behind a toggle and wakes one", async () => {
    const snoozedItem: ActionRow = { ...commitmentItem, snoozedUntil: "2026-02-10" };
    const bodies: Array<string> = [];
    const request = requestFrom((path, init) => {
      if (path === "/actions") return { ...emptyResponse(), snoozed: [snoozedItem] };
      if (path === `/actions/marks/${snoozedItem.actionKey}` && init?.method === "DELETE") {
        bodies.push(path);
        return undefined;
      }
      throw new ApiError(500, "unexpected");
    });
    render(
      <NeedsAttention
        accounts={[account]}
        currencies={currencies}
        onCommitmentResolved={noop}
        onPlanCardPayment={noop}
        onSettle={noop}
        request={request}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: /Show snoozed/ }));
    expect(screen.getByText(/snoozed until 2026-02-10/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Wake" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
  });

  it("shows resolved follow-ups behind a toggle and reopens one", async () => {
    const resolved = {
      id: "00000000-0000-4000-8000-0000000000f2",
      title: "Synthetic resolved follow-up",
      dueDate: null,
      accountId: null,
      accountName: null,
      state: "completed" as const,
      dismissReason: null,
      resolvedAt: "2026-01-20T00:00:00Z",
      createdAt: "2026-01-01T00:00:00Z",
      updatedAt: "2026-01-20T00:00:00Z",
    };
    const bodies: Array<string> = [];
    const request = requestFrom((path, init) => {
      if (path === "/actions") return { ...emptyResponse(), resolvedFollowUps: [resolved] };
      if (path === `/actions/follow-ups/${resolved.id}/reopen` && init?.method === "POST") {
        bodies.push(path);
        return {};
      }
      throw new ApiError(500, "unexpected");
    });
    render(
      <NeedsAttention
        accounts={[account]}
        currencies={currencies}
        onCommitmentResolved={noop}
        onPlanCardPayment={noop}
        onSettle={noop}
        request={request}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: /Show recently resolved/ }));
    expect(screen.getByText(/Synthetic resolved follow-up/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Reopen" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
  });
});
