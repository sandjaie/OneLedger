"use client";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";

import { useEffect, useState } from "react";

import { LONG_REQUEST_TIMEOUT_MS, type apiFetch, errorText } from "@/lib/api/client";
import { formatMoney, moneyText } from "@/lib/money/format";
import type {
  AccountRow,
  CurrencyRow,
  HouseholdCurrency,
  HouseholdResponse,
  ReviewBatch,
  ReviewGroup,
  ReviewRow,
} from "@/types/api";

type Props = {
  accounts: Array<AccountRow>;
  batchId: string;
  currencies: Array<CurrencyRow>;
  currency: HouseholdCurrency;
  household: HouseholdResponse;
  request: typeof apiFetch;
};

/** The frozen figure that belongs under the currency being displayed, or nothing.
 *
 * `multi-currency-and-fx.html` §2: the toggle **swaps a column, it does not recompute**. Both
 * figures were frozen at staging — `amount_base` against the household's base currency and
 * `amount_secondary` against its secondary — so showing the right one is a choice of column, and
 * formatting either with whatever the toggle happens to be prints real money under the wrong
 * symbol and the wrong grouping.
 *
 * Nothing where the row is already in the displayed currency: converting a figure into its own
 * currency says nothing, and printing an equal second figure beside it under one symbol invites
 * the reader to think they are two. `transactions-screen.tsx` reads the pair the same way.
 */
function converted(row: ReviewRow, household: HouseholdResponse, currency: HouseholdCurrency) {
  if (row.currency === currency.code) return undefined;
  const value = currency.code === household.baseCurrency ? row.amountBase : row.amountSecondary;
  return formatMoney(value, currency);
}

// `import_batch.status`. A batch that has committed is a record, not a working set — the API
// refuses a decision on one, and the screen must not offer an action it would refuse.
const REVIEWABLE = "awaiting_review";
const RECONCILED = "matched";

// What a batch the owner can no longer act on has to say for itself. One sentence for every
// closed state read "already imported", which is the opposite of what a discarded batch did —
// and neither sentence fits the states the enum carries but this screen has no words for, so
// those name themselves rather than borrow one.
const CLOSED: Record<string, string> = {
  completed: "This batch is already imported and can no longer be decided.",
  cancelled:
    "This batch was discarded. Nothing from it reached your ledger, and its account and " +
    "period are free to import again.",
};

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

function accepted(batch: ReviewBatch) {
  return batch.groups.find((group) => group.status === ACCEPTED)?.count ?? 0;
}

/** How many rows discarding would take with it.
 *
 * The group's own `count` rather than its rows' length: a later page of a large group will not
 * carry every row, and a confirmation that undercounts what it is about to delete is worse than
 * one that does not count at all. `rowsExtracted` is not the figure either — it is what the
 * statement yielded, not what is staged now.
 */
function staged(batch: ReviewBatch) {
  return batch.groups.reduce((total, group) => total + group.count, 0);
}

function withoutCategory(batch: ReviewBatch) {
  return batch.groups.reduce(
    (total, group) =>
      total + group.rows.filter((row) => row.classifiedBy === "unclassified").length,
    0,
  );
}



export function ReviewScreen({
  accounts,
  batchId,
  currencies,
  currency,
  household,
  request,
}: Props) {
  const [batch, setBatch] = useState<ReviewBatch>();
  const [selected, setSelected] = useState<Array<string>>([]);
  const [problem, setProblem] = useState<string>();
  const [imported, setImported] = useState<number>();
  const [discarded, setDiscarded] = useState<number>();
  // Two clicks, not one. Discarding cannot be undone and "cancel" reads as "close this screen"
  // at least as readily as "throw the batch away", so the confirmation says which it means.
  const [confirming, setConfirming] = useState(false);
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
        setConfirming(false);
        setBusy(false);
      })
      .catch((error: unknown) => {
        if (!current) return;
        setProblem(errorText(error, "This batch could not be loaded."));
        setBusy(false);
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
      // Left busy: the effect's re-read has not landed, and the buttons must not re-enable over
      // a batch the screen has already discarded. `reading` clears it when the new one arrives.
      reload();
    } catch (error) {
      setProblem(errorText(error, "Those rows could not be decided."));
      setBusy(false);
    }
  }

  async function commit() {
    setBusy(true);
    setProblem(undefined);
    try {
      const result = await request<{ rowsImported: number }>(`/import/${batchId}/commit`, {
        method: "POST",
        timeoutMs: LONG_REQUEST_TIMEOUT_MS,
      });
      setImported(result.rowsImported);
      // The commit has already completed; a recurring-discovery hiccup is never a commit
      // failure, so its own failure is swallowed rather than surfaced here.
      request("/recurring/refresh", { method: "POST" }).catch(() => undefined);
      reload();
    } catch (error) {
      // V14's own sentence, written for the owner: which account failed to reconcile and by
      // how much. The whole commit rolled back, so the batch is exactly as it was.
      setProblem(errorText(error, "This batch could not be imported."));
      setBusy(false);
    }
  }

  async function discard() {
    setBusy(true);
    setProblem(undefined);
    try {
      const result = await request<{ rowsDiscarded: number }>(`/import/${batchId}/cancel`, {
        method: "POST",
      });
      setDiscarded(result.rowsDiscarded);
      reload();
    } catch (error) {
      // The service's own sentence — a batch someone else imported from a second tab reads back
      // as already imported, which is not a failure to describe as "could not be discarded".
      setProblem(errorText(error, "This batch could not be discarded."));
      setConfirming(false);
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
  const unclassified = withoutCategory(batch);
  // The account's own currency, not a row's. The declared opening and closing are the account's
  // figures, and a committed batch carries no groups at all to read a code from. It is only the
  // banner's: each row formats in its own code, and a row's converted figure in the household's.
  const accountCode = accounts.find(({ id }) => id === batch.accountId)?.currency;

  return (
    <section className="workspace-content" aria-labelledby="review-title">
      <p className="eyebrow">Statement import</p>
      <h1 className="workspace-title" id="review-title">Review</h1>

      <Banner batch={batch} code={accountCode} currencies={currencies} />

      {problem && <p className="inline-alert" role="alert">{problem}</p>}
      {imported !== undefined && (
        <p className="import-verdict" role="status">
          {imported} {imported === 1 ? "row" : "rows"} imported.
        </p>
      )}
      {discarded !== undefined && (
        <p className="import-verdict" role="status">
          {discarded} staged {discarded === 1 ? "row" : "rows"} discarded. Nothing was imported.
        </p>
      )}
      {unclassified > 0 && (
        <p className="review-hint">
          {unclassified} {unclassified === 1 ? "row has" : "rows have"} no category. After
          importing, Transactions → Needs a category will ask how to mark {unclassified === 1
            ? "it"
            : "them"}.
        </p>
      )}

      {open ? (
        <div className="review-actions">
          <Button
            variant="default"
            disabled={!selected.length || busy}
            onClick={() => decide(ACCEPTED)}
            type="button"
          >Accept selected ({selected.length})</Button>
          <Button
            variant="outline"
            disabled={!selected.length || busy}
            onClick={() => decide("rejected")}
            type="button"
          >Reject selected ({selected.length})</Button>
          {ready > 0 ? (
            <Button variant="default" disabled={busy} onClick={commit} type="button">
              Import {ready} {ready === 1 ? "row" : "rows"}
            </Button>
          ) : (
            <p className="review-hint">Accept some rows before importing.</p>
          )}
          <Discard
            busy={busy}
            confirming={confirming}
            count={staged(batch)}
            onCancel={() => setConfirming(false)}
            onConfirm={discard}
            onStart={() => setConfirming(true)}
          />
        </div>
      ) : (
        <p className="review-hint" role="status">
          {CLOSED[batch.status] ?? `This batch is ${batch.status} and can no longer be decided.`}
        </p>
      )}

      {batch.groups.map((group) => (
        <Group
          currencies={currencies}
          currency={currency}
          group={group}
          household={household}
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

/** The one destructive action on this screen, behind a confirmation that says what it does.
 *
 * `POST /import/{batch}/cancel` deletes the batch's staged rows: they are what keeps the
 * household's reporting pair locked while nothing has been committed, so cancellation that left
 * them behind would recover the period and not the pair. Nothing in the ledger is touched — a
 * batch that has committed has no discard offered at all, and the API refuses one.
 */
function Discard({
  busy,
  confirming,
  count,
  onCancel,
  onConfirm,
  onStart,
}: {
  busy: boolean;
  confirming: boolean;
  count: number;
  onCancel: () => void;
  onConfirm: () => void;
  onStart: () => void;
}) {
  if (!confirming) {
    return (
      <Button variant="outline" disabled={busy} onClick={onStart} type="button">
        Discard this batch
      </Button>
    );
  }
  // A batch with no staged rows is reachable: `reconcile.py` reconciles a dormant month against
  // its stated opening and closing, so a statement with no transactions stages and then holds
  // its period like any other. Discarding it is right; counting to zero out loud is not.
  const rows = `${count} staged ${count === 1 ? "row" : "rows"}`;
  return (
    <div className="review-discard" role="group" aria-label="Confirm discarding this batch">
      <p className="review-hint">
        Discard {count > 0 ? rows : "this batch"}? Nothing from it has reached your ledger, and
        its account and period become free to import again.
      </p>
      <Button variant="outline" disabled={busy} onClick={onConfirm} type="button">
        {count > 0 ? `Discard ${count} ${count === 1 ? "row" : "rows"}` : "Discard it"}
      </Button>
      <Button variant="outline" disabled={busy} onClick={onCancel} type="button">
        Keep it
      </Button>
    </div>
  );
}

function Banner({
  batch,
  code,
  currencies,
}: {
  batch: ReviewBatch;
  code: string | undefined;
  currencies: Array<CurrencyRow>;
}) {
  const reconciliation = batch.reconciliation;
  const reconciled = reconciliation.status === RECONCILED;
  return (
    <div className={reconciled ? "import-verdict" : "inline-alert"} role="status">
      <strong>{reconciled ? "Reconciled" : `Reconciliation: ${reconciliation.status}`}</strong>
      {reconciliation.declaredOpening !== null && (
        <> · opening {moneyText(reconciliation.declaredOpening, code, currencies)}</>
      )}
      {reconciliation.declaredClosing !== null && (
        <> · closing {moneyText(reconciliation.declaredClosing, code, currencies)}</>
      )}
      <> · {batch.rowsExtracted} rows extracted</>
      {reconciliation.message && <> · {reconciliation.message}</>}
    </div>
  );
}

function Group({
  currencies,
  currency,
  household,
  group,
  onSelectGroup,
  onToggle,
  open,
  selected,
}: {
  currencies: Array<CurrencyRow>;
  currency: HouseholdCurrency;
  household: HouseholdResponse;
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
          <div className="review-select-all">
            <Checkbox
              // Named for the rows on screen, not the group's count: `count` is stated
              // separately because a later page of a large group will not carry every row, and
              // a label promising more than it ticks would go unnoticed until then.
              aria-label={`Select the ${group.rows.length} ${group.status} rows shown`}
              checked={ticked}
              onCheckedChange={(checked) => onSelectGroup(checked === true)}
            />
            <Button onClick={() => onSelectGroup(!ticked)} size="xs" type="button" variant="link">
              {ticked ? "Clear selection" : "Select all"}
            </Button>
          </div>
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
            household={household}
            currencies={currencies}
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
  currencies,
  currency,
  household,
  onToggle,
  open,
  row,
  selected,
}: {
  currencies: Array<CurrencyRow>;
  currency: HouseholdCurrency;
  household: HouseholdResponse;
  onToggle: (id: string) => void;
  open: boolean;
  row: ReviewRow;
  selected: boolean;
}) {
  const conversion = converted(row, household, currency);
  // Six cells, always, in the same order. `.review-row` is a fixed six-column grid and CSS grid
  // fills by position, so a row that renders fewer cells does not leave a gap — every later cell
  // shifts one column left and the description ends up in the date's 96px column. Three of these
  // are conditional, which is three ways for the count to decide the column; an empty span holds
  // the place instead.
  return (
    <li aria-label={`Row ${row.sourceRowNumber}`} className="review-row">
      {open ? (
        <span className="review-row-select">
          <Checkbox
            aria-label={`Row ${row.sourceRowNumber}`}
            checked={selected}
            onCheckedChange={() => onToggle(row.id)}
          />
        </span>
      ) : (
        <span aria-hidden="true" className="review-row-select" />
      )}
      <span className="review-date">{row.bookedDate}</span>
      <span className="review-description">{row.descriptionRaw}</span>
      {/* The statement's own amount first and the frozen conversion second — never the
          conversion alone (`ui-information-architecture.html` section 4). Both are formatted
          from the strings the API sent, and the conversion is the column that matches the
          displayed currency rather than a figure recomputed here (`converted`). The row names
          its own currency, so a row is never formatted with a batch-level guess. */}
      <span className="review-amount">
        {moneyText(row.amount, row.currency, currencies)}
      </span>
      <span className="review-converted">{conversion ?? ""}</span>
      <span className="section-label">{row.proposedTxnType ?? "unclassified"}</span>
      {/* What the cascade proposed and how sure it is, read-only. Correcting a category is
          the transactions screen's job, which already does it — a second place to do the same
          thing is a second place for it to be done differently. */}
      {row.proposedCategoryName && (
        <span className="review-proposal">{row.proposedCategoryName}</span>
      )}
      {row.classifiedBy !== "unclassified" && (
        <span className="section-label" title={`confidence ${row.classificationConfidence}`}>
          {row.classifiedBy.replace(/_/g, " ")}
        </span>
      )}
      <span className="section-label">
        {row.duplicateOfTransactionId ? "Already in the ledger" : ""}
      </span>
    </li>
  );
}
