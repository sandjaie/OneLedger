"use client";

import { useCallback, useEffect, useState } from "react";

import { LONG_REQUEST_TIMEOUT_MS, type apiFetch, errorText } from "@/lib/api/client";
import type {
  CategoryRow,
  ClassifyResult,
  RuleAnalytics,
  RuleRow,
  RuleSuggestion,
  RuleSuggestionsResponse,
} from "@/types/api";

type Props = {
  categories: Array<CategoryRow>;
  request: typeof apiFetch;
  onChanged: () => void;
};

type Draft = {
  name: string;
  priority: string;
  matchDescriptionPattern: string;
  matchPatternKind: "contains" | "exact" | "prefix" | "regex";
  matchDirection: "any" | "debit" | "credit";
  matchAmountMin: string;
  matchAmountMax: string;
  setCategoryId: string;
};

const EMPTY: Draft = {
  name: "",
  priority: "100",
  matchDescriptionPattern: "",
  matchPatternKind: "contains",
  matchDirection: "any",
  matchAmountMin: "",
  matchAmountMax: "",
  setCategoryId: "",
};

const PATTERN_KINDS = ["contains", "exact", "prefix", "regex"] as const;
const DIRECTIONS = ["any", "debit", "credit"] as const;

/** The service's own sentence where there is one; the fallback is for the network. */
/**
 * The rule as a sentence, so the list can be read without opening anything.
 *
 * Amount bounds are shown as the strings the API sent. No arithmetic, no comparison, no
 * formatting that would need to know a currency — a matcher is a bound on a magnitude, and the
 * account it will meet decides the denomination.
 */
function describe(rule: RuleRow, categories: Array<CategoryRow>): string {
  const matchers: Array<string> = [];
  if (rule.matchDescriptionPattern) {
    matchers.push(`narration ${rule.matchPatternKind} “${rule.matchDescriptionPattern}”`);
  }
  if (rule.matchReferencePattern) {
    matchers.push(`reference ${rule.matchPatternKind} “${rule.matchReferencePattern}”`);
  }
  if (rule.matchDirection !== "any") matchers.push(`${rule.matchDirection} only`);
  if (rule.matchAmountExact) matchers.push(`amount exactly ${rule.matchAmountExact}`);
  if (rule.matchAmountMin) matchers.push(`at least ${rule.matchAmountMin}`);
  if (rule.matchAmountMax) matchers.push(`at most ${rule.matchAmountMax}`);

  const outcomes: Array<string> = [];
  const category = categories.find((row) => row.id === rule.setCategoryId);
  if (category) outcomes.push(category.name);
  if (rule.setTxnType) outcomes.push(rule.setTxnType);
  if (rule.setPaymentMethod) outcomes.push(rule.setPaymentMethod);
  if (rule.setIsTransferCandidate) outcomes.push("transfer candidate");

  return `${matchers.join(", ") || "nothing"} → ${outcomes.join(", ") || "nothing"}`;
}

function body(draft: Draft) {
  return {
    name: draft.name || null,
    // Sent as the string it was typed as. `RuleBody.priority` is an int and pydantic coerces
    // a numeric string, so the browser never parses a number out of a form field — which is
    // the rule `scripts/check-identifiers.py` enforces, and it is right to enforce it here
    // even though a priority is not money: the exception is what erodes it.
    priority: draft.priority,
    isActive: true,
    matchDescriptionPattern: draft.matchDescriptionPattern || null,
    matchPatternKind: draft.matchPatternKind,
    matchDirection: draft.matchDirection,
    // Decimal strings in and decimal strings out. The browser never parses these into a
    // number: a bound read through a float is a bound that is wrong at the edge.
    matchAmountMin: draft.matchAmountMin || null,
    matchAmountMax: draft.matchAmountMax || null,
    setCategoryId: draft.setCategoryId || null,
  };
}

const CLASSIFIED_BY_LABELS: Record<string, string> = {
  user_manual: "Set by hand",
  user_rule: "Your rules",
  merchant_alias: "Known merchants",
  recurring_series: "Recurring series",
  prior_match: "Matched history",
  seed_rule: "Starter rules",
  unclassified: "Unclassified",
  ai: "AI",
};

/**
 * Coverage, per-rule hits/disagreements and the top unclassified descriptions — server strings
 * and server figures only, nothing computed in the browser. Loads its own data and renders
 * nothing until it has the shape it expects, so a caller that has not wired `/rules/analytics`
 * into its mock (most existing tests) sees no panel rather than a crash.
 */
function RuleAnalyticsPanel({ request }: { request: typeof apiFetch }) {
  const [analytics, setAnalytics] = useState<RuleAnalytics>();
  const [problem, setProblem] = useState<string>();

  useEffect(() => {
    let current = true;
    void request<RuleAnalytics>("/rules/analytics", { timeoutMs: LONG_REQUEST_TIMEOUT_MS })
      .then((result) => { if (current) setAnalytics(result); })
      .catch((error: unknown) => {
        if (current) setProblem(errorText(error, "Coverage could not be loaded."));
      });
    return () => { current = false; };
  }, [request]);

  if (problem) return <p className="inline-alert" role="alert">{problem}</p>;
  if (!analytics?.coverage) return null;

  const { coverage, rules, topUnclassified } = analytics;

  return (
    <section aria-labelledby="rules-analytics-title" className="settings-block">
      <h2 id="rules-analytics-title">Coverage and analytics</h2>
      <p className="lede">{coverage.totalRows} rows counted.</p>

      <table className="rules-analytics-table">
        <caption>By classified by</caption>
        <tbody>
          {Object.entries(coverage.byClassifiedBy).map(([status, figure]) => (
            <tr key={status}>
              <td>{CLASSIFIED_BY_LABELS[status] ?? status}</td>
              <td>{figure.count}</td>
              <td>{figure.percent === null ? "—" : `${figure.percent}%`}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <table className="rules-analytics-table">
        <caption>By confidence</caption>
        <tbody>
          {Object.entries(coverage.confidenceBands).map(([band, figure]) => (
            <tr key={band}>
              <td>{band}</td>
              <td>{figure.count}</td>
              <td>{figure.percent === null ? "—" : `${figure.percent}%`}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <p>
        {coverage.reportableUncategorized.count} reportable{" "}
        {coverage.reportableUncategorized.count === 1 ? "row has" : "rows have"} no category
        {coverage.reportableUncategorized.percent === null
          ? ""
          : ` (${coverage.reportableUncategorized.percent}%)`}.
      </p>

      <h3>Rule hits and disagreements</h3>
      {rules.length === 0 && <p className="lede">No active rules yet.</p>}
      {rules.length > 0 && (
        <ul className="rules-analytics-hits">
          {rules.map((rule) => (
            <li key={rule.ruleId}>
              <span className="rule-name">{rule.name ?? "Unnamed rule"}</span>
              <span>{rule.hits} {rule.hits === 1 ? "hit" : "hits"}</span>
              <span>{rule.lastHitDate ? `last hit ${rule.lastHitDate}` : "never hit"}</span>
              <span>{rule.disagreements} {rule.disagreements === 1 ? "disagreement" : "disagreements"}</span>
              {rule.disagreementSample.length > 0 && (
                <ul className="rule-sample">
                  {rule.disagreementSample.map((sample) => (
                    <li key={`${sample.bookedDate}-${sample.description}-${sample.amount}`}>
                      {sample.bookedDate} · {sample.description} · {sample.amount}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}

      <h3>Top unclassified descriptions</h3>
      {topUnclassified.length === 0 && <p className="lede">Nothing unclassified.</p>}
      {topUnclassified.length > 0 && (
        <ul className="rules-analytics-top">
          {topUnclassified.map((row) => (
            <li key={row.description}>
              {row.description} · {row.count} {row.count === 1 ? "row" : "rows"} · {row.totalAbsAmountBase}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** One suggestion: create the future-only rule, then preview and separately confirm applying it
 * to past rows.
 *
 * ponytail: "apply to past rows" reuses the existing, already-guarded `/classify?ruleId=`
 * cascade pass (preview, then apply) rather than the classification-runs preview/apply flow —
 * that flow needs an evidenced `knowledgeVersionId`, which a plain classification rule has no
 * way to carry. `/classify?ruleId=` never touches a row that already has an answer, which is
 * the same safety property; it only reaches currently-unclassified rows, not every row the rule
 * matches, and that is the deliberate scope of this simpler path. Nothing is written until the
 * owner has seen the preview's count and sample and presses the separate confirm.
 */
function SuggestedRuleCard({
  categories,
  onRuleCreated,
  request,
  suggestion,
}: {
  categories: Array<CategoryRow>;
  onRuleCreated: () => void;
  request: typeof apiFetch;
  suggestion: RuleSuggestion;
}) {
  const [rule, setRule] = useState<RuleRow>();
  const [preview, setPreview] = useState<ClassifyResult>();
  const [applied, setApplied] = useState<ClassifyResult>();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string>();

  const categoryName =
    categories.find((category) => category.id === suggestion.categoryId)?.name ??
    suggestion.categoryName ??
    "Uncategorised";

  async function create() {
    setBusy(true);
    setProblem(undefined);
    try {
      const saved = await request<RuleRow>("/rules", {
        method: "POST",
        body: JSON.stringify({
          name: `Mark ${categoryName}`,
          priority: 100,
          isActive: true,
          matchDescriptionPattern: suggestion.pattern,
          matchPatternKind: "contains",
          matchDirection: suggestion.direction,
          setCategoryId: suggestion.categoryId,
          setTxnType: suggestion.txnType,
        }),
        headers: { "Content-Type": "application/json" },
      });
      setRule(saved);
      onRuleCreated();
    } catch (error) {
      setProblem(errorText(error, "This rule was not saved. Retry."));
    } finally {
      setBusy(false);
    }
  }

  async function checkPastRows() {
    if (!rule) return;
    setBusy(true);
    setProblem(undefined);
    try {
      const result = await request<ClassifyResult>(`/classify?ruleId=${rule.id}&preview=true`, {
        method: "POST",
        timeoutMs: LONG_REQUEST_TIMEOUT_MS,
      });
      setPreview(result);
    } catch (error) {
      setProblem(errorText(error, "Past rows could not be checked. Retry."));
    } finally {
      setBusy(false);
    }
  }

  async function applyPastRows() {
    if (!rule || !preview) return;
    setBusy(true);
    setProblem(undefined);
    try {
      const result = await request<ClassifyResult>(`/classify?ruleId=${rule.id}`, {
        method: "POST",
        timeoutMs: LONG_REQUEST_TIMEOUT_MS,
      });
      setApplied(result);
    } catch (error) {
      setProblem(errorText(error, "Past rows were not updated. Retry."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="rule-suggestion">
      <span className="rule-describes">
        narration contains “{suggestion.pattern}”, {suggestion.direction} → {categoryName}
      </span>
      <span className="section-label">
        {suggestion.evidenceRows} corrections · {suggestion.affected} affected ·{" "}
        {suggestion.unclassified} unclassified
      </span>
      {problem && <p className="inline-alert" role="alert">{problem}</p>}
      {!rule && (
        <button disabled={busy} onClick={() => void create()} type="button">
          Create rule
        </button>
      )}
      {rule && !preview && (
        <button disabled={busy} onClick={() => void checkPastRows()} type="button">
          Check past rows
        </button>
      )}
      {preview && !applied && (
        <>
          <p role="status">
            {preview.changed} of {preview.considered} unclassified{" "}
            {preview.considered === 1 ? "row" : "rows"} would change. Checking changes nothing;
            nothing is applied until confirmed.
          </p>
          {preview.sample.length > 0 && (
            <ul className="rule-sample">
              {preview.sample.map((row) => (
                <li key={row.id}>
                  {row.bookedDate} · {row.descriptionRaw}
                </li>
              ))}
            </ul>
          )}
          {preview.changed > 0 && (
            <button disabled={busy} onClick={() => void applyPastRows()} type="button">
              Apply {preview.changed} {preview.changed === 1 ? "change" : "changes"}
            </button>
          )}
        </>
      )}
      {applied && (
        <p role="status">
          {applied.changed} of {applied.considered} unclassified{" "}
          {applied.considered === 1 ? "row" : "rows"} updated.
        </p>
      )}
    </li>
  );
}

function SuggestedRules({
  categories,
  onRuleCreated,
  request,
}: {
  categories: Array<CategoryRow>;
  onRuleCreated: () => void;
  request: typeof apiFetch;
}) {
  const [data, setData] = useState<RuleSuggestionsResponse>();
  const [problem, setProblem] = useState<string>();

  useEffect(() => {
    let current = true;
    void request<RuleSuggestionsResponse>("/rules/suggestions", { timeoutMs: LONG_REQUEST_TIMEOUT_MS })
      .then((result) => { if (current) setData(result); })
      .catch((error: unknown) => {
        if (current) setProblem(errorText(error, "Suggested rules could not be loaded."));
      });
    return () => { current = false; };
  }, [request]);

  if (problem) return <p className="inline-alert" role="alert">{problem}</p>;
  if (!data?.suggestions) return null;

  return (
    <section aria-labelledby="rules-suggestions-title" className="settings-block">
      <h2 id="rules-suggestions-title">Suggested rules</h2>
      <p className="lede">Derived only from your own corrections. Nothing here is created or applied automatically.</p>
      {data.suggestions.length === 0 && (
        <p className="lede">No suggestions yet — repeated corrections will appear here.</p>
      )}
      {data.suggestions.length > 0 && (
        <ul className="rule-suggestions-list">
          {data.suggestions.map((suggestion) => (
            <SuggestedRuleCard
              categories={categories}
              key={`${suggestion.pattern}-${suggestion.direction}-${suggestion.categoryId}-${suggestion.txnType}`}
              onRuleCreated={onRuleCreated}
              request={request}
              suggestion={suggestion}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * The section loads its own rules rather than being handed them.
 *
 * Deliberate: the settings loader is a fixed sequence of awaits and four existing tests count
 * it, so a fifth would make this task's diff about those tests rather than about rules. It is
 * also the honest shape — nothing outside this section reads a rule.
 */
export function RulesManager({ categories, request, onChanged }: Props) {
  const [rules, setRules] = useState<Array<RuleRow>>([]);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [preview, setPreview] = useState<{ affected: number; sample: Array<{ id: string; descriptionRaw: string; bookedDate: string; amount: string; currency: string }> }>();
  const [pass, setPass] = useState<{ considered: number; changed: number }>();
  const [problem, setProblem] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const reload = useCallback(async () => {
    try {
      const loaded = await request<Array<RuleRow>>("/rules");
      setRules(loaded);
      return loaded;
    } catch (error) {
      setProblem(errorText(error, "The rules could not be loaded. Retry."));
      return undefined;
    }
  }, [request]);

  useEffect(() => {
    // The state settles in the promise's continuation, never in the effect's own body: a
    // synchronous setState there re-renders before the effect has finished, which React
    // rightly calls a cascade.
    let current = true;
    void request<Array<RuleRow>>("/rules")
      .then((loaded) => {
        if (current) setRules(loaded);
      })
      .catch((error: unknown) => {
        if (current) setProblem(errorText(error, "The rules could not be loaded. Retry."));
      });
    return () => {
      current = false;
    };
  }, [request]);

  async function run<T>(work: () => Promise<T>, fallback: string): Promise<T | undefined> {
    setBusy(true);
    setProblem(undefined);
    try {
      return await work();
    } catch (error) {
      setProblem(errorText(error, fallback));
      return undefined;
    } finally {
      setBusy(false);
    }
  }

  async function check(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const result = await run(
      () =>
        request<{ affected: number; sample: Array<{ id: string; descriptionRaw: string; bookedDate: string; amount: string; currency: string }> }>(
          "/rules/preview",
          {
            method: "POST",
            body: JSON.stringify(body(draft)),
            headers: { "Content-Type": "application/json" },
          },
        ),
      "This rule could not be checked. Retry.",
    );
    if (result) setPreview(result);
  }

  async function create() {
    const saved = await run(
      () =>
        request<RuleRow>("/rules", {
          method: "POST",
          body: JSON.stringify(body(draft)),
          headers: { "Content-Type": "application/json" },
        }),
      "This rule was not saved. Retry.",
    );
    if (saved) {
      setDraft(EMPTY);
      setPreview(undefined);
      void reload();
      onChanged();
    }
  }

  async function setActive(rule: RuleRow, isActive: boolean) {
    const saved = await run(
      () =>
        request<RuleRow>(`/rules/${rule.id}`, {
          method: "PATCH",
          // A full replacement, as the endpoint expects: the screen holds the whole row, so
          // there is no absent-versus-null to resolve.
          body: JSON.stringify({ ...rule, isActive }),
          headers: { "Content-Type": "application/json" },
        }),
      "The rule was not changed. Retry.",
    );
    if (saved) {
      void reload();
      onChanged();
    }
  }

  async function remove(rule: RuleRow) {
    const done = await run(
      () => request<void>(`/rules/${rule.id}`, { method: "DELETE" }),
      "The rule was not deleted. Retry.",
    );
    if (done !== undefined) {
      void reload();
      onChanged();
    }
  }

  async function classify(previewOnly: boolean) {
    const result = await run(
      () =>
        request<{ considered: number; changed: number }>(
          `/classify${previewOnly ? "?preview=true" : ""}`,
          { method: "POST" },
        ),
      "The classification pass did not run. Retry.",
    );
    if (result) {
      setPass(result);
      if (!previewOnly) {
        void reload();
        onChanged();
      }
    }
  }

  return (
    <section aria-labelledby="rules-title" className="settings-block">
      <h2 id="rules-title">Rules</h2>
      <p className="lede">
        Evaluated in priority order, lowest first; the first rule that matches decides and the
        rest are not consulted. A rule you write always beats a seeded one.
      </p>

      {problem && <p className="inline-alert" role="alert">{problem}</p>}

      <ul className="rules-list">
        {rules.map((rule) => (
          <li key={rule.id}>
            <span className="rule-priority">{rule.priority}</span>
            <span className="rule-name">{rule.name ?? "Unnamed rule"}</span>
            <span className="rule-describes">{describe(rule, categories)}</span>
            <span className="section-label">{rule.origin}</span>
            <span className="section-label">applied {rule.timesApplied}×</span>
            <button
              disabled={busy}
              onClick={() => setActive(rule, !rule.isActive)}
              type="button"
            >
              {rule.isActive ? "Deactivate" : "Activate"}
            </button>
            <button disabled={busy} onClick={() => remove(rule)} type="button">
              Delete
            </button>
          </li>
        ))}
      </ul>
      {rules.length === 0 && <p className="lede">No rules yet.</p>}

      <form onSubmit={check}>
        <h3>Add a rule</h3>
        <label>
          Name
          <input
            onChange={(event) => setDraft({ ...draft, name: event.target.value })}
            value={draft.name}
          />
        </label>
        <label>
          Narration
          <input
            onChange={(event) =>
              setDraft({ ...draft, matchDescriptionPattern: event.target.value })
            }
            required
            value={draft.matchDescriptionPattern}
          />
        </label>
        <label>
          Match
          <select
            onChange={(event) =>
              setDraft({ ...draft, matchPatternKind: event.target.value as Draft["matchPatternKind"] })
            }
            value={draft.matchPatternKind}
          >
            {PATTERN_KINDS.map((kind) => (
              <option key={kind} value={kind}>{kind}</option>
            ))}
          </select>
        </label>
        <label>
          Direction
          <select
            onChange={(event) =>
              setDraft({ ...draft, matchDirection: event.target.value as Draft["matchDirection"] })
            }
            value={draft.matchDirection}
          >
            {DIRECTIONS.map((direction) => (
              <option key={direction} value={direction}>{direction}</option>
            ))}
          </select>
        </label>
        <label>
          Category
          <select
            onChange={(event) => setDraft({ ...draft, setCategoryId: event.target.value })}
            value={draft.setCategoryId}
          >
            <option value="">Leave uncategorised</option>
            {categories.map((category) => (
              <option key={category.id} value={category.id}>{category.name}</option>
            ))}
          </select>
        </label>
        <label>
          Priority
          <input
            onChange={(event) => setDraft({ ...draft, priority: event.target.value })}
            value={draft.priority}
          />
        </label>
        <button disabled={busy} type="submit">Check what this matches</button>
      </form>

      {preview && (
        <div className="rule-preview" role="status">
          <p>
            This rule matches <strong>{preview.affected}</strong>{" "}
            {preview.affected === 1 ? "transaction" : "transactions"} in the ledger. Checking
            changes nothing; nothing is created until you save.
          </p>
          {preview.sample.length > 0 && (
            <button onClick={() => setExpanded(!expanded)} type="button">
              {expanded ? "Hide" : "Show"} matched rows
            </button>
          )}
          {expanded && (
            <ul className="rule-sample">
              {preview.sample.map((row) => (
                <li key={row.id}>
                  <span>{row.bookedDate}</span>
                  <span>{row.descriptionRaw}</span>
                </li>
              ))}
            </ul>
          )}
          <button disabled={busy} onClick={create} type="button">Save this rule</button>
        </div>
      )}

      <div className="rule-pass">
        <h3>Classify what has no answer</h3>
        <p className="lede">
          Runs the rules over transactions that have no classification yet.{" "}
          <strong>
            It cannot change a transaction that already has one — not one you set by hand, and
            not one an earlier run decided.
          </strong>
        </p>
        <button disabled={busy} onClick={() => classify(true)} type="button">
          Check how many
        </button>
        {pass && (
          <p role="status">
            {pass.considered} {pass.considered === 1 ? "transaction has" : "transactions have"} no
            classification; the rules decide {pass.changed} of them.
          </p>
        )}
        {pass && pass.changed > 0 && (
          <button disabled={busy} onClick={() => classify(false)} type="button">
            Apply to {pass.changed}
          </button>
        )}
      </div>

      <RuleAnalyticsPanel request={request} />
      <SuggestedRules
        categories={categories}
        onRuleCreated={() => {
          void reload();
          onChanged();
        }}
        request={request}
      />
    </section>
  );
}
