"use client";

import { useEffect, useState } from "react";

import { type apiFetch, errorText } from "@/lib/api/client";
import type { CategoryRow, KnowledgeBody, KnowledgeRow } from "@/types/api";

type Props = {
  categories: Array<CategoryRow>;
  request: typeof apiFetch;
};

type Edits = {
  categoryId: string;
  pattern: string;
  patternKind: KnowledgeRow["patternKind"];
  priority: string;
};

function editsFor(row: KnowledgeRow): Edits {
  return {
    categoryId: row.categoryId ?? "",
    pattern: row.pattern,
    patternKind: row.patternKind,
    priority: String(row.priority),
  };
}

/**
 * The identity fields (`displayName`, `kind`, `merchantId`/`ownAccountId`) are carried forward
 * unchanged: `PATCH /merchant-knowledge/{aliasId}` itself refuses a body that disagrees with the
 * stable counterparty identity, so this is the body the endpoint accepts, not a UI restriction.
 * `merchantName` is always sent as `null` alongside `merchantId` — `coherent` refuses a body
 * naming both. `priority` is sent as the string the owner typed, never parsed in the browser:
 * pydantic's `int` field coerces a numeric string server-side, the same way `RulesManager` sends
 * its own priority.
 */
function bodyFrom(row: KnowledgeRow, edits: Edits): Omit<KnowledgeBody, "priority"> & {
  priority: string | number;
} {
  return {
    displayName: row.displayName,
    kind: row.kind,
    merchantId: row.merchantId,
    merchantName: null,
    ownAccountId: row.ownAccountId,
    pattern: edits.pattern,
    patternKind: edits.patternKind,
    role: row.role,
    priority: edits.priority,
    sourceKind: row.sourceKind,
    sourceUrl: row.sourceUrl,
    attribution: row.attribution,
    evidenceNote: row.evidenceNote,
    matcherVersion: row.matcherVersion,
    verifiedAt: row.verifiedAt,
    direction: row.direction,
    accountId: row.accountId,
    categoryId: edits.categoryId || null,
    txnType: row.txnType,
    paymentMethod: row.paymentMethod,
  };
}

const PATTERN_KINDS = ["exact", "prefix", "contains", "regex"] as const;

function describe(row: KnowledgeRow, categories: Array<CategoryRow>) {
  const category = categories.find((candidate) => candidate.id === row.categoryId)?.name;
  return `${row.pattern} (${row.patternKind}) → ${category ?? row.txnType ?? row.paymentMethod ?? "no change"}`;
}

/**
 * Settings › Merchant knowledge: every remembered private counterparty matcher, active by
 * default. Editing revises in place through `PATCH` (which itself revokes the prior evidence
 * version and appends a successor); nothing here reclassifies a transaction that has already
 * been decided.
 */
export function MerchantKnowledge({ categories, request }: Props) {
  const [rows, setRows] = useState<Array<KnowledgeRow>>([]);
  const [includeInactive, setIncludeInactive] = useState(false);
  const [problem, setProblem] = useState<string>();
  const [busyAliasId, setBusyAliasId] = useState<string>();
  const [editingAliasId, setEditingAliasId] = useState<string>();
  const [edits, setEdits] = useState<Edits>();

  const activeCategories = categories.filter((category) => category.isActive);
  const listPath = `/merchant-knowledge${includeInactive ? "?includeInactive=true" : ""}`;

  // Settings renders its sections only after the workspace loads, so the browser's own jump to
  // `#merchant-knowledge-title` (linked from Classify) happens before this heading exists.
  useEffect(() => {
    if (window.location.hash === "#merchant-knowledge-title") {
      document.getElementById("merchant-knowledge-title")?.scrollIntoView?.();
    }
  }, []);

  // The state settles in the promise's continuation, not the effect's own body, so a mutation
  // handler's `reload()` (a plain async function, not called from an effect) is the only path
  // that ever calls `setRows` synchronously inside a React effect.
  useEffect(() => {
    let current = true;
    void request<Array<KnowledgeRow>>(listPath)
      .then((loaded) => {
        if (current) setRows(loaded);
      })
      .catch((error: unknown) => {
        if (current) setProblem(errorText(error, "Merchant knowledge could not be loaded. Retry."));
      });
    return () => {
      current = false;
    };
  }, [request, listPath]);

  async function reload() {
    try {
      setRows(await request<Array<KnowledgeRow>>(listPath));
    } catch (error) {
      setProblem(errorText(error, "Merchant knowledge could not be loaded. Retry."));
    }
  }

  function startEdit(row: KnowledgeRow) {
    setEditingAliasId(row.aliasId);
    setEdits(editsFor(row));
    setProblem(undefined);
  }

  async function saveEdit(row: KnowledgeRow) {
    if (!edits) return;
    setBusyAliasId(row.aliasId);
    setProblem(undefined);
    try {
      await request<KnowledgeRow>(`/merchant-knowledge/${row.aliasId}`, {
        method: "PATCH",
        body: JSON.stringify(bodyFrom(row, edits)),
        headers: { "Content-Type": "application/json" },
      });
      setEditingAliasId(undefined);
      setEdits(undefined);
      await reload();
    } catch (error) {
      setProblem(errorText(error, "This edit was not saved. Retry."));
    } finally {
      setBusyAliasId(undefined);
    }
  }

  async function deactivate(row: KnowledgeRow) {
    setBusyAliasId(row.aliasId);
    setProblem(undefined);
    try {
      await request<void>(`/merchant-knowledge/${row.aliasId}`, { method: "DELETE" });
      await reload();
    } catch (error) {
      setProblem(errorText(error, "This could not be removed. Retry."));
    } finally {
      setBusyAliasId(undefined);
    }
  }

  return (
    <section aria-labelledby="merchant-knowledge-title" className="settings-block">
      <h2 id="merchant-knowledge-title">Merchant knowledge</h2>
      <p className="lede">
        Remembered private matchers, evidenced and household-only. Edits affect future
        classification only — nothing already decided is reclassified.
      </p>

      <label>
        <input
          checked={includeInactive}
          onChange={(event) => setIncludeInactive(event.target.checked)}
          type="checkbox"
        />
        Show inactive and superseded knowledge
      </label>

      {problem && <p className="inline-alert" role="alert">{problem}</p>}

      <ul className="merchant-knowledge-list">
        {rows.map((row) => (
          <li key={row.aliasId}>
            {editingAliasId === row.aliasId && edits ? (
              <>
                <label>
                  Wording
                  <input
                    onChange={(event) => setEdits({ ...edits, pattern: event.target.value })}
                    value={edits.pattern}
                  />
                </label>
                <label>
                  Match
                  <select
                    onChange={(event) =>
                      setEdits({
                        ...edits,
                        patternKind: event.target.value as Edits["patternKind"],
                      })
                    }
                    value={edits.patternKind}
                  >
                    {PATTERN_KINDS.map((kind) => (
                      <option key={kind} value={kind}>{kind}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Category
                  <select
                    onChange={(event) => setEdits({ ...edits, categoryId: event.target.value })}
                    value={edits.categoryId}
                  >
                    <option value="">No category</option>
                    {activeCategories.map((category) => (
                      <option key={category.id} value={category.id}>{category.name}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Priority
                  <input
                    onChange={(event) => setEdits({ ...edits, priority: event.target.value })}
                    value={edits.priority}
                  />
                </label>
                <button
                  disabled={busyAliasId === row.aliasId}
                  onClick={() => saveEdit(row)}
                  type="button"
                >Save</button>
                <button
                  disabled={busyAliasId === row.aliasId}
                  onClick={() => { setEditingAliasId(undefined); setEdits(undefined); }}
                  type="button"
                >Cancel</button>
              </>
            ) : (
              <>
                <span className="rule-name">{row.displayName}</span>
                <span className="section-label">{row.kind} · {row.role}</span>
                <span className="rule-describes">{describe(row, categories)}</span>
                <span className="section-label">{row.active ? "Active" : "Inactive"}</span>
                {row.active && (
                  <>
                    <button
                      disabled={busyAliasId === row.aliasId}
                      onClick={() => startEdit(row)}
                      type="button"
                    >Edit</button>
                    <button
                      disabled={busyAliasId === row.aliasId}
                      onClick={() => deactivate(row)}
                      type="button"
                    >Remove</button>
                  </>
                )}
              </>
            )}
          </li>
        ))}
      </ul>
      {rows.length === 0 && <p className="lede">No remembered merchant knowledge yet.</p>}
    </section>
  );
}
