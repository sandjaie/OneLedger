// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { InvestmentEventForm } from "@/app/investments/investment-event-form";
import type { apiFetch } from "@/lib/api/client";
import type { PortfolioPositions } from "@/types/api";

type Request = typeof apiFetch;

const HOLDING = { accountId: "00000000-0000-4000-8000-000000000010", securityId: "00000000-0000-4000-8000-000000000040", securityName: "Synthetic Growth Fund", currency: "XAA" };
const EMPTY_POSITIONS: PortfolioPositions = { asOf: "2026-02-06", baseCurrency: "XAA", secondaryCurrency: "XBB", positions: [] };

function requestFrom(handler: (path: string, init?: RequestInit) => unknown | Promise<unknown>): Request {
  return vi.fn(async (path: string, init?: RequestInit) => handler(path, init)) as Request;
}

afterEach(cleanup);

describe("InvestmentEventForm", () => {
  it("shows only the fields a cash distribution allows and sends exact strings, null for the rest", async () => {
    const posted: Array<unknown> = [];
    const request = requestFrom((path, init) => {
      if (path === "/portfolio/positions") return EMPTY_POSITIONS;
      if (path === "/investment-events/cash-lifecycle" && init?.method === "POST") {
        posted.push(JSON.parse(String(init.body)));
        return { id: "evt-1" };
      }
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(<InvestmentEventForm accounts={[]} holding={HOLDING} onRecorded={vi.fn()} request={request} />);
    fireEvent.change(await screen.findByLabelText("Event kind"), { target: { value: "cash_distribution" } });

    expect(screen.queryByLabelText("Units")).toBeNull();
    expect(screen.queryByLabelText("Event price")).toBeNull();
    expect(screen.getByLabelText("Gross amount (XAA)")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Event date"), { target: { value: "2026-02-10" } });
    fireEvent.change(screen.getByLabelText("Gross amount (XAA)"), { target: { value: "5.0000" } });
    fireEvent.change(screen.getByLabelText("Charges (XAA)"), { target: { value: "0.0000" } });
    fireEvent.change(screen.getByLabelText("Withheld tax (XAA)"), { target: { value: "0.5000" } });
    fireEvent.change(screen.getByLabelText("Net cash amount (XAA)"), { target: { value: "4.5000" } });
    fireEvent.click(screen.getByRole("button", { name: "Review event" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm event" }));

    await vi.waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({
      accountId: HOLDING.accountId, securityId: HOLDING.securityId, eventDate: "2026-02-10",
      eventKind: "cash_distribution", units: null, price: null, grossAmount: "5.0000",
      charges: "0.0000", withheldTax: "0.5000", netAmount: "4.5000", currency: "XAA",
    });
  });

  it("shows only bonus's own units field and posts to the unit-actions route", async () => {
    const posted: Array<unknown> = [];
    const request = requestFrom((path, init) => {
      if (path === "/portfolio/positions") return EMPTY_POSITIONS;
      if (path === "/investment-events/unit-actions" && init?.method === "POST") {
        posted.push(JSON.parse(String(init.body)));
        return { kind: "bonus" };
      }
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(<InvestmentEventForm accounts={[]} holding={HOLDING} onRecorded={vi.fn()} request={request} />);
    fireEvent.change(await screen.findByLabelText("Event kind"), { target: { value: "bonus" } });

    expect(screen.queryByLabelText("Charges (XAA)")).toBeNull();
    fireEvent.change(screen.getByLabelText("Event date"), { target: { value: "2026-02-10" } });
    fireEvent.change(screen.getByLabelText("Bonus units"), { target: { value: "2.00000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Review event" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm event" }));

    await vi.waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toEqual({
      kind: "bonus", accountId: HOLDING.accountId, securityId: HOLDING.securityId, eventDate: "2026-02-10",
      units: "2.00000000", currency: "XAA", idempotencyKey: expect.any(String),
    });
  });

  it("reuses the same idempotency key on a retry and creates a new one after editing a field", async () => {
    const posted: Array<{ idempotencyKey: string }> = [];
    let attempts = 0;
    const request = requestFrom((path, init) => {
      if (path === "/portfolio/positions") return EMPTY_POSITIONS;
      if (path === "/investment-events/unit-actions" && init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as { idempotencyKey: string };
        posted.push(body);
        attempts += 1;
        if (attempts < 2) throw new Error("network hiccup");
        return { kind: "bonus" };
      }
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(<InvestmentEventForm accounts={[]} holding={HOLDING} onRecorded={vi.fn()} request={request} />);
    fireEvent.change(await screen.findByLabelText("Event kind"), { target: { value: "bonus" } });
    fireEvent.change(screen.getByLabelText("Event date"), { target: { value: "2026-02-10" } });
    fireEvent.change(screen.getByLabelText("Bonus units"), { target: { value: "2.00000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Review event" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm event" }));

    await screen.findByRole("button", { name: "Retry event" });
    fireEvent.click(screen.getByRole("button", { name: "Retry event" }));
    await vi.waitFor(() => expect(posted).toHaveLength(2));
    expect(posted[1].idempotencyKey).toBe(posted[0].idempotencyKey);

    // A successful submit already reset the draft; a fresh entry is a new draft and gets a new key.
    fireEvent.change(await screen.findByLabelText("Event kind"), { target: { value: "bonus" } });
    fireEvent.change(screen.getByLabelText("Event date"), { target: { value: "2026-02-11" } });
    fireEvent.change(screen.getByLabelText("Bonus units"), { target: { value: "3.00000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Review event" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm event" }));
    await vi.waitFor(() => expect(posted).toHaveLength(3));
    expect(posted[2].idempotencyKey).not.toBe(posted[0].idempotencyKey);
  });
});
