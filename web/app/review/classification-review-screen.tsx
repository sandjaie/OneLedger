"use client";

import { useEffect, useState } from "react";

import { type apiFetch, errorText } from "@/lib/api/client";
import type {
  CategoryRow,
  ClassificationReviewGroup,
  ClassificationReviewResponse,
  DerivedChanges,
  KnowledgeBody,
  KnowledgeRow,
  OneledgerRoutesClassificationDecisionsPreviewResult as PreviewResult,
  PreviewBody,
  RunResult,
} from "@/types/api";

type Props = {
  categories: Array<CategoryRow>;
  request: typeof apiFetch;
};

const PAGE_SIZE = 50;

function groupsPath(offset: number) {
  return `/classification-review/groups?limit=${PAGE_SIZE}&offset=${offset}`;
}

const REASON_LABELS: Record<ClassificationReviewGroup["reason"], string> = {
  conflicting_knowledge: "Conflicting knowledge",
  conflicting_prior: "Conflicting history",
  excluded_role: "Excluded role",
  identity_unresolved: "Identity unresolved",
  low_confidence: "Low confidence",
  recognized_pending: "Ready to apply",
  unclassified: "Unclassified",
};

function key(prefix: string) {
  return `${prefix}-${crypto.randomUUID()}`;
}

function proposedChanges(group: ClassificationReviewGroup): DerivedChanges {
  const changes: DerivedChanges = {
    classifiedBy: group.proposedClassifiedBy as NonNullable<DerivedChanges["classifiedBy"]>,
    classificationConfidence: group.proposedConfidence,
    isReviewed: true,
  };
  if (group.proposedCategoryId !== null) changes.categoryId = group.proposedCategoryId;
  if (group.proposedMerchantId !== null) changes.merchantId = group.proposedMerchantId;
  if (group.proposedTxnType !== null) changes.txnType = group.proposedTxnType as DerivedChanges["txnType"];
  if (group.proposedPaymentMethod !== null) {
    changes.paymentMethod = group.proposedPaymentMethod as DerivedChanges["paymentMethod"];
  }
  return changes;
}

function ownerChanges(categoryId: string): DerivedChanges {
  return {
    categoryId,
    classifiedBy: "merchant_alias",
    classificationConfidence: "1.000",
    isReviewed: true,
  };
}

function previewBody(
  group: ClassificationReviewGroup,
  knowledgeVersionId: string,
  transactionIds: Array<string>,
  changes: DerivedChanges,
): PreviewBody {
  return {
    knowledgeVersionId,
    reason: `Owner reviewed ${group.description}`,
    items: transactionIds.map((transactionId) => ({
      transactionId,
      reason: group.explanation,
      changes,
    })),
  };
}

function knowledgeBody(
  group: ClassificationReviewGroup,
  categoryId: string,
  pattern: string,
): KnowledgeBody {
  return {
    displayName: pattern,
    kind: "unresolved",
    pattern,
    patternKind: pattern === group.description ? "exact" : "contains",
    role: "unresolved",
    priority: 100,
    sourceKind: "owner",
    attribution: "Owner review",
    evidenceNote: "Confirmed in grouped classification review.",
    matcherVersion: "owner-v1",
    direction: group.direction,
    accountId: group.accountId,
    categoryId,
    paymentMethod: group.currentPaymentMethod as KnowledgeBody["paymentMethod"],
  };
}

async function postJson<T>(request: typeof apiFetch, path: string, body: unknown) {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function GroupCard({
  categories,
  group,
  request,
}: {
  categories: Array<CategoryRow>;
  group: ClassificationReviewGroup;
  request: typeof apiFetch;
}) {
  const [categoryId, setCategoryId] = useState(group.proposedCategoryId ?? "");
  const [pattern, setPattern] = useState(group.description);
  const [preview, setPreview] = useState<PreviewResult>();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string>();
  const [problem, setProblem] = useState<string>();
  const [appliedRunId, setAppliedRunId] = useState<string>();

  const activeCategories = categories.filter((category) => category.isActive);
  const knownProposal = group.knowledgeVersionId !== null;
  const canRemember = !knownProposal && group.rememberable;
  const incompleteScope = group.transactionIdsTruncated;
  const proposedCategory = group.proposedCategoryName ?? "No category";

  async function createKnowledge(rememberedPattern = pattern) {
    return postJson<KnowledgeRow>(
      request,
      "/merchant-knowledge",
      knowledgeBody(group, categoryId, rememberedPattern),
    );
  }

  async function makePreview() {
    if (incompleteScope || (!knownProposal && !categoryId)) return;
    setBusy(true);
    setProblem(undefined);
    setMessage(undefined);
    try {
      let knowledgeVersionId = group.knowledgeVersionId;
      let changes = proposedChanges(group);
      if (knowledgeVersionId === null) {
        const knowledge = await createKnowledge();
        knowledgeVersionId = knowledge.knowledgeVersionId;
        changes = ownerChanges(categoryId);
      }
      const result = await postJson<PreviewResult>(
        request,
        "/classification-runs/preview",
        previewBody(group, knowledgeVersionId, group.transactionIds, changes),
      );
      setPreview(result);
    } catch (error) {
      setProblem(errorText(error, "The proposed changes could not be previewed."));
    } finally {
      setBusy(false);
    }
  }

  async function applyPreview() {
    if (!preview) return;
    setBusy(true);
    setProblem(undefined);
    try {
      const result = await postJson<RunResult>(
        request,
        `/classification-runs/${preview.runId}/apply`,
        { idempotencyKey: key("classification-apply") },
      );
      if (result.state === "applied") {
        setAppliedRunId(preview.runId);
        setMessage(`Applied to ${result.changed} transactions.`);
      } else {
        setMessage(
          `Nothing was applied; ${result.conflicted} conflicts and ${result.unresolved} unresolved.`,
        );
      }
    } catch (error) {
      setProblem(errorText(error, "The preview could not be applied."));
    } finally {
      setBusy(false);
    }
  }

  async function applyOne() {
    if (!categoryId || group.transactionIds.length === 0) return;
    setBusy(true);
    setProblem(undefined);
    setMessage(undefined);
    let knowledge: KnowledgeRow | undefined;
    try {
      // A one-row decision needs evidence for the guarded preview, but it must not persist the
      // statement wording. The inert exact pattern is revoked immediately after the apply.
      knowledge = await createKnowledge("Temporary owner decision");
      const onePreview = await postJson<PreviewResult>(
        request,
        "/classification-runs/preview",
        previewBody(
          group,
          knowledge.knowledgeVersionId,
          [group.transactionIds[0]],
          ownerChanges(categoryId),
        ),
      );
      const result = await postJson<RunResult>(
        request,
        `/classification-runs/${onePreview.runId}/apply`,
        { idempotencyKey: key("classification-one") },
      );
      if (result.state === "applied") {
        setMessage(`Applied to ${result.changed} transaction; the wording was not remembered.`);
      } else {
        setMessage("The one-row answer was not applied because the transaction changed.");
      }
    } catch (error) {
      setProblem(errorText(error, "The one-row answer could not be applied."));
    } finally {
      if (knowledge) {
        try {
          await request<void>(`/merchant-knowledge/${knowledge.aliasId}`, { method: "DELETE" });
        } catch {
          setProblem("The row was handled, but its temporary wording could not be forgotten.");
        }
      }
      setBusy(false);
    }
  }

  async function undo() {
    if (!appliedRunId) return;
    setBusy(true);
    setProblem(undefined);
    try {
      const result = await postJson<RunResult>(
        request,
        `/classification-runs/${appliedRunId}/undo`,
        { idempotencyKey: key("classification-undo"), reason: "Owner requested undo" },
      );
      setMessage(
        result.conflicted || result.unresolved
          ? `Undid ${result.changed} changes; ${result.conflicted + result.unresolved} could not be undone.`
          : `Undid ${result.changed} changes.`,
      );
      if (!result.conflicted && !result.unresolved) setAppliedRunId(undefined);
    } catch (error) {
      setProblem(errorText(error, "The application could not be undone."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="classification-card" aria-label={group.description}>
      <div className="classification-card-head">
        <div>
          <span className={`classification-reason classification-reason-${group.reason}`}>
            {REASON_LABELS[group.reason]}
          </span>
          <h2>{group.description}</h2>
        </div>
        <span className="classification-count">
          {group.occurrences} {group.occurrences === 1 ? "occurrence" : "occurrences"}
        </span>
      </div>

      <p className="classification-explanation">{group.explanation}</p>
      <dl className="classification-facts">
        <div><dt>Account</dt><dd>{group.accountName}</dd></div>
        <div><dt>Dates</dt><dd>{group.firstBookedDate} – {group.lastBookedDate}</dd></div>
        <div><dt>Direction</dt><dd>{group.direction}</dd></div>
        <div><dt>Current</dt><dd>{group.currentCategoryName ?? "Unclassified"}</dd></div>
        <div><dt>Economic role</dt><dd>{knownProposal && group.proposedTxnType && group.proposedTxnType !== group.currentTxnType ? `${group.currentTxnType} → ${group.proposedTxnType}` : group.currentTxnType}</dd></div>
        <div><dt>Payment method</dt><dd>{group.proposedPaymentMethod ?? group.currentPaymentMethod ?? "Not identified"}</dd></div>
        {knownProposal && <div><dt>Proposed</dt><dd>{proposedCategory}</dd></div>}
        {knownProposal && <div><dt>Confidence</dt><dd>{group.proposedConfidence}</dd></div>}
      </dl>

      {!knownProposal && (
        <div className="classification-answer-fields">
          <label className="classification-category">
            Category for {group.description}
            <select value={categoryId} onChange={(event) => setCategoryId(event.target.value)}>
              <option value="">Choose a category…</option>
              {activeCategories.map((category) => (
                <option key={category.id} value={category.id}>{category.name}</option>
              ))}
            </select>
          </label>
          {canRemember && (
            <label className="classification-category">
              Wording to remember
              <input value={pattern} onChange={(event) => setPattern(event.target.value)} />
              <small>Remove reference numbers or personal identifiers before saving a reusable answer.</small>
            </label>
          )}
        </div>
      )}

      {incompleteScope && (
        <p className="classification-warning">Narrow the date range before applying this group; the complete set of rows is not loaded.</p>
      )}
      {problem && <p className="inline-alert" role="alert">{problem}</p>}
      {message && <p className="classification-result" role="status">{message}</p>}
      {preview && !appliedRunId && (
        <p className="classification-preview" role="status">
          {preview.changed} can change · {preview.conflicted} conflicts · {preview.unresolved} unresolved
        </p>
      )}

      <div className="classification-actions">
        {(knownProposal || canRemember) && (
          <button
            className="text-action"
            disabled={busy || incompleteScope || (!knownProposal && (!categoryId || !pattern.trim()))}
            onClick={makePreview}
            type="button"
          >
            {knownProposal
              ? `Preview ${group.occurrences} proposed changes`
              : `Remember and preview ${group.occurrences}`}
          </button>
        )}
        {!knownProposal && (
          <button
            className="text-action"
            disabled={busy || !categoryId || group.transactionIds.length === 0}
            onClick={applyOne}
            type="button"
          >Apply to one only</button>
        )}
        {preview && !appliedRunId && (
          <button
            className="classification-primary"
            disabled={busy || preview.changed === 0 || preview.conflicted > 0}
            onClick={applyPreview}
            type="button"
          >Apply {preview.changed} changes</button>
        )}
        {appliedRunId && (
          <button className="text-action" disabled={busy} onClick={undo} type="button">
            Undo this application
          </button>
        )}
      </div>
    </article>
  );
}

export function ClassificationReviewScreen({ categories, request }: Props) {
  const [page, setPage] = useState<ClassificationReviewResponse>();
  const [loading, setLoading] = useState(true);
  const [problem, setProblem] = useState<string>();
  const [attempt, setAttempt] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreProblem, setMoreProblem] = useState<string>();

  useEffect(() => {
    let current = true;
    request<ClassificationReviewResponse>(groupsPath(0))
      .then((result) => { if (current) setPage(result); })
      .catch(() => { if (current) setProblem("The review queue could not be loaded."); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [attempt, request]);

  function retry() {
    setLoading(true);
    setProblem(undefined);
    setAttempt((value) => value + 1);
  }

  async function loadMore() {
    if (!page) return;
    setLoadingMore(true);
    setMoreProblem(undefined);
    try {
      const next = await request<ClassificationReviewResponse>(groupsPath(page.groups.length));
      setPage((current) => {
        if (!current) return next;
        const seen = new Set(current.groups.map((group) => group.groupKey));
        return {
          ...next,
          groups: [...current.groups, ...next.groups.filter((group) => !seen.has(group.groupKey))],
        };
      });
    } catch {
      setMoreProblem("More review groups could not be loaded.");
    } finally {
      setLoadingMore(false);
    }
  }

  const remaining = page ? Math.max(0, page.totalGroups - page.groups.length) : 0;

  return (
    <section className="classification-review" aria-labelledby="classification-review-title">
      <header className="classification-review-head">
        <div>
          <p className="classification-kicker">Household knowledge</p>
          <h1 id="classification-review-title">Review</h1>
          <p>Answer recurring classification questions once, then inspect the exact scope before anything changes.</p>
        </div>
        {page && (
          <div className="classification-summary" aria-label="Review queue summary">
            <strong>{page.totalGroups}</strong>
            <span>groups · {page.totalOccurrences} occurrences</span>
          </div>
        )}
      </header>

      {loading && <p className="classification-state">Loading review queue…</p>}
      {!loading && problem && (
        <div className="classification-state">
          <p className="inline-alert" role="alert">{problem}</p>
          <button className="text-action" onClick={retry} type="button">Try again</button>
        </div>
      )}
      {!loading && !problem && page?.candidateLimitReached && (
        <p className="classification-warning">The candidate scan limit was reached. Use a narrower date range before treating this queue as complete.</p>
      )}
      {!loading && !problem && page?.groups.length === 0 && (
        <p className="classification-empty">Nothing needs classification review.</p>
      )}
      {!loading && !problem && page && page.groups.length > 0 && (
        <>
          <div className="classification-list">
            {page.groups.map((group) => (
              <GroupCard categories={categories} group={group} key={group.groupKey} request={request} />
            ))}
          </div>
          {moreProblem && <p className="inline-alert" role="alert">{moreProblem}</p>}
          {remaining > 0 && (
            <button className="classification-load-more" disabled={loadingMore} onClick={loadMore} type="button">
              {loadingMore
                ? "Loading more groups…"
                : `Load ${Math.min(PAGE_SIZE, remaining)} more ${Math.min(PAGE_SIZE, remaining) === 1 ? "group" : "groups"}`}
            </button>
          )}
        </>
      )}
    </section>
  );
}
