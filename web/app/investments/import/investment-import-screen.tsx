"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { reasonText } from "@/app/investments/review-reasons";
import { SecurityForm } from "@/app/investments/security-form";
import { INVESTMENT_ACCOUNT_TYPES } from "@/lib/accounts/types";
import { ApiError, LONG_REQUEST_TIMEOUT_MS, type apiFetch, errorText } from "@/lib/api/client";
import type {
  AccountRow,
  CommittedGroupResponse,
  GroupResponse,
  InvestmentCancelResponse,
  InvestmentCommitResponse,
  InvestmentParseResponse,
  ReasonResponse,
  SecurityCandidate,
  StagedGroupStatus,
  TargetResponse,
} from "@/types/api";

type Props = {
  accounts: Array<AccountRow>;
  /** Present only when the page loaded an existing import id — resuming skips the upload step
   * and goes straight to the staged review (Task 17 contract). */
  importId?: string;
  request: typeof apiFetch;
};

// `uploaded_pages`' own sentence for a file it cannot open, shared with `import-screen.tsx` — a
// 422 covers every parse refusal this route makes, and only this one is something the owner can
// answer with a password.
const LOCKED = "PDF is encrypted or the password is incorrect";


const GROUP_STATUS_TEXT: Record<StagedGroupStatus, string> = {
  draft: "Not yet staged",
  ready: "Ready to commit",
  blocked: "Blocked — resolve the reasons below",
  committed: "Committed",
  cancelled: "Cancelled",
};

const COMMIT_REASON_TEXT: Record<string, string> = {
  open_review: "This group still has an open review item.",
  missing_fx_rate: "No exchange rate is recorded for this group's date.",
  database_refusal: "The ledger refused this group's entries.",
};

// `investment_import.status`. Nothing left to decide once one of these is reached.
const TERMINAL: ReadonlySet<string> = new Set(["completed", "cancelled", "failed"]);

type Resolution = { accountId: string; securityId: string };

function reasonsList(reasons: Array<ReasonResponse>) {
  if (reasons.length === 0) return null;
  return (
    <ul className="investment-import-reasons">
      {reasons.map((reason, index) => (
        <li key={index}>
          {reasonText(reason.code)}
          {reason.pageNumber !== null && ` (page ${reason.pageNumber}${reason.rowNumber !== null ? `, row ${reason.rowNumber}` : ""})`}
        </li>
      ))}
    </ul>
  );
}

// Units and an amount are two different facts and never share one label: "10.00000000 units"
// is a count, "1000.0000 INR" is money, and a statement row can carry either, both or neither.
function draftAmountText(draft: TargetResponse["drafts"][number]) {
  const amount = draft.grossAmount ?? draft.netAmount ?? null;
  const parts: Array<string> = [];
  if (draft.units !== null) parts.push(`${draft.units} units`);
  if (amount !== null) parts.push(`${amount} ${draft.currency}`);
  return parts.join(" · ");
}

function draftLine(draft: TargetResponse["drafts"][number]) {
  return [draft.eventDate, draft.eventKind.replaceAll("_", " "), draftAmountText(draft)]
    .filter(Boolean)
    .join(" · ");
}

function checkpointAmountText(checkpoint: TargetResponse["checkpoints"][number]) {
  const parts: Array<string> = [];
  if (checkpoint.reportedUnits !== null) parts.push(`${checkpoint.reportedUnits} units`);
  if (checkpoint.reportedValueNative !== null) {
    parts.push(
      checkpoint.currency
        ? `${checkpoint.reportedValueNative} ${checkpoint.currency}`
        : checkpoint.reportedValueNative,
    );
  }
  return parts.join(" · ");
}

function checkpointLine(checkpoint: TargetResponse["checkpoints"][number]) {
  return [`As of ${checkpoint.asOfDate}`, checkpointAmountText(checkpoint)]
    .filter(Boolean)
    .join(" · ");
}

// A `Map` rather than a plain object: an object's keys are always strings, and turning one back
// into the integer `sourceGroupNumber` the API expects would need `Number()` — banned in this
// codebase because money is a string on the wire. A `Map` keeps the number a number throughout.
function resolutionsBody(resolutions: Map<number, Resolution>) {
  return JSON.stringify(
    Array.from(resolutions.entries())
      .filter(([, value]) => value.accountId && value.securityId)
      .map(([sourceGroupNumber, value]) => ({
        sourceGroupNumber,
        accountId: value.accountId,
        securityId: value.securityId,
      })),
  );
}

function everyTargetResolved(groups: Array<GroupResponse>, resolutions: Map<number, Resolution>) {
  return groups.every((group) =>
    group.targets.every(
      (target) =>
        (target.accountId ?? resolutions.get(target.sourceGroupNumber)?.accountId) &&
        (target.securityId ?? resolutions.get(target.sourceGroupNumber)?.securityId),
    ),
  );
}

export function InvestmentImportScreen({ accounts, importId, request }: Props) {
  const investmentAccounts = accounts.filter(
    (account) => account.isActive && INVESTMENT_ACCOUNT_TYPES.has(account.accountType),
  );

  const [file, setFile] = useState<File>();
  const [password, setPassword] = useState("");
  const [locked, setLocked] = useState(false);
  const [resolutions, setResolutions] = useState<Map<number, Resolution>>(new Map());
  const [preview, setPreview] = useState<InvestmentParseResponse>();
  const [imported, setImported] = useState<InvestmentParseResponse>();
  const [commitResult, setCommitResult] = useState<InvestmentCommitResponse>();
  const [cancelled, setCancelled] = useState(false);
  const [confirmingCancel, setConfirmingCancel] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string>();
  const [loading, setLoading] = useState(Boolean(importId));

  // Resuming: `/investments/import/{importId}` loads the staged state directly and skips upload.
  useEffect(() => {
    if (!importId) return;
    let current = true;
    request<InvestmentParseResponse>(`/investment-imports/${importId}`)
      .then((result) => {
        if (current) setImported(result);
      })
      .catch((error: unknown) => {
        if (current) setProblem(errorText(error, "This import could not be loaded."));
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [importId, request]);

  function chooseFile(files: FileList | null) {
    const next = files?.[0];
    setFile(next);
    setPassword("");
    setLocked(false);
    setPreview(undefined);
    setResolutions(new Map());
    setProblem(undefined);
  }

  function setResolution(groupNumber: number, change: Partial<Resolution>) {
    setResolutions((current) => {
      const next = new Map(current);
      const existing = next.get(groupNumber) ?? { accountId: "", securityId: "" };
      next.set(groupNumber, { ...existing, ...change });
      return next;
    });
  }

  async function check() {
    if (!file) return;
    setBusy(true);
    setProblem(undefined);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("stage", "false");
      form.append("resolutions", resolutionsBody(resolutions));
      if (password) form.append("password", password);
      const result = await request<InvestmentParseResponse>("/investment-imports/parse", {
        method: "POST",
        body: form,
        timeoutMs: LONG_REQUEST_TIMEOUT_MS,
      });
      setPreview(result);
      setLocked(false);
    } catch (error) {
      const message = errorText(error, "This statement could not be checked.");
      setProblem(message);
      if (message === LOCKED) setLocked(true);
    } finally {
      setBusy(false);
    }
  }

  async function stage() {
    if (!file || !preview) return;
    setBusy(true);
    setProblem(undefined);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("stage", "true");
      form.append("resolutions", resolutionsBody(resolutions));
      if (password) form.append("password", password);
      const result = await request<InvestmentParseResponse>("/investment-imports/parse", {
        method: "POST",
        body: form,
        timeoutMs: LONG_REQUEST_TIMEOUT_MS,
      });
      if (result.staged) {
        setImported(result);
        setPassword("");
      } else {
        setPreview(result);
        setProblem("This statement was not staged. Resolve what is flagged below and try again.");
      }
    } catch (error) {
      setProblem(errorText(error, "This statement could not be staged."));
    } finally {
      setBusy(false);
    }
  }

  async function refresh(id: string) {
    try {
      const result = await request<InvestmentParseResponse>(`/investment-imports/${id}`);
      setImported(result);
    } catch (error) {
      setProblem(errorText(error, "This import could not be reloaded."));
    }
  }

  async function commit() {
    if (!imported?.importId || !imported.expectedState) return;
    setBusy(true);
    setProblem(undefined);
    try {
      const result = await request<InvestmentCommitResponse>(
        `/investment-imports/${imported.importId}/commit`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ expectedState: imported.expectedState }),
          timeoutMs: LONG_REQUEST_TIMEOUT_MS,
        },
      );
      setCommitResult(result);
      await refresh(imported.importId);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setProblem("This import changed since it was loaded. It has been refreshed below.");
        await refresh(imported.importId);
      } else {
        setProblem(errorText(error, "This import could not be committed."));
      }
    } finally {
      setBusy(false);
    }
  }

  async function cancelImport() {
    if (!imported?.importId) return;
    setBusy(true);
    setProblem(undefined);
    try {
      const result = await request<InvestmentCancelResponse>(
        `/investment-imports/${imported.importId}/cancel`,
        { method: "POST" },
      );
      setImported((current) => (current ? { ...current, status: result.status } : current));
      setCancelled(true);
      setConfirmingCancel(false);
    } catch (error) {
      setProblem(errorText(error, "This import could not be cancelled."));
      setConfirmingCancel(false);
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <section className="workspace-content" aria-labelledby="investment-import-title">
        <h1 className="workspace-title" id="investment-import-title">Import investments</h1>
        <p className="lede" role="status">Loading this import…</p>
      </section>
    );
  }

  // Once staged (fresh or resumed), the review list replaces the upload form entirely — there is
  // nothing left to upload for an import id already on the server.
  if (imported) {
    const committable = imported.groups.some((group) => group.status === "ready");
    const terminal = TERMINAL.has(imported.status);
    return (
      <section className="workspace-content" aria-labelledby="investment-import-title">
        <p className="eyebrow">Statement import</p>
        <h1 className="workspace-title" id="investment-import-title">Review investment import</h1>
        <p className="lede">
          {imported.totalGroupCount} groups · {imported.totalDraftCount} rows ·{" "}
          {imported.totalBlockerCount} blocking {imported.totalBlockerCount === 1 ? "reason" : "reasons"}
        </p>

        {problem && <p className="inline-alert" role="alert">{problem}</p>}
        {cancelled && <p className="import-verdict" role="status">This import was cancelled. Nothing reached your holdings.</p>}
        {reasonsList(imported.reasons)}

        {commitResult && (
          <div className="import-verdict" role="status">
            <p>Commit result:</p>
            <ul className="investment-import-reasons">
              {commitResult.groups.map((group) => (
                <li key={group.groupNumber}>
                  Group {group.groupNumber}: {group.status}
                  {group.reason && ` — ${commitReasonText(group.reason)}`}
                  {group.status === "committed" && (
                    <> · <Link href="/investments">View in portfolio</Link></>
                  )}
                </li>
              ))}
            </ul>
            <p>
              <Link href="/investments">View the portfolio</Link>
              {" · "}
              <Link href="/settings">Settings &rsaquo; Data quality</Link>
            </p>
          </div>
        )}

        {!terminal && (
          <div className="review-actions">
            <button
              className="primary-action"
              disabled={busy || !committable}
              onClick={() => void commit()}
              type="button"
            >Commit ready groups</button>
            <Cancel
              busy={busy}
              confirming={confirmingCancel}
              onCancel={() => setConfirmingCancel(false)}
              onConfirm={() => void cancelImport()}
              onStart={() => setConfirmingCancel(true)}
            />
          </div>
        )}

        {imported.groups.map((group) => (
          <ImportedGroup key={group.groupNumber} group={group} />
        ))}

        <p className="review-hint">
          This import can be resumed at{" "}
          <a href={`/investments/import/${imported.importId}`}>/investments/import/{imported.importId}</a>.
        </p>
      </section>
    );
  }

  if (investmentAccounts.length === 0) {
    return (
      <section className="workspace-content investment-entry-empty" aria-labelledby="investment-import-title">
        <p className="section-label">Account required</p>
        <h1 className="workspace-title" id="investment-import-title">Set up the holding container first</h1>
        <p>An investment account is required before importing a statement.</p>
      </section>
    );
  }

  return (
    <section className="workspace-content" aria-labelledby="investment-import-title">
      <p className="eyebrow">Statement import</p>
      <h1 className="workspace-title" id="investment-import-title">Import investments</h1>
      <p className="lede">
        Upload a detailed investment statement. Nothing reaches your holdings until every group
        is resolved, staged and committed.
      </p>

      <div className="import-controls">
        <label className="file-field">
          <span>Statement file</span>
          <input
            accept="application/pdf,.pdf"
            disabled={busy}
            onChange={(event) => chooseFile(event.target.files)}
            type="file"
          />
        </label>
        {locked && (
          <label className="password-field">
            <span>Password for this statement</span>
            <input
              autoComplete="new-password"
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              value={password}
            />
            <small>Used for this file only, for this check. It is not saved anywhere.</small>
          </label>
        )}
        <button className="primary-action" disabled={!file || busy} onClick={() => void check()} type="button">
          {busy ? "Checking…" : "Check statement"}
        </button>
      </div>

      {problem && <p className="inline-alert" role="alert">{problem}</p>}

      {preview && (
        <>
          <p className="lede">
            {preview.totalGroupCount} groups · {preview.totalDraftCount} rows ·{" "}
            {preview.totalBlockerCount} blocking {preview.totalBlockerCount === 1 ? "reason" : "reasons"}
          </p>
          {reasonsList(preview.reasons)}
          {preview.previewTruncated && (
            <p className="inline-alert" role="alert">
              This statement has more groups than the preview shows; staging is unavailable for it
              here.
            </p>
          )}
          {preview.groups.map((group) => (
            <PreviewGroup
              accounts={investmentAccounts}
              busy={busy}
              group={group}
              key={group.groupNumber}
              onResolve={setResolution}
              request={request}
              resolutions={resolutions}
            />
          ))}
          <button
            className="primary-action"
            disabled={
              busy
              || !preview.supported
              || preview.previewTruncated
              || !everyTargetResolved(preview.groups, resolutions)
            }
            onClick={() => void stage()}
            type="button"
          >{busy ? "Staging…" : "Stage import"}</button>
        </>
      )}
    </section>
  );
}

function commitReasonText(reason: NonNullable<CommittedGroupResponse["reason"]>) {
  return COMMIT_REASON_TEXT[reason] ?? reason.replaceAll("_", " ");
}

function Cancel({
  busy,
  confirming,
  onCancel,
  onConfirm,
  onStart,
}: {
  busy: boolean;
  confirming: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  onStart: () => void;
}) {
  if (!confirming) {
    return (
      <button className="text-action" disabled={busy} onClick={onStart} type="button">
        Cancel this import
      </button>
    );
  }
  return (
    <div className="review-discard" role="group" aria-label="Confirm cancelling this import">
      <p className="review-hint">Cancel this import? Nothing staged from it will reach your holdings.</p>
      <button className="text-action" disabled={busy} onClick={onConfirm} type="button">Cancel it</button>
      <button className="text-action" disabled={busy} onClick={onCancel} type="button">Keep it</button>
    </div>
  );
}

function PreviewGroup({
  accounts,
  busy,
  group,
  onResolve,
  request,
  resolutions,
}: {
  accounts: Array<AccountRow>;
  busy: boolean;
  group: GroupResponse;
  onResolve: (groupNumber: number, change: Partial<Resolution>) => void;
  request: typeof apiFetch;
  resolutions: Map<number, Resolution>;
}) {
  return (
    <section className="investment-import-group" aria-label={`Group ${group.groupNumber}`}>
      <h3>Group {group.groupNumber}</h3>
      {reasonsList(group.reasons)}
      {group.targets.map((target) => (
        <PreviewTarget
          accounts={accounts}
          busy={busy}
          key={target.sourceGroupNumber}
          onResolve={onResolve}
          request={request}
          resolution={resolutions.get(target.sourceGroupNumber)}
          target={target}
        />
      ))}
    </section>
  );
}

function PreviewTarget({
  accounts,
  busy,
  onResolve,
  request,
  resolution,
  target,
}: {
  accounts: Array<AccountRow>;
  busy: boolean;
  onResolve: (groupNumber: number, change: Partial<Resolution>) => void;
  request: typeof apiFetch;
  resolution: Resolution | undefined;
  target: TargetResponse;
}) {
  const [security, setSecurity] = useState<SecurityCandidate>();
  const needsAccount = target.accountId === null;
  const needsSecurity = target.securityId === null;

  function chooseSecurity(next: SecurityCandidate | undefined) {
    setSecurity(next);
    onResolve(target.sourceGroupNumber, { securityId: next?.id ?? "" });
  }

  return (
    <div className="investment-import-target">
      <dl>
        <div><dt>Account</dt><dd>{target.accountMask ?? "Not yet resolved"}</dd></div>
        <div><dt>Security</dt><dd>{target.securityLabel ?? "Not yet resolved"}{target.isin && ` · ${target.isin}`}</dd></div>
      </dl>

      {target.drafts.length > 0 && (
        <ul className="investment-import-rows">
          {target.drafts.map((draft, index) => <li key={index}>{draftLine(draft)}</li>)}
        </ul>
      )}
      {target.checkpoints.length > 0 && (
        <ul className="investment-import-rows">
          {target.checkpoints.map((checkpoint, index) => <li key={index}>{checkpointLine(checkpoint)}</li>)}
        </ul>
      )}
      {reasonsList(target.reasons)}

      {needsAccount && (
        <label className="select-field">
          <span>Account</span>
          <select
            disabled={busy}
            onChange={(event) => onResolve(target.sourceGroupNumber, { accountId: event.target.value })}
            value={resolution?.accountId ?? ""}
          >
            <option value="">Choose an account</option>
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>{account.name}</option>
            ))}
          </select>
        </label>
      )}

      {needsSecurity && (
        <SecurityForm
          disabled={busy}
          onChange={chooseSecurity}
          request={request}
          security={security}
          suggestedIsin={target.isin}
          suggestedLabel={target.securityLabel}
          suggestedSecurityId={target.securityCandidates[0]}
        />
      )}
    </div>
  );
}

function ImportedGroup({ group }: { group: GroupResponse }) {
  return (
    <section className="investment-import-group" aria-label={`Group ${group.groupNumber}`}>
      <h3>
        Group {group.groupNumber}
        {group.status && <span className="section-label"> · {GROUP_STATUS_TEXT[group.status]}</span>}
      </h3>
      {reasonsList(group.reasons)}
      {group.targets.map((target, index) => (
        <div className="investment-import-target" key={index}>
          <dl>
            <div><dt>Account</dt><dd>{target.accountMask ?? "—"}</dd></div>
            <div><dt>Security</dt><dd>{target.securityLabel ?? "—"}{target.isin && ` · ${target.isin}`}</dd></div>
          </dl>
          {target.drafts.length > 0 && (
            <ul className="investment-import-rows">
              {target.drafts.map((draft, draftIndex) => <li key={draftIndex}>{draftLine(draft)}</li>)}
            </ul>
          )}
          {target.checkpoints.length > 0 && (
            <ul className="investment-import-rows">
              {target.checkpoints.map((checkpoint, checkpointIndex) => (
                <li key={checkpointIndex}>{checkpointLine(checkpoint)}</li>
              ))}
            </ul>
          )}
          {reasonsList(target.reasons)}
        </div>
      ))}
    </section>
  );
}
