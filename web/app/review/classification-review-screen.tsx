"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { type apiFetch, errorText } from "@/lib/api/client";
import { dateLabel } from "@/lib/format/date";
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
  RunRow,
} from "@/types/api";

type Props = {
  categories: Array<CategoryRow>;
  request: typeof apiFetch;
};

const PAGE_SIZE = 50;

type Sort = "default" | "impact";

function groupsPath(offset: number, sort: Sort) {
  const base = `/classification-review/groups?limit=${PAGE_SIZE}&offset=${offset}`;
  return sort === "impact" ? `${base}&sort=impact` : base;
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

function humanLabel(value: string | null) {
  return value ? value.replaceAll("_", " ").replace(/^./, (first) => first.toUpperCase()) : "Not identified";
}

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
  featured,
  group,
  request,
}: {
  categories: Array<CategoryRow>;
  featured: boolean;
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
      "/merchant-knowledge?pending=true",
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
        { idempotencyKey: key("classification-one"), activateKnowledge: false },
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
    <article className={`classification-card${featured ? " classification-card--focus" : ""}`} aria-label={group.description}>
      <div className="classification-card-head">
        <div>
          <Badge className={`classification-reason classification-reason-${group.reason}`} variant="outline">
            {REASON_LABELS[group.reason]}
          </Badge>
          <h2>{group.description}</h2>
        </div>
        <Badge className="classification-count" variant="secondary">
          {group.occurrences} {group.occurrences === 1 ? "occurrence" : "occurrences"}
        </Badge>
      </div>

      <p className="classification-explanation">{group.explanation}</p>

      {!knownProposal && (
        <div className="classification-answer-fields">
          <label className="classification-category">
            Category for {group.description}
            <NativeSelect value={categoryId} onChange={(event) => setCategoryId(event.target.value)}>
              <NativeSelectOption value="">Choose a category…</NativeSelectOption>
              {activeCategories.map((category) => (
                <NativeSelectOption key={category.id} value={category.id}>{category.name}</NativeSelectOption>
              ))}
            </NativeSelect>
          </label>
          {canRemember && (
            <label className="classification-category">
              Wording to remember
              <Input value={pattern} onChange={(event) => setPattern(event.target.value)} />
              <small>Remove reference numbers or personal identifiers before saving a reusable answer.</small>
            </label>
          )}
        </div>
      )}

      {incompleteScope && (
        <Alert variant="destructive"><AlertDescription>Narrow the date range before applying this group; the complete set of rows is not loaded.</AlertDescription></Alert>
      )}
      {problem && <Alert variant="destructive"><AlertDescription>{problem}</AlertDescription></Alert>}
      {message && <p className="classification-result" role="status">{message}</p>}
      {preview && !appliedRunId && (
        <p className="classification-preview" role="status">
          {preview.changed} can change · {preview.conflicted} conflicts · {preview.unresolved} unresolved
        </p>
      )}

      {(knownProposal || canRemember) && <div className="classification-actions">
          <Button
            disabled={busy || incompleteScope || (!knownProposal && (!categoryId || !pattern.trim()))}
            onClick={makePreview}
            type="button"
            variant={featured ? "default" : "outline"}
          >
            {knownProposal
              ? `Preview ${group.occurrences} proposed changes`
              : `Remember and preview ${group.occurrences}`}
          </Button>
      </div>}
      <h3 className="classification-evidence-title">Evidence and scope</h3>
      <dl className="classification-facts">
        <div><dt>Account</dt><dd>{group.accountName}</dd></div>
        <div><dt>Dates</dt><dd>{group.firstBookedDate} – {group.lastBookedDate}</dd></div>
        <div><dt>Direction</dt><dd>{humanLabel(group.direction)}</dd></div>
        <div><dt>Current</dt><dd>{group.currentCategoryName ?? "Unclassified"}</dd></div>
        <div><dt>Economic role</dt><dd>{knownProposal && group.proposedTxnType && group.proposedTxnType !== group.currentTxnType ? `${humanLabel(group.currentTxnType)} → ${humanLabel(group.proposedTxnType)}` : humanLabel(group.currentTxnType)}</dd></div>
        <div><dt>Payment method</dt><dd>{humanLabel(group.proposedPaymentMethod ?? group.currentPaymentMethod)}</dd></div>
        {knownProposal && <div><dt>Proposed</dt><dd>{proposedCategory}</dd></div>}
        {knownProposal && <div><dt>Confidence</dt><dd>{group.proposedConfidence}</dd></div>}
      </dl>
      {(!knownProposal || (preview && !appliedRunId) || appliedRunId) && <div className="classification-actions classification-commit-actions">
        {!knownProposal && (
          <Button
            disabled={busy || !categoryId || group.transactionIds.length === 0}
            onClick={applyOne}
            type="button"
            variant="outline"
          >Apply to one only</Button>
        )}
        {preview && !appliedRunId && (
          <Button
            disabled={busy || preview.changed === 0 || preview.conflicted > 0}
            onClick={applyPreview}
            type="button"
          >Apply {preview.changed} changes</Button>
        )}
        {appliedRunId && (
          <Button disabled={busy} onClick={undo} type="button" variant="outline">
            Undo this application
          </Button>
        )}
      </div>}
    </article>
  );
}

// Undo is atomic (V23 §undo): a conflict leaves nothing undone, so the only two shapes a
// result ever takes are "every item changed" (`state === "applied"`) or "nothing changed, here
// is why" (`state === "conflict"`, one reason per unresolved item). There is no partial case to
// render.
type UndoOutcome =
  | { kind: "result"; result: RunResult }
  | { kind: "error"; message: string };

function RecentRuns({ request }: { request: typeof apiFetch }) {
  const [runs, setRuns] = useState<Array<RunRow>>();
  const [problem, setProblem] = useState<string>();
  const [busyRunId, setBusyRunId] = useState<string>();
  const [outcomes, setOutcomes] = useState<Record<string, UndoOutcome>>({});

  const load = useCallback(async () => {
    try {
      const result = await request<Array<RunRow>>("/classification-runs?limit=20&offset=0");
      setRuns(result);
    } catch (error) {
      setProblem(errorText(error, "Recent changes could not be loaded."));
    }
  }, [request]);

  useEffect(() => {
    void load();
  }, [load]);

  async function undo(run: RunRow) {
    setBusyRunId(run.runId);
    setOutcomes((current) => {
      const next = { ...current };
      delete next[run.runId];
      return next;
    });
    try {
      const result = await postJson<RunResult>(
        request,
        `/classification-runs/${run.runId}/undo`,
        { idempotencyKey: key("classification-runs-undo"), reason: "Owner requested undo" },
      );
      setOutcomes((current) => ({ ...current, [run.runId]: { kind: "result", result } }));
      void load();
    } catch (error) {
      setOutcomes((current) => ({
        ...current,
        [run.runId]: {
          kind: "error",
          message: errorText(error, "This application could not be undone."),
        },
      }));
    } finally {
      setBusyRunId(undefined);
    }
  }

  if (problem) return <Alert variant="destructive"><AlertDescription>{problem}</AlertDescription></Alert>;
  if (!runs) return null;

  return (
    <section aria-labelledby="classification-recent-runs-title" className="classification-recent-runs">
      <h2 id="classification-recent-runs-title">Recent changes</h2>
      {runs.length === 0 && <p className="classification-empty">No applied changes yet.</p>}
      {runs.length > 0 && (
        <ul>
          {runs.map((run) => {
            const outcome = outcomes[run.runId];
            return (
              <li key={run.runId}>
                <span>{dateLabel(run.appliedAt.slice(0, 10))}</span>
                <span>{run.reason}</span>
                <span>{run.changed} {run.changed === 1 ? "change" : "changes"}</span>
                {run.knowledgeDisplayName && <span>{run.knowledgeDisplayName}</span>}
                {!run.undone && run.sourceRunId === null && (
                  <Button
                    disabled={busyRunId === run.runId}
                    onClick={() => undo(run)}
                    type="button"
                    variant="outline"
                  >Undo</Button>
                )}
                {outcome?.kind === "error" && (
                  <p className="inline-alert" role="alert">{outcome.message}</p>
                )}
                {outcome?.kind === "result" && outcome.result.state === "conflict" && (
                  <div role="alert">
                    <p>The application could not be undone.</p>
                    <ul>
                      {outcome.result.items.map((item) => (
                        <li key={item.transactionId}>{item.reason}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {outcome?.kind === "result" && outcome.result.state === "applied" && (
                  <p role="status">
                    Undid {outcome.result.changed} changes.{" "}
                    {run.knowledgeDisplayName && (
                      <>
                        The remembered merchant stays active — see{" "}
                        <Link href="/settings#merchant-knowledge-title">Settings › Merchant knowledge</Link>.
                      </>
                    )}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

export function ClassificationReviewScreen({ categories, request }: Props) {
  const [page, setPage] = useState<ClassificationReviewResponse>();
  const [loading, setLoading] = useState(true);
  const [problem, setProblem] = useState<string>();
  const [attempt, setAttempt] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreProblem, setMoreProblem] = useState<string>();
  const [sort, setSort] = useState<Sort>("default");

  useEffect(() => {
    let current = true;
    request<ClassificationReviewResponse>(groupsPath(0, sort))
      .then((result) => { if (current) setPage(result); })
      .catch(() => { if (current) setProblem("The review queue could not be loaded."); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [attempt, request, sort]);

  function retry() {
    setLoading(true);
    setProblem(undefined);
    setAttempt((value) => value + 1);
  }

  function changeSort(next: Sort) {
    setLoading(true);
    setProblem(undefined);
    setPage(undefined);
    setSort(next);
  }

  async function loadMore() {
    if (!page) return;
    setLoadingMore(true);
    setMoreProblem(undefined);
    try {
      const next = await request<ClassificationReviewResponse>(
        groupsPath(page.groups.length, sort),
      );
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
          <h1 id="classification-review-title">Classify</h1>
          <p>Review the next household question, preview its exact scope, then apply only when it is right.</p>
        </div>
        {page && (
          <div className="classification-summary" aria-label="Review queue summary">
            <strong>{page.totalGroups}</strong>
            <span>{page.totalGroups === 1 ? "group" : "groups"} · {page.totalOccurrences} occurrences</span>
          </div>
        )}
      </header>

      <label className="classification-sort">
        Sort
        <NativeSelect
          disabled={loading}
          onChange={(event) => changeSort(event.target.value as Sort)}
          value={sort}
        >
          <NativeSelectOption value="default">Most urgent first</NativeSelectOption>
          <NativeSelectOption value="impact">Largest amount first</NativeSelectOption>
        </NativeSelect>
      </label>

      {loading && <p className="classification-state">Loading review queue…</p>}
      {!loading && problem && (
        <div className="classification-state">
          <Alert variant="destructive"><AlertDescription>{problem}</AlertDescription></Alert>
          <Button onClick={retry} type="button" variant="outline">Try again</Button>
        </div>
      )}
      {!loading && !problem && page?.candidateLimitReached && (
        <Alert variant="destructive"><AlertDescription>The candidate scan limit was reached. Use a narrower date range before treating this queue as complete.</AlertDescription></Alert>
      )}
      {!loading && !problem && page?.groups.length === 0 && (
        <p className="classification-empty">Nothing needs classification review.</p>
      )}
      {!loading && !problem && page && page.groups.length > 0 && (
        <>
          <div className="classification-list">
            {page.groups.map((group, index) => (
              <GroupCard categories={categories} featured={index === 0} group={group} key={group.groupKey} request={request} />
            ))}
          </div>
          {moreProblem && <Alert variant="destructive"><AlertDescription>{moreProblem}</AlertDescription></Alert>}
          {remaining > 0 && (
            <Button disabled={loadingMore} onClick={loadMore} type="button" variant="outline">
              {loadingMore
                ? "Loading more groups…"
                : `Load ${Math.min(PAGE_SIZE, remaining)} more ${Math.min(PAGE_SIZE, remaining) === 1 ? "group" : "groups"}`}
            </Button>
          )}
        </>
      )}

      <RecentRuns request={request} />
    </section>
  );
}
