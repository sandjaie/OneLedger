"use client";

import { useState } from "react";

import { AccountProposalPanel } from "@/app/import/account-proposal";
import { ApiError, type apiFetch } from "@/lib/api/client";
import { formatMoney } from "@/lib/money/format";
import type { AccountRow, CurrencyRow, HouseholdCurrency, ParseResponse } from "@/types/api";

type Props = {
  accounts: Array<AccountRow>;
  currencies: Array<CurrencyRow>;
  currency: HouseholdCurrency;
  request: typeof apiFetch;
};

/** One chosen file and whatever the API has said about it so far.
 *
 * `parsed` holds the most recent answer about this file, and a staging attempt that fails
 * leaves the previous one in place: the summary is the only record of what the file contains
 * until the owner tries again, so a failure must not erase it.
 *
 * `batchId` outlives the `staged` state deliberately. A second check re-posts the dry run and
 * an already-staged file's document is stored, so the route answers 409 — a duplicate of
 * itself — and losing the id there would lose the only pointer to the batch just created.
 */
type Candidate = {
  key: string;
  file: File;
  state: "chosen" | "checking" | "checked" | "refused" | "staging" | "staged";
  parsed?: ParseResponse;
  batchId?: string;
  /** Once one file resolves an account, every later request for it keeps that exact account. */
  accountId?: string;
  /** The new account is not yet in the workspace prop; retain its catalogue key for formatting. */
  accountCurrency?: string;
  problem?: string;
  /** True once the service has said this file is encrypted, so it is offered a password field. */
  locked?: boolean;
  /** The password for this file, for as long as it is needed and no longer.
   *
   * A password is on the never-store list (`security-and-privacy.html` §1). It lives here, in
   * memory, and reaches nothing else — no `localStorage`, no URL, no log, no parse summary. It
   * is cleared the moment the file is staged, because after that nothing will ask for it again.
   */
  password?: string;
};

// `POST /parse` takes a PDF. Refusing anything else here is not validation the API relies on —
// it repeats the check — it is refusing to upload a file that cannot possibly work, which is
// also refusing to send its contents anywhere.
const PDF = "application/pdf";

// A statement whose verdict is anything but this cannot stage: Invariant I8 blocks the import
// rather than importing figures the statement itself does not support.
const RECONCILED = "matched";

// `words.extract_pages` answers a file it cannot open with exactly this. Recognised by its
// sentence rather than by its status: a 422 covers every parse refusal this route makes, and
// only this one is something the owner can answer.
const LOCKED = "PDF is encrypted or the password is incorrect";

function reconciles(parsed: ParseResponse) {
  return parsed.reconciliation.status === RECONCILED;
}

/** `pipeline.blocks_staging`, as the screen must read it.
 *
 * Two conditions, not one. The second — a row that would not normalize — is the half a screen
 * gating on the verdict alone misses: a batch missing rows reconciles only by coincidence, and
 * staging part of a statement produces a ledger wrong in a way no later check looks for.
 * Offering Stage on such a file offers an action the API answers `staged: false` to.
 */
function canStage(parsed: ParseResponse) {
  return reconciles(parsed) && parsed.failures.length === 0;
}

function candidate(file: File, index: number): Candidate {
  const key = `${file.name}-${index}-${file.lastModified}`;
  if (file.type === PDF || file.name.toLowerCase().endsWith(".pdf")) {
    return { key, file, state: "chosen" };
  }
  return {
    key,
    file,
    state: "refused",
    problem: "OneLedger reads only PDF statements; this file was not uploaded.",
  };
}

function detail(error: unknown, fallback: string) {
  // An ApiError carries the service's own sentence, which for this route is written for the
  // owner — the layout scores, the date a duplicate first arrived, the reconciliation refusal.
  // Anything else is the network or the browser, and has nothing to say about the statement.
  return error instanceof ApiError ? error.detail : fallback;
}

function body(item: Candidate, stage: boolean, accountId: string) {
  const form = new FormData();
  form.append("file", item.file);
  form.append("stage", stage ? "true" : "false");
  // Sent only when the owner overrode it. Absent, the pipeline resolves the account from the
  // statement's own number by salted hash, which is the path that needs no picker at all.
  const resolvedAccountId = item.accountId ?? accountId;
  if (resolvedAccountId) form.append("accountId", resolvedAccountId);
  // This file's own password, never another's. The service holds none between requests — the
  // blob it stores is the original and stays encrypted — so staging re-sends it.
  if (item.password) form.append("password", item.password);
  return form;
}

function periodText(parsed: ParseResponse) {
  const range = `${parsed.period.start} to ${parsed.period.end}`;
  // Invariant I7: a period the parse computed is never shown as one the statement stated.
  return parsed.period.isDerived ? `${range} · derived from the rows` : range;
}

function countsText(parsed: ParseResponse) {
  const entries = Object.entries(parsed.counts);
  if (!entries.length) return "";
  return entries.map(([status, count]) => `${count} ${status}`).join(" · ");
}

function leadsProposal(item: Candidate, candidates: Array<Candidate>) {
  const mask = item.parsed?.accountProposal?.accountNumberMasked;
  if (!mask) return false;
  const first = candidates
    .filter((candidate) => candidate.parsed?.accountProposal?.accountNumberMasked === mask)
    .toSorted((left, right) => {
      const byPeriod = left.parsed!.period.start.localeCompare(right.parsed!.period.start);
      return byPeriod || left.key.localeCompare(right.key);
    })[0];
  return first?.key === item.key;
}

export function ImportScreen({ accounts, currencies, currency, request }: Props) {
  const [candidates, setCandidates] = useState<Array<Candidate>>([]);
  const [accountId, setAccountId] = useState("");
  const [busy, setBusy] = useState(false);

  function update(key: string, change: Partial<Candidate>) {
    setCandidates((current) =>
      current.map((item) => (item.key === key ? { ...item, ...change } : item)),
    );
  }

  function choose(files: FileList | null) {
    setCandidates(Array.from(files ?? []).map(candidate));
  }

  async function checkAll() {
    setBusy(true);
    // Sequential rather than concurrent: each file is a multi-megabyte upload the service
    // parses synchronously, and the staging path takes the household row FOR UPDATE. Firing
    // them together would queue on that lock anyway, with the failures arriving interleaved.
    for (const item of candidates) {
      // A staged file is done. Re-posting its dry run would answer 409 against the document it
      // just stored — a duplicate of itself — and take its review link with it.
      if (item.state === "refused" || item.state === "staged") continue;
      update(item.key, { state: "checking", problem: undefined });
      try {
        const parsed = await request<ParseResponse>("/parse", {
          method: "POST",
          body: body(item, false, accountId),
        });
        const resolved = accounts.find((account) => account.id === parsed.accountId);
        update(item.key, {
          state: "checked",
          parsed,
          accountId: item.accountId,
          accountCurrency: resolved?.currency ?? item.accountCurrency,
        });
      } catch (error) {
        const problem = detail(error, "This file could not be checked. Try again.");
        // `locked` is sticky: a wrong password answers with the same sentence, and the field has
        // to survive that or the owner gets one attempt and no way to make a second.
        update(item.key, { state: "chosen", problem, locked: item.locked || problem === LOCKED });
      }
    }
    setBusy(false);
  }

  async function stage(item: Candidate) {
    update(item.key, { state: "staging", problem: undefined });
    try {
      const staged = await request<ParseResponse>("/parse", {
        method: "POST",
        body: body(item, true, accountId),
      });
      if (staged.staged && staged.batchId) {
        // The password is done: the document is stored and every later request names the batch.
        // Held past here it would only be a copy of a never-store value with nothing to do.
        update(item.key, {
          state: "staged",
          parsed: staged,
          batchId: staged.batchId,
          password: undefined,
          locked: false,
        });
      } else {
        // 200 without staging is spec §7.5's answer for a batch the pipeline refused: the
        // verdict, the delta and the divergent row do not fit in an error's `detail` string.
        // Reading HTTP success as staging success would tell the owner their statement was
        // staged when none of it was.
        update(item.key, {
          state: "checked",
          parsed: staged,
          problem: "This statement was not staged. Resolve what is flagged below and try again.",
        });
      }
    } catch (error) {
      // Back to `checked`, keeping the summary: the file is still a candidate, and the reason
      // it was refused is what the owner needs beside it.
      update(item.key, {
        state: "checked",
        problem: detail(error, "This file could not be staged. Try again."),
      });
    }
  }

  async function resolveProposal(item: Candidate, account: AccountRow) {
    const mask = item.parsed?.accountProposal?.accountNumberMasked;
    if (!mask) return;
    setBusy(true);
    const matching = candidates
      .filter((candidate) => candidate.parsed?.accountProposal?.accountNumberMasked === mask)
      .toSorted((left, right) => {
        const byPeriod = left.parsed!.period.start.localeCompare(right.parsed!.period.start);
        return byPeriod || left.key.localeCompare(right.key);
      });
    for (const candidate of matching) {
      const withAccount = { ...candidate, accountId: account.id, accountCurrency: account.currency };
      update(candidate.key, {
        state: "checking",
        accountId: account.id,
        accountCurrency: account.currency,
        problem: undefined,
      });
      try {
        const parsed = await request<ParseResponse>("/parse", {
          method: "POST",
          body: body(withAccount, false, ""),
        });
        update(candidate.key, {
          state: "checked",
          parsed,
          accountId: account.id,
          accountCurrency: account.currency,
        });
      } catch (error) {
        update(candidate.key, {
          state: "chosen",
          parsed: undefined,
          accountId: account.id,
          accountCurrency: account.currency,
          problem: detail(error, "This file could not be re-checked with its account."),
        });
      }
    }
    setBusy(false);
  }

  // The same two exclusions the loop makes, so the button is not offered for a pass that would
  // skip every file it has.
  const checkable = candidates.some(
    (item) => item.state !== "refused" && item.state !== "staged",
  );

  return (
    <section className="workspace-content" aria-labelledby="import-title">
      <p className="eyebrow">Statements</p>
      <h1 className="workspace-title" id="import-title">Import</h1>
      <p className="lede">
        Choose one or more statement PDFs. Nothing is uploaded until you ask for a check, and
        nothing reaches the ledger until you stage a batch and review it.
      </p>

      <div className="import-controls">
        <label className="file-field">
          <span>Statement files</span>
          <input
            accept="application/pdf,.pdf"
            multiple
            onChange={(event) => choose(event.target.files)}
            type="file"
          />
        </label>

        <label className="select-field">
          <span>Account</span>
          <select onChange={(event) => setAccountId(event.target.value)} value={accountId}>
            <option value="">Detect from the statement</option>
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>{account.name}</option>
            ))}
          </select>
        </label>

        <button
          className="primary-action"
          disabled={!checkable || busy}
          onClick={checkAll}
          type="button"
        >{busy ? "Checking…" : "Check statements"}</button>
      </div>

      <ul className="import-list">
        {candidates.map((item) => (
          <li aria-label={item.file.name} className="import-file" key={item.key}>
            <div className="import-file-head">
              <strong>{item.file.name}</strong>
              <span aria-live="polite" className="section-label" role="status">
                {stateLabel(item)}
              </span>
            </div>

            {item.problem && <p className="inline-alert" role="alert">{item.problem}</p>}

            {item.locked && (
              <label className="password-field">
                <span>Password for this statement</span>
                <input
                  // `new-password` rather than `off`: browsers increasingly ignore `off` on a
                  // password field and offer to save the value. The owner's own password manager
                  // is not a never-store violation — the list binds OneLedger — but an unprompted
                  // offer to keep a bank statement's password is a surprise worth declining.
                  autoComplete="new-password"
                  onChange={(event) =>
                    update(item.key, { password: event.target.value || undefined })
                  }
                  type="password"
                  value={item.password ?? ""}
                />
                <small>
                  Used for this file only, for this check. It is not saved anywhere.
                </small>
              </label>
            )}

            {item.parsed && (
              <Summary
                accounts={accounts}
                currencies={currencies}
                currency={currency}
                item={item}
                parsed={item.parsed}
              />
            )}

            {!busy && item.state === "checked" && item.parsed?.accountProposal && leadsProposal(item, candidates) && (
              <AccountProposalPanel
                currencies={currencies}
                file={item.file}
                layoutId={item.parsed.layoutId}
                onResolved={(account) => resolveProposal(item, account)}
                password={item.password}
                proposal={item.parsed.accountProposal}
                request={request}
              />
            )}

            {!busy && item.state === "checked" && item.parsed?.accountProposal && !leadsProposal(item, candidates) && (
              <p className="import-verdict">
                This account will be confirmed from the earliest selected statement.
              </p>
            )}

            {item.state === "checked" && item.parsed && !item.parsed.accountProposal && canStage(item.parsed) && (
              <button className="primary-action" disabled={busy} onClick={() => stage(item)} type="button">
                Stage batch
              </button>
            )}

            {item.state === "staged" && item.batchId && (
              <a className="text-action" href={`/import/${item.batchId}`}>Review this batch</a>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function stateLabel(item: Candidate) {
  if (item.state === "refused") return "Not uploaded";
  if (item.state === "checking") return "Checking…";
  if (item.state === "staging") return "Staging…";
  if (item.state === "staged") return "Staged";
  if (item.state === "checked" && item.parsed?.accountProposal) return "Needs account";
  if (item.state === "checked") return "Checked";
  return "Ready to check";
}

function Summary({
  accounts,
  currencies,
  currency,
  item,
  parsed,
}: {
  accounts: Array<AccountRow>;
  currencies: Array<CurrencyRow>;
  currency: HouseholdCurrency;
  item: Candidate;
  parsed: ParseResponse;
}) {
  // The declared closing is a figure in the account's own currency, so it is formatted in that
  // one — read from the catalogue row, never from a union of codes (ADR-0012). Taken from the
  // account the parse resolved rather than from a preview row, because the preview is capped
  // and a statement whose rows all failed to normalize still declares a closing balance.
  // Falling back to the household's display currency means an account the catalogue does not
  // cover still formats with real grouping rather than printing raw.
  const code = item.accountCurrency ?? accounts.find(({ id }) => id === parsed.accountId)?.currency;
  const shown = currencies.find(({ code: option }) => option === code) ?? currency;
  const reconciliation = parsed.reconciliation;

  return (
    <div className="import-summary">
      <dl>
        <div>
          <dt>Period</dt>
          <dd>{periodText(parsed)}</dd>
        </div>
        <div>
          <dt>Rows</dt>
          <dd>{parsed.rowsExtracted} rows{countsText(parsed) && ` · ${countsText(parsed)}`}</dd>
        </div>
        {reconciliation.declaredClosing !== null && (
          <div>
            <dt>Declared closing</dt>
            <dd>{formatMoney(reconciliation.declaredClosing, shown)}</dd>
          </div>
        )}
      </dl>

      <p className={reconciles(parsed) ? "import-verdict" : "inline-alert"}>
        {reconciliation.message}
      </p>

      {parsed.failures.length > 0 && (
        <ul className="import-failures">
          {parsed.failures.map((failure) => (
            <li key={`${failure.sourcePageNumber}-${failure.sourceRowNumber}`}>
              Page {failure.sourcePageNumber ?? "?"}, row {failure.sourceRowNumber} —{" "}
              {failure.reason}
            </li>
          ))}
        </ul>
      )}

      {item.state === "staged" && (
        <p className="import-verdict">Staged for review. Nothing is in the ledger yet.</p>
      )}
    </div>
  );
}
