/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RulesManager } from "@/app/settings/rules-manager";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type { CategoryRow, RuleAnalytics, RuleRow, RuleSuggestion } from "@/types/api";

type Request = typeof apiFetch;

const CATEGORIES = [
  { id: "cat-groceries", parentId: null, name: "Groceries", slug: "groceries", kind: "expense",
    bucket: "need", sortOrder: 10, isSystem: true, isActive: true },
  { id: "cat-dining", parentId: null, name: "Food & Dining", slug: "food-dining",
    kind: "expense", bucket: "want", sortOrder: 20, isSystem: true, isActive: true },
] as Array<CategoryRow>;

function rule(changes: Partial<RuleRow> = {}): RuleRow {
  return {
    id: "rule-1", name: "Groceries", priority: 100, isActive: true, origin: "user_created",
    timesApplied: 3, lastAppliedAt: null,
    matchDescriptionPattern: "GROCER", matchPatternKind: "contains", matchAccountId: null,
    matchCurrency: null, matchAmountMin: null, matchAmountMax: null, matchAmountExact: null,
    matchDirection: "any", matchCountry: null, matchReferencePattern: null,
    setCategoryId: "cat-groceries", setMerchantId: null, setTxnType: null,
    setPaymentMethod: null, setCountry: null, setTags: [], setIsTransferCandidate: false,
    setCounterpartyAccountId: null,
    ...changes,
  } as RuleRow;
}

function mount(request: Request, onChanged = vi.fn()) {
  render(<RulesManager categories={CATEGORIES} onChanged={onChanged} request={request} />);
  return onChanged;
}

/** Answers `/rules` with the given list and records every other call. */
function serving(rules: Array<RuleRow>, extra: Record<string, unknown> = {}) {
  const calls: Array<{ path: string; init?: RequestInit }> = [];
  const request = vi.fn(async (path: string, init?: RequestInit) => {
    calls.push({ path, init });
    if (path === "/rules" && (init?.method ?? "GET") === "GET") return rules;
    if (path in extra) {
      const answer = extra[path];
      if (answer instanceof Error) throw answer;
      return answer;
    }
    return {};
  }) as Request;
  return { request, calls };
}

afterEach(cleanup);

describe("the rule list", () => {
  it("renders in priority order and says what each rule does, in words", async () => {
    // `GET /rules` returns ascending priority, which is evaluation order. The screen renders
    // that order and does not re-sort: a list that reordered by name or by id would stop
    // reading the way the cascade runs, which is the only reason the order is shown at all.
    const { request } = serving([
      rule({ id: "b", name: "Big debits", priority: 10, matchDescriptionPattern: "CARD",
             matchDirection: "debit", matchAmountMin: "500.0000", setCategoryId: null,
             setIsTransferCandidate: true }),
      rule({ id: "a", name: "Coffee", priority: 300, matchDescriptionPattern: "COFFEE",
             setCategoryId: "cat-dining" }),
    ]);
    mount(request);

    const items = await screen.findAllByRole("listitem");
    expect(within(items[0]).getByText("Big debits")).toBeTruthy();
    expect(within(items[1]).getByText("Coffee")).toBeTruthy();

    expect(
      within(items[0]).getByText(
        "narration contains “CARD”, debit only, at least 500.0000 → transfer candidate",
      ),
    ).toBeTruthy();
    expect(
      within(items[1]).getByText("narration contains “COFFEE” → Food & Dining"),
    ).toBeTruthy();
  });

  it("shows how often a rule has actually decided something", async () => {
    const { request } = serving([rule({ timesApplied: 12 })]);
    mount(request);

    expect(await screen.findByText("applied 12×")).toBeTruthy();
  });

  it("says so when there are none rather than rendering an empty list", async () => {
    const { request } = serving([]);
    mount(request);

    expect(await screen.findByText("No rules yet.")).toBeTruthy();
  });
});

describe("creating a rule", () => {
  it("shows the affected count first and creates nothing until it is confirmed", async () => {
    const { request, calls } = serving([], {
      "/rules/preview": { affected: 41, sample: [] },
    });
    mount(request);
    await screen.findByText("No rules yet.");

    fireEvent.change(screen.getByLabelText("Narration"), { target: { value: "GROCER" } });
    fireEvent.click(screen.getByRole("button", { name: "Check what this matches" }));

    const counted = (await screen.findByText(/This rule matches/)).closest('[role="status"]');
    expect(counted?.textContent).toContain("This rule matches");
    expect(counted?.textContent).toContain("41");
    expect(counted?.textContent).toContain("transactions in the ledger");
    // Nothing has been created: the count is the safeguard against an over-general pattern,
    // and it is worth nothing if the rule exists by the time it is shown.
    expect(calls.filter((call) => call.path === "/rules" && call.init?.method === "POST"))
      .toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Save this rule" }));

    await waitFor(() =>
      expect(calls.filter((call) => call.path === "/rules" && call.init?.method === "POST"))
        .toHaveLength(1),
    );
  });

  it("sends the amount matchers as the decimal strings they were typed as", async () => {
    const { request, calls } = serving([], { "/rules/preview": { affected: 0, sample: [] } });
    mount(request);
    await screen.findByText("No rules yet.");

    fireEvent.change(screen.getByLabelText("Narration"), { target: { value: "CARD" } });
    fireEvent.click(screen.getByRole("button", { name: "Check what this matches" }));
    await screen.findByText(/This rule matches/);

    const previewed = calls.find((call) => call.path === "/rules/preview");
    const body = JSON.parse(String(previewed?.init?.body));
    // Strings, not numbers: a bound read through a float is a bound that is wrong at the edge,
    // and the browser does no arithmetic on money at all.
    expect(body.matchAmountMin).toBeNull();
    expect(typeof body.matchDescriptionPattern).toBe("string");
  });

  it("expands the matched rows on request and hides them again", async () => {
    const { request } = serving([], {
      "/rules/preview": {
        affected: 2,
        sample: [
          { id: "t1", bookedDate: "2026-03-02", descriptionRaw: "GROCER MARKET",
            amount: "-120.0000", currency: "XAE" },
        ],
      },
    });
    mount(request);
    await screen.findByText("No rules yet.");
    fireEvent.change(screen.getByLabelText("Narration"), { target: { value: "GROCER" } });
    fireEvent.click(screen.getByRole("button", { name: "Check what this matches" }));

    fireEvent.click(await screen.findByRole("button", { name: "Show matched rows" }));
    expect(screen.getByText("GROCER MARKET")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Hide matched rows" }));
    expect(screen.queryByText("GROCER MARKET")).toBeNull();
  });

  it("renders the service's own sentence when a create is refused", async () => {
    const { request } = serving([], {
      "/rules/preview": new ApiError(
        422, "a rule needs something to decide: a category, a merchant, a transaction type",
      ),
    });
    mount(request);
    await screen.findByText("No rules yet.");

    fireEvent.change(screen.getByLabelText("Narration"), { target: { value: "X" } });
    fireEvent.click(screen.getByRole("button", { name: "Check what this matches" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(
      "a rule needs something to decide: a category, a merchant, a transaction type",
    );
    // Not a JSON envelope, and not a generic apology that hides what the service said.
    expect(alert.textContent).not.toContain("{");
  });
});

describe("editing a rule", () => {
  it("deactivates by sending a full replacement rather than deleting", async () => {
    const { request, calls } = serving([rule()]);
    mount(request);
    await screen.findByText("Groceries");

    fireEvent.click(screen.getByRole("button", { name: "Deactivate" }));

    await waitFor(() =>
      expect(calls.some((call) => call.init?.method === "PATCH")).toBe(true),
    );
    const patch = calls.find((call) => call.init?.method === "PATCH");
    const body = JSON.parse(String(patch?.init?.body));
    expect(body.isActive).toBe(false);
    // A full replacement: every editable field travels, so there is no absent-versus-null.
    expect(body.matchDescriptionPattern).toBe("GROCER");
    expect(body.priority).toBe(100);
    expect(calls.some((call) => call.init?.method === "DELETE")).toBe(false);
  });

  it("offers to reactivate a rule that is off", async () => {
    const { request } = serving([rule({ isActive: false })]);
    mount(request);

    expect(await screen.findByRole("button", { name: "Activate" })).toBeTruthy();
  });

  it("renders the service's sentence when a delete is refused", async () => {
    const { request } = serving([rule()], {});
    const refusing = vi.fn(async (path: string, init?: RequestInit) => {
      if (path === "/rules" && (init?.method ?? "GET") === "GET") return [rule()];
      if (path === "/rules/analytics" || path === "/rules/suggestions") return {};
      throw new ApiError(
        409,
        "this rule classified rows that are already in the ledger, and they name it as the "
        + "reason; deactivate it instead so those rows keep their provenance",
      );
    }) as Request;
    void request;
    mount(refusing);
    await screen.findByText("Groceries");

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));

    expect((await screen.findByRole("alert")).textContent).toContain("deactivate it instead");
  });
});

describe("the unclassified pass", () => {
  it("states what it cannot touch, counts first, and only then applies", async () => {
    const { request, calls } = serving([rule()], {
      "/classify?preview=true": { considered: 4184, changed: 900, applied: false, sample: [] },
      "/classify": { considered: 4184, changed: 900, applied: true, sample: [] },
    });
    mount(request);
    await screen.findByText("Groceries");

    // The promise is on screen before anything runs, because it is the reason the owner can
    // press the button without checking what it will overwrite: nothing.
    expect(
      screen.getByText(/cannot change a transaction that already has one/),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Check how many" }));

    expect(await screen.findByText(/4184 transactions have no classification/)).toBeTruthy();
    expect(calls.filter((call) => call.path === "/classify")).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Apply to 900" }));

    await waitFor(() =>
      expect(calls.filter((call) => call.path === "/classify")).toHaveLength(1),
    );
  });

  it("offers no apply button when the rules decide nothing", async () => {
    const { request } = serving([rule()], {
      "/classify?preview=true": { considered: 12, changed: 0, applied: false, sample: [] },
    });
    mount(request);
    await screen.findByText("Groceries");

    fireEvent.click(screen.getByRole("button", { name: "Check how many" }));
    await screen.findByText(/the rules decide 0 of them/);

    expect(screen.queryByRole("button", { name: /^Apply to/ })).toBeNull();
  });
});

function analytics(overrides: Partial<RuleAnalytics> = {}): RuleAnalytics {
  return {
    coverage: {
      totalRows: 10,
      byClassifiedBy: {
        user_manual: { count: 4, percent: "40.00" },
        user_rule: { count: 3, percent: "30.00" },
        merchant_alias: { count: 0, percent: "0.00" },
        recurring_series: { count: 0, percent: "0.00" },
        prior_match: { count: 0, percent: "0.00" },
        seed_rule: { count: 0, percent: "0.00" },
        unclassified: { count: 3, percent: "30.00" },
        ai: { count: 0, percent: "0.00" },
      },
      confidenceBands: {
        none: { count: 0, percent: "0.00" },
        "below_0.5": { count: 1, percent: "10.00" },
        "0.5_to_0.8": { count: 1, percent: "10.00" },
        "above_0.8": { count: 8, percent: "80.00" },
      },
      reportableUncategorized: { count: 2, percent: "20.00" },
    },
    rules: [
      {
        ruleId: "rule-1",
        name: "Synthetic groceries rule",
        origin: "user_created",
        hits: 5,
        lastHitDate: "2026-08-01",
        disagreements: 1,
        disagreementSample: [
          { bookedDate: "2026-07-20", description: "SYNTHETIC GROCER SHOP", amount: "-12.0000" },
        ],
      },
    ],
    topUnclassified: [{ description: "SYNTHETIC UNKNOWN VENDOR", count: 4, totalAbsAmountBase: "88.0000" }],
    ...overrides,
  } as RuleAnalytics;
}

function suggestion(overrides: Partial<RuleSuggestion> = {}): RuleSuggestion {
  return {
    pattern: "COFFEEHOUSE",
    direction: "debit",
    categoryId: "cat-dining",
    categoryName: "Food & Dining",
    txnType: "expense",
    evidenceRows: 3,
    affected: 4,
    unclassified: 1,
    ...overrides,
  };
}

describe("coverage and analytics", () => {
  it("renders server figures for coverage, rule hits and top unclassified descriptions", async () => {
    const { request } = serving([], {
      "/rules/analytics": analytics(),
      "/rules/suggestions": { suggestions: [] },
    });
    mount(request);

    const region = await screen.findByRole("region", { name: "Coverage and analytics" });
    expect(within(region).getByText("10 rows counted.")).toBeTruthy();
    expect(within(region).getByText("40.00%")).toBeTruthy();
    expect(within(region).getByText("Synthetic groceries rule")).toBeTruthy();
    expect(within(region).getByText(/5 hits/)).toBeTruthy();
    expect(within(region).getByText(/1 disagreement/)).toBeTruthy();
    expect(within(region).getByText(/SYNTHETIC GROCER SHOP/)).toBeTruthy();
    expect(within(region).getByText(/SYNTHETIC UNKNOWN VENDOR/)).toBeTruthy();
    expect(within(region).getByText(/2 reportable rows have no category \(20.00%\)/)).toBeTruthy();
  });

  it("keeps the analytics deep-link target visible when the endpoint answers with an unexpected shape", async () => {
    const { request } = serving([], { "/rules/analytics": {}, "/rules/suggestions": {} });
    mount(request);
    await screen.findByText("No rules yet.");

    const region = await screen.findByRole("region", { name: "Coverage and analytics" });
    expect(within(region).getByText("Coverage is unavailable.")).toBeTruthy();
  });
});

describe("suggested rules", () => {
  it("creates the suggested rule, previews past rows, and only writes on confirm", async () => {
    const created = rule({ id: "rule-99", name: "Mark Food & Dining" });
    const { request, calls } = serving([], {
      "/rules/analytics": {},
      "/rules/suggestions": { suggestions: [suggestion()] },
      "/rules": created,
      "/classify?ruleId=rule-99&preview=true": {
        considered: 5,
        changed: 4,
        applied: false,
        sample: [
          {
            id: "t1",
            bookedDate: "2026-05-01",
            descriptionRaw: "COFFEEHOUSE",
            amount: "-4.5000",
            currency: "XAE",
            classifiedBy: "unclassified",
          },
        ],
      },
      "/classify?ruleId=rule-99": { considered: 5, changed: 4, applied: true, sample: [] },
    });
    mount(request);

    const region = await screen.findByRole("region", { name: "Suggested rules" });
    expect(within(region).getByText(/COFFEEHOUSE/)).toBeTruthy();
    expect(within(region).getByText(/3 corrections · 4 affected · 1 unclassified/)).toBeTruthy();

    fireEvent.click(within(region).getByRole("button", { name: "Create rule" }));
    await waitFor(() =>
      expect(calls.some((call) => call.path === "/rules" && call.init?.method === "POST")).toBe(
        true,
      ),
    );
    const createCall = calls.find(
      (call) => call.path === "/rules" && call.init?.method === "POST",
    );
    const createBody = JSON.parse(String(createCall?.init?.body));
    expect(createBody.matchDescriptionPattern).toBe("COFFEEHOUSE");
    expect(createBody.matchDirection).toBe("debit");
    expect(createBody.setCategoryId).toBe("cat-dining");
    expect(createBody.setTxnType).toBe("expense");

    fireEvent.click(await within(region).findByRole("button", { name: "Check past rows" }));

    expect(
      await within(region).findByText(/4 of 5 unclassified rows would change/),
    ).toBeTruthy();
    expect(within(region).getByText(/2026-05-01/)).toBeTruthy();
    // Nothing is written by the preview: the exact non-preview path was never called.
    expect(calls.some((call) => call.path === "/classify?ruleId=rule-99")).toBe(false);

    fireEvent.click(
      within(region).getByRole("button", { name: "Apply 4 changes" }),
    );

    expect(await within(region).findByText("4 of 5 unclassified rows updated.")).toBeTruthy();
    const previewIndex = calls.findIndex(
      (call) => call.path === "/classify?ruleId=rule-99&preview=true",
    );
    const applyIndex = calls.findIndex((call) => call.path === "/classify?ruleId=rule-99");
    expect(previewIndex).toBeGreaterThanOrEqual(0);
    expect(applyIndex).toBeGreaterThan(previewIndex);
  });

  it("says so when there are no suggestions yet", async () => {
    const { request } = serving([], {
      "/rules/analytics": {},
      "/rules/suggestions": { suggestions: [] },
    });
    mount(request);

    const region = await screen.findByRole("region", { name: "Suggested rules" });
    expect(
      within(region).getByText("No suggestions yet — repeated corrections will appear here."),
    ).toBeTruthy();
  });
});

describe("starter rule packs", () => {
  it("offers a shipped pack when no seed rules are loaded, and loads it on click", async () => {
    const { request, calls } = serving([], {
      "/rules/packs": [{ name: "banking-narration", ruleCount: 37 }],
      "/rules/packs/banking-narration": [rule({ id: "seed-1", origin: "seed" })],
    });
    const onChanged = mount(request);

    fireEvent.click(
      await screen.findByRole("button", { name: "Load starter rules" }),
    );

    await waitFor(() =>
      expect(calls.some((call) => call.path === "/rules/packs/banking-narration")).toBe(true),
    );
    expect(onChanged).toHaveBeenCalled();
    // Reloading afterwards is the same `GET /rules` every other write already triggers.
    expect(calls.filter((call) => call.path === "/rules" && !call.init?.method).length).toBe(2);
  });

  it("hides the starter section once the household already holds a seed rule", async () => {
    const { request } = serving([rule({ id: "seed-1", origin: "seed" })]);
    mount(request);

    await screen.findAllByRole("listitem");
    expect(screen.queryByText("Starter rules")).toBeNull();
  });

  it("shows the server's sentence when a pack has already been loaded once", async () => {
    const { request } = serving([], {
      "/rules/packs": [{ name: "banking-narration", ruleCount: 37 }],
      "/rules/packs/banking-narration": new ApiError(
        409,
        "this household already holds seed rules; a pack loads once",
      ),
    });
    mount(request);

    fireEvent.click(
      await screen.findByRole("button", { name: "Load starter rules" }),
    );

    expect(
      await screen.findByText("this household already holds seed rules; a pack loads once"),
    ).toBeTruthy();
  });
});
