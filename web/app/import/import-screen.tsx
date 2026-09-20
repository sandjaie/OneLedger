"use client";

import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";

import { useState, type DragEvent } from "react";

import { AccountProposalPanel } from "@/app/import/account-proposal";
import { LayoutMapper, type LayoutMapperSeed } from "@/app/import/layout-mapper";
import { ImportStageJourney, type ImportStage } from "@/app/ui/import-stage-journey";
import { LONG_REQUEST_TIMEOUT_MS, type apiFetch, errorText } from "@/lib/api/client";
import { moneyText } from "@/lib/money/format";
import type {
  AccountRow,
  CurrencyRow,
  LayoutCreate,
  LayoutProfile,
  ParseResponse,
} from "@/types/api";

type Props = {
  accounts: Array<AccountRow>;
  currencies: Array<CurrencyRow>;
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
  /** Open only while the owner is mapping or remapping this file's layout. */
  mapping?: boolean;
  mapperSeed?: LayoutMapperSeed;
  /** Every saved layout name, read once when the mapper opens, for its same-name warning. */
  savedLayoutNames?: Array<string>;
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

// `pipeline.LayoutNotIdentified` writes one of these two openings — no saved layout at all, or
// none that scored unambiguously — and nothing else in `POST /parse` starts a refusal this way.
// Recognised by prefix because the second sentence names the scored candidates after it.
const NEEDS_MAPPING = ["no layouts are saved yet", "no saved layout identifies this statement"];

function needsMapping(problem: string | undefined) {
  return problem !== undefined && NEEDS_MAPPING.some((sentence) => problem.startsWith(sentence));
}

// A blank starting point for Map when the engine's own proposal is refused (statement-ingestion
// spec's "Failure behaviour": a blank draft with the reason, never a dead end).
const BLANK_DRAFT: LayoutCreate = {
  name: "",
  institutionId: null,
  accountType: null,
  fingerprints: [],
  headerRowPattern: null,
  columns: [],
  dateFormat: "dd/MM/yyyy",
  signConvention: "signed",
  invertsSign: false,
  chronologicalOrder: "ascending",
  rowStartRule: "has_date_and_amount",
  continuationRule: null,
  skipLinePatterns: [],
  openingBalancePattern: null,
  closingBalancePattern: null,
  hasRunningBalance: false,
  quirks: [],
  metadataPatterns: null,
};

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

// The layout identified this statement — the identity matched too, so this is not an account
// proposal's problem — and still cannot stage: the mapping itself is what needs fixing.
function needsRemap(parsed: ParseResponse) {
  return parsed.identityResolution.status === "matched" && !canStage(parsed);
}

function mapperBody(item: Candidate) {
  const form = new FormData();
  form.append("file", item.file);
  if (item.password) form.append("password", item.password);
  return form;
}

// Remap seeds from the saved profile, without `id` and `version`: saving always inserts that
// layout's next one (ADR-0012), so the two fields a saved row alone carries have no place here.
function profileAsDraft(profile: LayoutProfile): LayoutCreate {
  return {
    name: profile.name,
    institutionId: profile.institutionId,
    accountType: profile.accountType,
    fingerprints: profile.fingerprints,
    headerRowPattern: profile.headerRowPattern,
    columns: profile.columns,
    dateFormat: profile.dateFormat,
    signConvention: profile.signConvention,
    invertsSign: profile.invertsSign,
    chronologicalOrder: profile.chronologicalOrder,
    rowStartRule: profile.rowStartRule,
    continuationRule: profile.continuationRule,
    skipLinePatterns: profile.skipLinePatterns,
    openingBalancePattern: profile.openingBalancePattern,
    closingBalancePattern: profile.closingBalancePattern,
    hasRunningBalance: profile.hasRunningBalance,
    quirks: profile.quirks,
    metadataPatterns: profile.metadataPatterns,
  };
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

function body(item: Candidate, stage: boolean, accountId: string) {
  const form = new FormData();
  form.append("file", item.file);
  form.append("stage", stage ? "true" : "false");
  // Sent only when the owner overrode it. Otherwise identity resolution uses the statement's
  // typed normalized identifier; the salted fingerprint is only a fallback for legacy rows that
  // have not yet been upgraded from their verified statement source.
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
  const groupKey = item.parsed?.identityResolution.proposalGroupKey;
  if (!item.parsed || item.parsed.identityResolution.status === "matched") return false;
  const first = candidates
    .filter((candidate) => (
      groupKey === null
        ? candidate.key === item.key
        : candidate.parsed?.identityResolution.proposalGroupKey === groupKey
    ))
    .toSorted((left, right) => {
      const byPeriod = left.parsed!.period.start.localeCompare(right.parsed!.period.start);
      return byPeriod || left.key.localeCompare(right.key);
    })[0];
  return first?.key === item.key;
}

function candidateStage(item: Candidate): ImportStage {
  if (item.state === "staged") return 4;
  if (item.parsed?.identityResolution.status === "matched") return 3;
  if (item.state === "checking" || item.parsed || item.problem) return 2;
  return 1;
}

function candidateCompleteThrough(item: Candidate): 0 | ImportStage {
  if (item.state === "staged") return 3;
  if (item.parsed?.identityResolution.status === "matched") return 2;
  if (item.parsed || item.problem) return 1;
  return 0;
}

export function ImportScreen({ accounts, currencies, request }: Props) {
  const [candidates, setCandidates] = useState<Array<Candidate>>([]);
  const [accountId, setAccountId] = useState("");
  const [busy, setBusy] = useState(false);
  const [checkingAll, setCheckingAll] = useState(false);

  function update(key: string, change: Partial<Candidate>) {
    setCandidates((current) =>
      current.map((item) => (item.key === key ? { ...item, ...change } : item)),
    );
  }

  function choose(files: FileList | null) {
    if (busy) return;
    setCandidates(Array.from(files ?? []).map(candidate));
  }

  function drop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    if (busy || event.dataTransfer.files.length === 0) {
      return;
    }
    choose(event.dataTransfer.files);
  }

  async function checkOne(item: Candidate) {
    update(item.key, { state: "checking", problem: undefined });
    try {
      const parsed = await request<ParseResponse>("/parse", {
        method: "POST",
        body: body(item, false, accountId),
        timeoutMs: LONG_REQUEST_TIMEOUT_MS,
      });
      const resolved = accounts.find((account) => account.id === parsed.accountId);
      update(item.key, {
        state: "checked",
        parsed,
        accountId: item.accountId,
        accountCurrency: resolved?.currency ?? item.accountCurrency,
      });
    } catch (error) {
      const problem = errorText(error, "This file could not be checked. Try again.");
      // `locked` is sticky: a wrong password answers with the same sentence, and the field has
      // to survive that or the owner gets one attempt and no way to make a second.
      update(item.key, { state: "chosen", problem, locked: item.locked || problem === LOCKED });
    }
  }

  async function checkAll() {
    setBusy(true);
    setCheckingAll(true);
    // Sequential rather than concurrent: each file is a multi-megabyte upload the service
    // parses synchronously, and the staging path takes the household row FOR UPDATE. Firing
    // them together would queue on that lock anyway, with the failures arriving interleaved.
    for (const item of candidates) {
      // A staged file is done. Re-posting its dry run would answer 409 against the document it
      // just stored — a duplicate of itself — and take its review link with it.
      if (item.state === "refused" || item.state === "staged") continue;
      await checkOne(item);
    }
    setCheckingAll(false);
    setBusy(false);
  }

  async function openMapper(item: Candidate, remap: boolean) {
    setBusy(true);
    update(item.key, { problem: undefined });
    try {
      const layouts = await request<Array<LayoutProfile>>("/layouts");
      const savedLayoutNames = layouts.map((layout) => layout.name);
      if (remap) {
        const existing = item.parsed
          && layouts.find((layout) => layout.id === item.parsed?.layoutId);
        if (!existing) {
          update(item.key, { problem: "This layout could not be found to remap." });
          return;
        }
        const draft = profileAsDraft(existing);
        update(item.key, {
          mapping: true,
          savedLayoutNames,
          mapperSeed: { draft: draft as LayoutCreate, remap: true },
        });
        return;
      }
      try {
        const proposal = await request<{ draft: LayoutCreate }>("/layouts/propose", {
          method: "POST",
          body: mapperBody(item),
          timeoutMs: LONG_REQUEST_TIMEOUT_MS,
        });
        update(item.key, {
          mapping: true,
          savedLayoutNames,
          mapperSeed: {
            draft: { ...proposal.draft, accountType: proposal.draft.accountType ?? null },
            remap: false,
          },
        });
      } catch (error) {
        update(item.key, {
          mapping: true,
          savedLayoutNames,
          mapperSeed: {
            draft: BLANK_DRAFT,
            remap: false,
            reason: errorText(error, "This file could not be proposed for mapping."),
          },
        });
      }
    } catch (error) {
      update(item.key, { problem: errorText(error, "Saved layouts could not be loaded.") });
    } finally {
      setBusy(false);
    }
  }

  function closeMapper(item: Candidate) {
    update(item.key, { mapping: false, mapperSeed: undefined, savedLayoutNames: undefined });
  }

  async function onLayoutSaved(item: Candidate) {
    closeMapper(item);
    await checkOne(item);
  }

  async function stage(item: Candidate) {
    update(item.key, { state: "staging", problem: undefined });
    try {
      const staged = await request<ParseResponse>("/parse", {
        method: "POST",
        body: body(item, true, accountId),
        timeoutMs: LONG_REQUEST_TIMEOUT_MS,
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
        problem: errorText(error, "This file could not be staged. Try again."),
      });
    }
  }

  async function resolveProposal(item: Candidate, account: AccountRow) {
    const groupKey = item.parsed?.identityResolution.proposalGroupKey;
    if (!item.parsed || item.parsed.identityResolution.status === "matched") return;
    setBusy(true);
    const matching = candidates
      .filter((candidate) => (
        groupKey === null
          ? candidate.key === item.key
          : candidate.parsed?.identityResolution.proposalGroupKey === groupKey
      ))
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
          timeoutMs: LONG_REQUEST_TIMEOUT_MS,
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
          problem: errorText(error, "This file could not be re-checked with its account."),
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
    <section className="workspace-content statement-import-screen" aria-labelledby="import-title">
      <header className="statement-import-header">
        <h1 className="workspace-title" id="import-title">Statement import</h1>
        <p>Check first. Nothing enters the ledger until you review and commit it.</p>
      </header>

      <ImportStageJourney current={1} />

      <div
        aria-label="Upload statement files"
        className="statement-import-dropzone"
        onDragOver={(event) => event.preventDefault()}
        onDrop={drop}
      >
        <div className="statement-import-intro">
          <span aria-hidden="true" className="statement-import-filemark">PDF</span>
          <h2>Upload a financial statement</h2>
          <p>
            Choose or drop bank and card PDFs. We will check their account, period and layout.
          </p>
          <span className="statement-import-format">PDF</span>
        </div>

        <div className="import-controls">
          <label className="file-field statement-import-picker">
            <span>Statement files</span>
            <Input
              accept="application/pdf,.pdf"
              disabled={busy}
              multiple
              onChange={(event) => choose(event.target.files)}
              type="file"
            />
          </label>

          <label className="select-field">
            <span>Account</span>
            <NativeSelect
              disabled={busy}
              onChange={(event) => setAccountId(event.target.value)}
              value={accountId}
            >
              <option value="">Detect from the statement</option>
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>{account.name}</option>
              ))}
            </NativeSelect>
          </label>

          <Button
            variant="default"
            disabled={!checkable || busy}
            onClick={checkAll}
            type="button"
          >{busy ? "Checking…" : "Check statements"}</Button>
        </div>
      </div>

      <ul className="import-list">
        {candidates.map((item) => (
          <li
            aria-label={item.file.name}
            className="import-file statement-import-file-card"
            key={item.key}
          >
            <div className="import-file-head">
              <div className="statement-import-file-identity">
                <span aria-hidden="true" className="statement-import-filemark">PDF</span>
                <div>
                  <strong>{item.file.name}</strong>
                  <small>Selected locally · checked only when you ask</small>
                </div>
              </div>
              <span aria-live="polite" className="section-label" role="status">
                {stateLabel(item)}
              </span>
            </div>

            <ImportStageJourney
              completeThrough={candidateCompleteThrough(item)}
              current={candidateStage(item)}
              label={`${item.file.name} import progress`}
            />

            {item.problem && <p className="inline-alert" role="alert">{item.problem}</p>}

            {item.state === "chosen" && needsMapping(item.problem) && !item.mapping && (
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => void openMapper(item, false)}
                type="button"
              >Map this layout</Button>
            )}

            {item.locked && (
              <label className="password-field">
                <span>Password for this statement</span>
                <Input
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
                item={item}
                parsed={item.parsed}
              />
            )}

            {!checkingAll && item.state === "checked" && item.parsed && item.parsed.identityResolution.status !== "matched" && leadsProposal(item, candidates) && (
              <AccountProposalPanel
                currencies={currencies}
                file={item.file}
                key={item.parsed.identityResolution.proposalGroupKey ?? item.key}
                layoutId={item.parsed.layoutId}
                onBusyChange={setBusy}
                onResolved={(account) => resolveProposal(item, account)}
                password={item.password}
                resolution={item.parsed.identityResolution}
                request={request}
                screenBusy={busy}
              />
            )}

            {!checkingAll && item.state === "checked" && item.parsed && item.parsed.identityResolution.status !== "matched" && !leadsProposal(item, candidates) && (
              <p className="import-verdict">
                This account identity will be confirmed from the earliest selected statement in
                its group.
              </p>
            )}

            {item.state === "checked" && item.parsed?.identityResolution.status === "matched" && canStage(item.parsed) && (
              <Button variant="default" disabled={busy} onClick={() => stage(item)} type="button">
                Stage batch
              </Button>
            )}

            {item.state === "checked" && item.parsed && needsRemap(item.parsed) && !item.mapping && (
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => void openMapper(item, true)}
                type="button"
              >Remap this layout</Button>
            )}

            {item.mapping && item.mapperSeed && (
              <LayoutMapper
                file={item.file}
                onCancel={() => closeMapper(item)}
                onSaved={() => onLayoutSaved(item)}
                password={item.password}
                request={request}
                savedNames={item.savedLayoutNames ?? []}
                seed={item.mapperSeed}
              />
            )}

            {item.state === "staged" && item.batchId && (
              <a className={buttonVariants({ variant: "outline" })} href={`/import/${item.batchId}`}>Review this batch</a>
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
  if (item.state === "checked" && item.parsed && item.parsed.identityResolution.status !== "matched") {
    return item.parsed.identityResolution.status === "ambiguous"
      ? "Needs a choice"
      : "Needs confirmation";
  }
  if (item.state === "checked") return "Checked";
  return "Ready to check";
}

function Summary({
  accounts,
  currencies,
  item,
  parsed,
}: {
  accounts: Array<AccountRow>;
  currencies: Array<CurrencyRow>;
  item: Candidate;
  parsed: ParseResponse;
}) {
  // The declared closing is a figure in the account's own currency, so it is formatted in that
  // one — read from the catalogue row, never from a union of codes (ADR-0012). Taken from the
  // account the parse resolved rather than from a preview row, because the preview is capped
  // and a statement whose rows all failed to normalize still declares a closing balance.
  // A code the catalogue does not cover prints raw rather than under the household's symbol.
  const code = item.accountCurrency ?? accounts.find(({ id }) => id === parsed.accountId)?.currency;
  const reconciliation = parsed.reconciliation;
  const identity = parsed.identityResolution;
  const matchedAccount = identity.candidates.at(0)
    ?? accounts.find(({ id }) => id === parsed.accountId);
  const matchedInstitution = identity.institution.candidates.at(0);

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
            <dd>{moneyText(reconciliation.declaredClosing, code, currencies)}</dd>
          </div>
        )}
      </dl>

      {identity.status === "matched" && (
        <p className="import-identity-status">
          <strong>Matched automatically</strong>
          <span>
            {[matchedInstitution?.name, matchedAccount?.name, identity.accountNumberMasked]
              .filter(Boolean)
              .join(" · ")}
          </span>
        </p>
      )}

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
