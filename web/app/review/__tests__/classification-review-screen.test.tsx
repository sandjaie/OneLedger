// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ClassificationReviewScreen } from "@/app/review/classification-review-screen";
import type {
  CategoryRow,
  ClassificationReviewGroup,
  ClassificationReviewResponse,
  KnowledgeRow,
  OneledgerRoutesClassificationDecisionsPreviewResult as PreviewResult,
  RunResult,
  RunRow,
} from "@/types/api";

const categories = [
  {
    id: "category-groceries",
    parentId: null,
    name: "Groceries",
    slug: "groceries",
    kind: "expense",
    bucket: "needs",
    sortOrder: 1,
    isSystem: true,
    isActive: true,
  },
] as Array<CategoryRow>;

function group(overrides: Partial<ClassificationReviewGroup> = {}): ClassificationReviewGroup {
  return {
    groupKey: "group-1",
    reason: "recognized_pending",
    explanation: "A saved household answer matches these rows.",
    accountId: "account-1",
    accountName: "Daily account",
    direction: "debit",
    description: "SAMPLE MARKET",
    occurrences: 3,
    firstBookedDate: "2026-08-01",
    lastBookedDate: "2026-09-01",
    transactionIds: ["transaction-1", "transaction-2", "transaction-3"],
    transactionIdsTruncated: false,
    currentCategoryId: null,
    currentCategoryName: null,
    currentTxnType: "expense",
    currentPaymentMethod: null,
    currentClassifiedBy: "unclassified",
    proposedCategoryId: "category-groceries",
    proposedCategoryName: "Groceries",
    proposedMerchantId: null,
    proposedTxnType: "expense",
    proposedPaymentMethod: "card",
    proposedClassifiedBy: "merchant_alias",
    proposedConfidence: "1.000",
    knowledgeVersionId: "knowledge-version-1",
    rememberable: true,
    ...overrides,
  };
}

function response(groups: Array<ClassificationReviewGroup>): ClassificationReviewResponse {
  return {
    groups,
    totalGroups: groups.length,
    totalOccurrences: groups.reduce((sum, item) => sum + item.occurrences, 0),
    candidatesScanned: groups.length,
    candidateLimitReached: false,
  };
}

function preview(overrides: Partial<PreviewResult> = {}): PreviewResult {
  return {
    runId: "run-1",
    state: "proposed",
    changed: 3,
    conflicted: 0,
    unresolved: 0,
    items: [],
    proposals: [],
    ...overrides,
  };
}

function applied(overrides: Partial<RunResult> = {}): RunResult {
  return {
    runId: "run-1",
    state: "applied",
    changed: 3,
    conflicted: 0,
    unresolved: 0,
    items: [],
    ...overrides,
  };
}

afterEach(cleanup);

describe("grouped classification review", () => {
  it("shows loading, grouped explanations and an empty state without financial amounts", async () => {
    let resolve!: (value: ClassificationReviewResponse) => void;
    const request = vi.fn((path: string) => {
      if (path === "/classification-runs?limit=20&offset=0") return Promise.resolve([]);
      return new Promise<ClassificationReviewResponse>((done) => {
        resolve = done;
      });
    });

    const view = render(
      <ClassificationReviewScreen categories={categories} request={request as never} />,
    );
    expect(screen.getByText("Loading review queue…")).toBeTruthy();

    resolve(response([group()]));
    const card = await screen.findByRole("article", { name: "SAMPLE MARKET" });
    expect(within(card).getByText("A saved household answer matches these rows.")).toBeTruthy();
    expect(within(card).getByText(/3 occurrences/)).toBeTruthy();
    expect(view.container.textContent).not.toMatch(/[₹$]|\b\d+[.,]\d{2}\b/);

    cleanup();
    render(
      <ClassificationReviewScreen
        categories={categories}
        request={vi.fn().mockResolvedValue(response([])) as never}
      />,
    );
    expect(await screen.findByText("Nothing needs classification review.")).toBeTruthy();
  });

  it("previews the exact saved-knowledge scope before applying it", async () => {
    const request = vi.fn(async (path: string, init?: RequestInit) => {
      void init;
      if (path === "/classification-runs?limit=20&offset=0") return [];
      if (path === "/classification-review/groups?limit=50&offset=0") return response([group()]);
      if (path === "/classification-runs/preview") return preview();
      if (path === "/classification-runs/run-1/apply") return applied();
      throw new Error(`Unexpected path ${path}`);
    });
    render(<ClassificationReviewScreen categories={categories} request={request as never} />);

    fireEvent.click(await screen.findByRole("button", { name: "Preview 3 proposed changes" }));
    expect(await screen.findByText("3 can change · 0 conflicts · 0 unresolved")).toBeTruthy();
    const previewCall = request.mock.calls.find(([path]) => path === "/classification-runs/preview");
    expect(previewCall?.[1]).toMatchObject({
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    expect(JSON.parse(previewCall?.[1]?.body as string)).toEqual({
        knowledgeVersionId: "knowledge-version-1",
        reason: "Owner reviewed SAMPLE MARKET",
        items: ["transaction-1", "transaction-2", "transaction-3"].map((transactionId) => ({
          transactionId,
          reason: "A saved household answer matches these rows.",
          changes: {
            categoryId: "category-groceries",
            txnType: "expense",
            paymentMethod: "card",
            classifiedBy: "merchant_alias",
            classificationConfidence: "1.000",
            isReviewed: true,
          },
        })),
    });

    fireEvent.click(screen.getByRole("button", { name: "Apply 3 changes" }));
    await screen.findByText("Applied to 3 transactions.");
    expect(request).toHaveBeenCalledWith("/classification-runs/run-1/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: expect.stringContaining("idempotencyKey"),
    });
  });

  it("offers conditional undo and reports conflicts without claiming a full reversal", async () => {
    const request = vi.fn(async (path: string, init?: RequestInit) => {
      void init;
      if (path === "/classification-runs?limit=20&offset=0") return [];
      if (path === "/classification-review/groups?limit=50&offset=0") return response([group()]);
      if (path === "/classification-runs/preview") return preview();
      if (path.endsWith("/apply")) return applied();
      if (path.endsWith("/undo")) {
        return applied({ runId: "undo-1", changed: 2, conflicted: 1, sourceRunId: "run-1" });
      }
      throw new Error(`Unexpected path ${path}`);
    });
    render(<ClassificationReviewScreen categories={categories} request={request as never} />);

    fireEvent.click(await screen.findByRole("button", { name: "Preview 3 proposed changes" }));
    fireEvent.click(await screen.findByRole("button", { name: "Apply 3 changes" }));
    fireEvent.click(await screen.findByRole("button", { name: "Undo this application" }));

    expect(await screen.findByText("Undid 2 changes; 1 could not be undone.")).toBeTruthy();
    expect(request).toHaveBeenCalledWith("/classification-runs/run-1/undo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: expect.stringContaining("Owner requested undo"),
    });
  });

  it("keeps one-row and reusable owner answers separate", async () => {
    const unresolved = group({
      reason: "unclassified",
      explanation: "No household answer matches this wording yet.",
      occurrences: 2,
      transactionIds: ["transaction-1", "transaction-2"],
      proposedCategoryId: null,
      proposedCategoryName: null,
      proposedTxnType: null,
      proposedPaymentMethod: null,
      proposedClassifiedBy: "unclassified",
      proposedConfidence: "0.000",
      knowledgeVersionId: null,
    });
    const knowledge = {
      aliasId: "alias-1",
      knowledgeVersionId: "knowledge-version-new",
    } as KnowledgeRow;
    const request = vi.fn(async (path: string, init?: RequestInit) => {
      void init;
      if (path === "/classification-runs?limit=20&offset=0") return [];
      if (path === "/classification-review/groups?limit=50&offset=0") return response([unresolved]);
      if (path === "/merchant-knowledge?pending=true") return knowledge;
      if (path === "/classification-runs/preview") return preview({ changed: 2 });
      if (path.endsWith("/apply")) return applied({ changed: 1 });
      if (path === "/merchant-knowledge/alias-1") return undefined;
      throw new Error(`Unexpected path ${path}`);
    });
    render(<ClassificationReviewScreen categories={categories} request={request as never} />);

    fireEvent.change(await screen.findByLabelText("Category for SAMPLE MARKET"), {
      target: { value: "category-groceries" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Remember and preview 2" }));
    expect(await screen.findByText("2 can change · 0 conflicts · 0 unresolved")).toBeTruthy();

    const createCall = request.mock.calls.find(([path]) => path === "/merchant-knowledge?pending=true");
    expect(JSON.parse(createCall?.[1]?.body as string)).toMatchObject({
      displayName: "SAMPLE MARKET",
      kind: "unresolved",
      pattern: "SAMPLE MARKET",
      patternKind: "exact",
      role: "unresolved",
      sourceKind: "owner",
      categoryId: "category-groceries",
      direction: "debit",
    });

    cleanup();
    request.mockClear();
    render(<ClassificationReviewScreen categories={categories} request={request as never} />);
    fireEvent.change(await screen.findByLabelText("Category for SAMPLE MARKET"), {
      target: { value: "category-groceries" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Apply to one only" }));
    await screen.findByText("Applied to 1 transaction; the wording was not remembered.");
    const oneOffCreate = request.mock.calls.find(
      ([path]) => path === "/merchant-knowledge?pending=true",
    );
    expect(JSON.parse(oneOffCreate?.[1]?.body as string)).toMatchObject({
      displayName: "Temporary owner decision",
      pattern: "Temporary owner decision",
    });
    const oneOffApply = request.mock.calls.find(([path]) => (path as string).endsWith("/apply"));
    expect(JSON.parse(oneOffApply?.[1]?.body as string)).toMatchObject({
      activateKnowledge: false,
    });
    expect(request).toHaveBeenCalledWith("/merchant-knowledge/alias-1", { method: "DELETE" });
  });

  it("blocks incomplete scopes and lets a failed queue load retry", async () => {
    let groupsCalls = 0;
    const request = vi.fn(async (path: string) => {
      if (path === "/classification-runs?limit=20&offset=0") return [];
      groupsCalls += 1;
      if (groupsCalls === 1) throw new Error("offline");
      return response([group({ transactionIdsTruncated: true })]);
    });
    render(<ClassificationReviewScreen categories={categories} request={request as never} />);

    expect((await screen.findByRole("alert")).textContent).toContain("The review queue could not be loaded.");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    const card = await screen.findByRole("article", { name: "SAMPLE MARKET" });
    expect(within(card).getByText(/Narrow the date range/)).toBeTruthy();
    expect(within(card).getByRole<HTMLButtonElement>("button", { name: /Preview/ }).disabled).toBe(true);
  });

  it("does not turn a conflicting answer into reusable knowledge", async () => {
    render(
      <ClassificationReviewScreen
        categories={categories}
        request={vi.fn().mockResolvedValue(response([group({
          reason: "conflicting_knowledge",
          knowledgeVersionId: null,
          proposedCategoryId: null,
          proposedCategoryName: null,
          rememberable: false,
        })])) as never}
      />,
    );

    const card = await screen.findByRole("article", { name: "SAMPLE MARKET" });
    expect(within(card).queryByRole("button", { name: /Remember/ })).toBeNull();
    expect(within(card).getByRole("button", { name: "Apply to one only" })).toBeTruthy();
  });

  it("shows economic role separately and applies the classifier's stated provenance", async () => {
    const request = vi.fn(async (path: string, init?: RequestInit) => {
      void init;
      if (path === "/classification-runs?limit=20&offset=0") return [];
      if (path === "/classification-review/groups?limit=50&offset=0") {
        return response([group({ proposedClassifiedBy: "prior_match", proposedTxnType: "refund" })]);
      }
      if (path === "/classification-runs/preview") return preview();
      throw new Error(`Unexpected path ${path}`);
    });
    render(<ClassificationReviewScreen categories={categories} request={request as never} />);

    const card = await screen.findByRole("article", { name: "SAMPLE MARKET" });
    expect(within(card).getByText("expense → refund")).toBeTruthy();
    fireEvent.click(within(card).getByRole("button", { name: "Preview 3 proposed changes" }));

    await screen.findByText("3 can change · 0 conflicts · 0 unresolved");
    const call = request.mock.calls.find(([path]) => path === "/classification-runs/preview");
    const body = JSON.parse(call?.[1]?.body as string);
    expect(body.items[0].changes.classifiedBy).toBe("prior_match");
  });

  it("uses a contains matcher when the owner removes variable statement references", async () => {
    const unresolved = group({
      reason: "unclassified",
      knowledgeVersionId: null,
      proposedCategoryId: null,
      proposedCategoryName: null,
    });
    const request = vi.fn(async (path: string, init?: RequestInit) => {
      void init;
      if (path === "/classification-runs?limit=20&offset=0") return [];
      if (path === "/classification-review/groups?limit=50&offset=0") return response([unresolved]);
      if (path === "/merchant-knowledge?pending=true") {
        return { aliasId: "alias-1", knowledgeVersionId: "version-1" } as KnowledgeRow;
      }
      if (path === "/classification-runs/preview") return preview();
      throw new Error(`Unexpected path ${path}`);
    });
    render(<ClassificationReviewScreen categories={categories} request={request as never} />);

    fireEvent.change(await screen.findByLabelText("Category for SAMPLE MARKET"), {
      target: { value: "category-groceries" },
    });
    fireEvent.change(screen.getByRole("textbox", { name: /Wording to remember/ }), {
      target: { value: "SAMPLE" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Remember and preview 3" }));

    await screen.findByText("3 can change · 0 conflicts · 0 unresolved");
    const call = request.mock.calls.find(([path]) => path === "/merchant-knowledge?pending=true");
    expect(JSON.parse(call?.[1]?.body as string)).toMatchObject({
      displayName: "SAMPLE",
      pattern: "SAMPLE",
      patternKind: "contains",
    });
  });

  it("loads later groups instead of trapping the owner on the first page", async () => {
    const first = response([group()]);
    first.totalGroups = 2;
    const later = response([group({ groupKey: "group-2", description: "SECOND MARKET" })]);
    later.totalGroups = 2;
    const request = vi.fn(async (path: string) => {
      if (path === "/classification-runs?limit=20&offset=0") return [];
      if (path === "/classification-review/groups?limit=50&offset=0") return first;
      if (path === "/classification-review/groups?limit=50&offset=1") return later;
      throw new Error(`Unexpected path ${path}`);
    });
    render(<ClassificationReviewScreen categories={categories} request={request as never} />);

    fireEvent.click(await screen.findByRole("button", { name: "Load 1 more group" }));
    expect(await screen.findByRole("article", { name: "SECOND MARKET" })).toBeTruthy();
  });

  it("re-fetches with sort=impact when the owner switches sort order", async () => {
    const impactOnly = response([group({ groupKey: "group-2", description: "BIG SPEND" })]);
    const request = vi.fn(async (path: string) => {
      if (path === "/classification-runs?limit=20&offset=0") return [];
      if (path === "/classification-review/groups?limit=50&offset=0") return response([group()]);
      if (path === "/classification-review/groups?limit=50&offset=0&sort=impact") return impactOnly;
      throw new Error(`Unexpected path ${path}`);
    });
    render(<ClassificationReviewScreen categories={categories} request={request as never} />);
    await screen.findByRole("article", { name: "SAMPLE MARKET" });

    fireEvent.change(screen.getByLabelText("Sort"), { target: { value: "impact" } });

    expect(await screen.findByRole("article", { name: "BIG SPEND" })).toBeTruthy();
  });

  it("keeps stale and manually protected preview results visibly unapplied", async () => {
    const request = vi.fn(async (path: string) => {
      if (path === "/classification-runs?limit=20&offset=0") return [];
      if (path === "/classification-review/groups?limit=50&offset=0") return response([group()]);
      if (path === "/classification-runs/preview") {
        return preview({
          changed: 0,
          unresolved: 1,
          items: [{ transactionId: "transaction-1", status: "unresolved", reason: "manual classification is protected" }],
        });
      }
      throw new Error(`Unexpected path ${path}`);
    });
    render(<ClassificationReviewScreen categories={categories} request={request as never} />);

    fireEvent.click(await screen.findByRole("button", { name: "Preview 3 proposed changes" }));
    expect(await screen.findByText("0 can change · 0 conflicts · 1 unresolved")).toBeTruthy();
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Apply 0 changes" }).disabled).toBe(true);
  });

  it("shows recent applied runs and offers undo only where it applies", async () => {
    const runs: Array<RunRow> = [
      {
        runId: "run-undoable",
        appliedAt: "2026-09-10T00:00:00Z",
        reason: "Owner reviewed SAMPLE MARKET",
        changed: 3,
        sourceRunId: null,
        undone: false,
        knowledgeDisplayName: "SAMPLE MARKET",
      },
      {
        runId: "run-already-undone",
        appliedAt: "2026-09-09T00:00:00Z",
        reason: "Owner reviewed OTHER MARKET",
        changed: 1,
        sourceRunId: null,
        undone: true,
        knowledgeDisplayName: "OTHER MARKET",
      },
      {
        runId: "run-is-an-undo",
        appliedAt: "2026-09-08T00:00:00Z",
        reason: "Owner requested undo",
        changed: 1,
        sourceRunId: "run-some-source",
        undone: false,
        knowledgeDisplayName: null,
      },
    ];
    const request = vi.fn(async (path: string, init?: RequestInit) => {
      void init;
      if (path === "/classification-runs?limit=20&offset=0") return runs;
      if (path === "/classification-review/groups?limit=50&offset=0") return response([]);
      if (path === "/classification-runs/run-undoable/undo") {
        return applied({ runId: "run-undo-new", changed: 3, sourceRunId: "run-undoable" });
      }
      throw new Error(`Unexpected path ${path}`);
    });
    render(<ClassificationReviewScreen categories={categories} request={request as never} />);

    await screen.findByText("Recent changes");
    const undoButtons = screen.getAllByRole("button", { name: "Undo" });
    expect(undoButtons).toHaveLength(1);

    fireEvent.click(undoButtons[0]);
    expect(await screen.findByText(/Undid 3 changes\./)).toBeTruthy();
    expect(screen.getByText(/Settings › Merchant knowledge/)).toBeTruthy();
    expect(request).toHaveBeenCalledWith("/classification-runs/run-undoable/undo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: expect.stringContaining("Owner requested undo"),
    });
  });
});
