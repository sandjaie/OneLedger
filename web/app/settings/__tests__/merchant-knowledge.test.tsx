/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MerchantKnowledge } from "@/app/settings/merchant-knowledge";
import type { apiFetch } from "@/lib/api/client";
import type { CategoryRow, KnowledgeRow } from "@/types/api";

type Request = typeof apiFetch;

const CATEGORIES = [
  {
    id: "cat-groceries",
    parentId: null,
    name: "Groceries",
    slug: "groceries",
    kind: "expense",
    bucket: "need",
    sortOrder: 10,
    isSystem: true,
    isActive: true,
  },
] as Array<CategoryRow>;

function row(changes: Partial<KnowledgeRow> = {}): KnowledgeRow {
  return {
    aliasId: "alias-1",
    counterpartyId: "counterparty-1",
    knowledgeVersionId: "version-1",
    knowledgeId: "knowledge-1",
    version: 1,
    displayName: "Synthetic Market",
    kind: "merchant",
    merchantId: "merchant-1",
    merchantName: "Synthetic Market",
    ownAccountId: null,
    pattern: "SYNTHETIC MARKET",
    patternKind: "contains",
    role: "purchase",
    priority: 50,
    sourceKind: "owner",
    sourceUrl: null,
    attribution: "household owner",
    evidenceNote: "confirmed synthetic merchant",
    matcherVersion: "owner-v1",
    verifiedAt: null,
    direction: "debit",
    accountId: null,
    categoryId: "cat-groceries",
    txnType: "expense",
    paymentMethod: "card",
    active: true,
    createdAt: "2026-09-01T00:00:00Z",
    ...changes,
  } as KnowledgeRow;
}

afterEach(cleanup);

describe("Settings > Merchant knowledge", () => {
  it("lists active knowledge and describes what each row does", async () => {
    const request = vi.fn(async (path: string) => {
      if (path === "/merchant-knowledge") return [row()];
      throw new Error(`Unexpected path ${path}`);
    }) as Request;
    render(<MerchantKnowledge categories={CATEGORIES} request={request} />);

    expect(await screen.findByText("Synthetic Market")).toBeTruthy();
    expect(screen.getByText(/SYNTHETIC MARKET \(contains\) → Groceries/)).toBeTruthy();
    expect(screen.getByText("Active")).toBeTruthy();
  });

  it("reloads with includeInactive when the owner asks to see it", async () => {
    const request = vi.fn(async (path: string) => {
      if (path === "/merchant-knowledge") return [row()];
      if (path === "/merchant-knowledge?includeInactive=true") {
        return [row(), row({ aliasId: "alias-2", active: false })];
      }
      throw new Error(`Unexpected path ${path}`);
    }) as Request;
    render(<MerchantKnowledge categories={CATEGORIES} request={request} />);
    await screen.findByText("Synthetic Market");

    fireEvent.click(screen.getByRole("checkbox", { name: /Show inactive/ }));

    expect(await screen.findByText("Inactive")).toBeTruthy();
  });

  it("edits through PATCH with the full body and merchantName forced to null", async () => {
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    const request = (async (path: string, init?: RequestInit) => {
      calls.push({ path, init });
      if (path === "/merchant-knowledge") return [row()];
      if (path === "/merchant-knowledge/alias-1" && init?.method === "PATCH") {
        return row({ categoryId: "cat-groceries", priority: 75 });
      }
      throw new Error(`Unexpected path ${path}`);
    }) as Request;
    render(<MerchantKnowledge categories={CATEGORIES} request={request} />);
    await screen.findByText("Synthetic Market");

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    fireEvent.change(screen.getByLabelText("Priority"), { target: { value: "75" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    const call = await vi.waitFor(() => {
      const found = calls.find(({ path }) => path === "/merchant-knowledge/alias-1");
      if (!found) throw new Error("PATCH not called yet");
      return found;
    });
    expect(JSON.parse(call.init?.body as string)).toMatchObject({
      displayName: "Synthetic Market",
      kind: "merchant",
      merchantId: "merchant-1",
      merchantName: null,
      pattern: "SYNTHETIC MARKET",
      priority: "75",
      categoryId: "cat-groceries",
    });
  });

  it("deactivates a row through DELETE and reloads", async () => {
    let deleted = false;
    const request = vi.fn(async (path: string, init?: RequestInit) => {
      if (path === "/merchant-knowledge" && !deleted) return [row()];
      if (path === "/merchant-knowledge" && deleted) return [];
      if (path === "/merchant-knowledge/alias-1" && init?.method === "DELETE") {
        deleted = true;
        return undefined;
      }
      throw new Error(`Unexpected path ${path}`);
    }) as Request;
    render(<MerchantKnowledge categories={CATEGORIES} request={request} />);
    await screen.findByText("Synthetic Market");

    fireEvent.click(screen.getByRole("button", { name: "Remove" }));

    expect(await screen.findByText("No remembered merchant knowledge yet.")).toBeTruthy();
  });
});
