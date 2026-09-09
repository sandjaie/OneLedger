"use client";

import { useEffect, useState } from "react";

import { ApiError, type apiFetch } from "@/lib/api/client";
import { formatMoney } from "@/lib/money/format";
import type {
  CurrencyRow,
  HouseholdCurrency,
  ReviewBatch,
  ReviewGroup,
  ReviewRow,
} from "@/types/api";

type Props = {
  batchId: string;
  currencies: Array<CurrencyRow>;
  currency: HouseholdCurrency;
  request: typeof apiFetch;
};

// `import_batch.status`. A batch that has committed is a record, not a working set — the API
// refuses a decision on one, and the screen must not offer an action it would refuse.
const REVIEWABLE = "awaiting_review";
const RECONCILED = "matched";

// V13's staged statuses. `duplicate` collides with something already committed, which is why
// accepting it is a deliberate override rather than an ordinary tick; `accepted` is what the
// commit imports.
const DUPLICATE = "duplicate";
const ACCEPTED = "accepted";

// What each group means, in the owner's words rather than the column's. A status without an
// entry falls back to itself: M4 extends this vocabulary, and an unlabelled group is better
// than a group silently omitted.
const GROUP_LABEL: Record<string, string> = {
  needs_review: "Needs review",
  new: "New",
  near_duplicate: "Possible duplicate",
  accepted: "Accepted",
  rejected: "Rejected",
  duplicate: "Already in the ledger",
};

function label(status: string) {
  return GROUP_LABEL[status] ?? status;
}

function detail(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.detail : fallback;
}

function accepted(batch: ReviewBatch) {
  return batch.groups.find((group) => group.status === ACCEPTED)?.count ?? 0;
}

export function ReviewScreen({ batchId, currencies, currency, request }: Props) {
  const [batch, setBatch] = useState<ReviewBatch>();
  const [selected, setSelected] = useState<Array<string>>([]);
  const [problem, setProblem] = useState<string>();
  const [imported, setImported] = useState<number>();
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);

  // The tree's own fetch shape (`workspace.tsx`): the effect does not set state itself, and a
  // `current` flag drops an answer that arrives after unmount. Re-reading is a counter rather
  // than a callback the effect depends on, so a decision reloads by asking for the next
  // attempt instead of by calling into the effect's body.
  useEffect(() => {
    let current = true;
    request<ReviewBatch>(`/import/${batchId}`)
      .then((next) => {
        if (!current) return;
        setBatch(next);
        // The selection names rows by id and a decision moves them between groups. Clearing it
        // is what stops a second click from re-deciding rows already dealt with.
        setSelected([]);
      })
      .catch((error: unknown) => {
        if (!current) return;
        setProblem(detail(error, "This batch could not be loaded."));
      });
    return () => {
      current = false;
    };
  }, [attempt, batchId, request]);

  function reload() {
    setAttempt((value) => value + 1);
  }

  function toggle(id: string) {
    setSelected((current) =>
      current.includes(id) ? current.filter((each) => each !== id) : [...current, id],
    );
  }

  function selectGroup(group: ReviewGroup, ticked: boolean) {
    const ids = group.rows.map((row) => row.id);
    setSelected((current) =>
      ticked
        ? [...current, ...ids.filter((id) => !current.includes(id))]
        : current.filter((id) => !ids.includes(id)),
    );
  }

  async function decide(decision: string) {
    setBusy(true);
    setProblem(undefined);
    try {
      await request<{ updated: number }>(`/import/${batchId}/rows`, {
        method: "PATCH",
        body: JSON.stringify({ rowIds: selected, decision }),
        headers: { "Content-Type": "application/json" },
      });
      reload();
    } catch (error) {
      setProblem(detail(error, "Those rows could not be decided."));
    } finally {
      setBusy(false);
    }
  }

  async function commit() {
    setBusy(true);
    setProblem(undefined);
    try {
      const result = await request<{ rowsImported: number }>(`/import/${batchId}/commit`, {
        method: "POST",
      });
      setImported(result.rowsImported);
      reload();
    } catch (error) {
      // V14's own sentence, written for the owner: which account failed to reconcile and by
      // how much. The whole commit rolled back, so the batch is exactly as it was.
      setProblem(detail(error, "This batch could not be imported."));
    } finally {
      setBusy(false);
    }
  }

  if (!batch) {
    return (
      <section className="workspace-content" aria-labelledby="review-title">
        <h1 className="workspace-title" id="review-title">Review</h1>
        {problem ? (
          <p className="inline-alert" role="alert">{problem}</p>
        ) : (
          <p className="lede" role="status">Loading this batch…</p>
        )}
      </section>
    );
  }

  const open = batch.status === REVIEWABLE;
  const ready = accepted(batch);
  const shown =
    currencies.find(({ code }) => code === batch.groups[0]?.rows[0]?.currency) ?? currency;

  return (
    <section className="workspace-content" aria-labelledby="review-title">
      <p className="eyebrow">Statement import</p>
      <h1 className="workspace-title" id="review-title">Review</h1>

      <Banner batch={batch} currency={shown} />

      {problem && <p className="inline-alert" role="alert">{problem}</p>}
      {imported !== undefined && (
        <p className="import-verdict" role="status">
          {imported} {imported === 1 ? "row" : "rows"} imported.
        </p>
      )}

      {open ? (
        <div className="review-actions">
          <button
            className="primary-action"
            disabled={!selected.length || busy}
            onClick={() => decide(ACCEPTED)}
            type="button"
          >Accept selected ({selected.length})</button>
          <button
            className="text-action"
            disabled={!selected.length || busy}
            onClick={() => decide("rejected")}
            type="button"
          >Reject selected ({selected.length})</button>
          {ready > 0 ? (
            <button className="primary-action" disabled={busy} onClick={commit} type="button">
              Import {ready} {ready === 1 ? "row" : "rows"}
            </button>
          ) : (
            <p className="review-hint">Accept some rows before importing.</p>
          )}
        </div>
      ) : (
        <p className="review-hint" role="status">
          This batch is already imported and can no longer be decided.
        </p>
      )}

      {batch.groups.map((group) => (
        <Group
          currency={shown}
          group={group}
          key={group.status}
          onSelectGroup={(ticked) => selectGroup(group, ticked)}
          onToggle={toggle}
          open={open}
          selected={selected}
        />
      ))}
    </section>
  );
}

function Banner({ batch, currency }: { batch: ReviewBatch; currency: HouseholdCurrency }) {
  const reconciliation = batch.reconciliation;
  const reconciled = reconciliation.status === RECONCILED;
  return (
    <div className={reconciled ? "import-verdict" : "inline-alert"} role="status">
      <strong>{reconciled ? "Reconciled" : `Reconciliation: ${reconciliation.status}`}</strong>
      {reconciliation.declaredOpening !== null && (
        <> · opening {formatMoney(reconciliation.declaredOpening, currency)}</>
      )}
      {reconciliation.declaredClosing !== null && (
        <> · closing {formatMoney(reconciliation.declaredClosing, currency)}</>
      )}
      <> · {batch.rowsExtracted} rows extracted</>
      {reconciliation.message && <> · {reconciliation.message}</>}
    </div>
  );
}

function Group({
  currency,
  group,
  onSelectGroup,
  onToggle,
  open,
  selected,
}: {
  currency: HouseholdCurrency;
  group: ReviewGroup;
  onSelectGroup: (ticked: boolean) => void;
  onToggle: (id: string) => void;
  open: boolean;
  selected: Array<string>;
}) {
  const colliding = group.status === DUPLICATE;
  const ticked = group.rows.every((row) => selected.includes(row.id));

  return (
    <section className="review-group" aria-labelledby={`group-${group.status}`}>
      <div className="review-group-head">
        <h3 id={`group-${group.status}`}>{group.status} · {label(group.status)} ({group.count})</h3>
        {/* No wholesale tick on the colliding group. Each of those rows already exists in the
            ledger, so accepting one is a confirmed-distinct override — a deliberate act per
            row, not a group the owner can sweep up without meaning to. */}
        {open && !colliding && (
          <label className="review-select-all">
            <input
              aria-label={`Select all ${group.count} ${group.status}`}
              checked={ticked}
              onChange={(event) => onSelectGroup(event.target.checked)}
              type="checkbox"
            />
            <span>Select all</span>
          </label>
        )}
      </div>

      {colliding && (
        <p className="review-hint">
          Each of these matches a transaction already in the ledger and is excluded by default.
          Accepting one imports it as a separate transaction.
        </p>
      )}

      <ul className="review-rows">
        {group.rows.map((row) => (
          <Row
            currency={currency}
            key={row.id}
            onToggle={onToggle}
            open={open}
            row={row}
            selected={selected.includes(row.id)}
          />
        ))}
      </ul>
    </section>
  );
}

function Row({
  currency,
  onToggle,
  open,
  row,
  selected,
}: {
  currency: HouseholdCurrency;
  onToggle: (id: string) => void;
  open: boolean;
  row: ReviewRow;
  selected: boolean;
}) {
  return (
    <li aria-label={`Row ${row.sourceRowNumber}`} className="review-row">
      {open && (
        <input
          aria-label={`Row ${row.sourceRowNumber}`}
          checked={selected}
          onChange={() => onToggle(row.id)}
          type="checkbox"
        />
      )}
      <span className="review-date">{row.bookedDate}</span>
      <span className="review-description">{row.descriptionRaw}</span>
      {/* The statement's own amount, formatted from the string the API sent. The frozen
          amount_base travels with the row and is shown beside it rather than recomputed. */}
      <span className="review-amount">{formatMoney(row.amount, currency)}</span>
      <span className="section-label">{row.proposedTxnType ?? "unclassified"}</span>
      {row.duplicateOfTransactionId && (
        <span className="section-label">Already in the ledger</span>
      )}
    </li>
  );
}
