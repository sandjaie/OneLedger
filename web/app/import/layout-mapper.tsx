"use client";

import { useId, useState } from "react";

import { ACCOUNT_TYPES } from "@/app/accounts/accounts-screen";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type { LayoutColumn, LayoutCreate, LayoutProfile, LayoutProposal } from "@/types/api";

/** What the form starts from. `remap` locks the name field: saving always creates that
 * layout's next version (ADR-0012), so the name that decides it must not drift from the one
 * being remapped. `reason` is shown once, above the form, when a proposal was refused. */
export type LayoutMapperSeed = {
  draft: LayoutCreate;
  remap: boolean;
  reason?: string;
};

type Props = {
  file: File;
  password?: string;
  seed: LayoutMapperSeed;
  savedNames: Array<string>;
  request: typeof apiFetch;
  onSaved: (profile: LayoutProfile) => void | Promise<void>;
  onCancel: () => void;
};

// The engine's own small vocabularies (ADR-0012). Kept here rather than read off the API because
// none of them varies per household — they are the shape of the extraction contract itself.
const DATE_FORMATS = ["dd/MM/yyyy", "dd-MM-yyyy", "dd MMM yyyy", "dd/MM/yy", "yyyy-MM-dd"] as const;
const SIGN_CONVENTIONS = ["signed", "debit_credit_columns", "cr_suffix", "balance_delta"] as const;
const CHRONOLOGICAL_ORDERS = ["ascending", "descending"] as const;
const ROW_START_RULES = ["has_date_and_amount", "has_date"] as const;
const CONTINUATION_RULES = ["no_date_and_no_amount", "blank_date_and_money_columns"] as const;
const QUIRKS = [
  "filter_script:arabic",
  "continuation:no_date_and_no_amount",
  "metadata_text:words",
  "row_assembly:nearest_dated_row",
] as const;
const COLUMN_FIELDS = [
  "booked_date",
  "value_date",
  "description",
  "reference",
  "amount",
  "debit",
  "credit",
  "running_balance",
  "original_amount",
  "original_currency",
] as const;

// `fingerprints`, `skipLinePatterns` and `quirks` are optional in the generated type only
// because the API default-fills them server-side; every seed this component receives already
// has real arrays, and every list editor below relies on that.
type Draft = LayoutCreate & {
  fingerprints: Array<string>;
  skipLinePatterns: Array<string>;
  quirks: Array<string>;
};

function normalizeDraft(draft: LayoutCreate): Draft {
  return {
    ...draft,
    fingerprints: draft.fingerprints ?? [],
    skipLinePatterns: draft.skipLinePatterns ?? [],
    quirks: draft.quirks ?? [],
  };
}

type MetadataRow = { key: string; pattern: string };

function metadataRowsFrom(patterns: LayoutCreate["metadataPatterns"]): Array<MetadataRow> {
  return Object.entries(patterns ?? {}).map(([key, pattern]) => ({ key, pattern }));
}

function metadataPatternsFrom(rows: Array<MetadataRow>): LayoutCreate["metadataPatterns"] {
  const named = rows.filter((row) => row.key.trim().length > 0);
  return named.length > 0
    ? Object.fromEntries(named.map((row) => [row.key.trim(), row.pattern]))
    : null;
}

function problemText(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.detail : fallback;
}

function previewBody(file: File, password: string | undefined, draft: LayoutCreate) {
  const form = new FormData();
  form.append("file", file);
  if (password) form.append("password", password);
  form.append("draft", JSON.stringify(draft));
  return form;
}

export function LayoutMapper({
  file,
  password,
  seed,
  savedNames,
  request,
  onSaved,
  onCancel,
}: Props) {
  const headingId = useId();
  const [draft, setDraft] = useState<Draft>(() => normalizeDraft(seed.draft));
  const [metadataRows, setMetadataRows] = useState<Array<MetadataRow>>(() =>
    metadataRowsFrom(seed.draft.metadataPatterns));
  const [preview, setPreview] = useState<LayoutProposal>();
  const [previewedJson, setPreviewedJson] = useState<string>();
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewProblem, setPreviewProblem] = useState<string>();
  const [saveBusy, setSaveBusy] = useState(false);
  const [saveProblem, setSaveProblem] = useState<string>();

  function change<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  function changeListItem(key: "fingerprints" | "skipLinePatterns", index: number, value: string) {
    setDraft((current) => ({
      ...current,
      [key]: current[key].map((item, i) => (i === index ? value : item)),
    }));
  }
  function addListItem(key: "fingerprints" | "skipLinePatterns") {
    setDraft((current) => ({ ...current, [key]: [...current[key], ""] }));
  }
  function removeListItem(key: "fingerprints" | "skipLinePatterns", index: number) {
    setDraft((current) => ({ ...current, [key]: current[key].filter((_, i) => i !== index) }));
  }

  function changeColumn(index: number, patch: Partial<LayoutColumn>) {
    setDraft((current) => ({
      ...current,
      columns: current.columns.map((column, i) => (i === index ? { ...column, ...patch } : column)),
    }));
  }
  function addColumn() {
    setDraft((current) => ({
      ...current,
      columns: [...current.columns, { field: COLUMN_FIELDS[0], xMin: 0, xMax: 0, label: null }],
    }));
  }
  function removeColumn(index: number) {
    setDraft((current) => ({ ...current, columns: current.columns.filter((_, i) => i !== index) }));
  }

  function toggleQuirk(quirk: string) {
    setDraft((current) => ({
      ...current,
      quirks: current.quirks.includes(quirk)
        ? current.quirks.filter((item) => item !== quirk)
        : [...current.quirks, quirk],
    }));
  }

  function syncMetadata(rows: Array<MetadataRow>) {
    setMetadataRows(rows);
    change("metadataPatterns", metadataPatternsFrom(rows));
  }
  function changeMetadataKey(index: number, key: string) {
    syncMetadata(metadataRows.map((row, i) => (i === index ? { ...row, key } : row)));
  }
  function changeMetadataPattern(index: number, pattern: string) {
    syncMetadata(metadataRows.map((row, i) => (i === index ? { ...row, pattern } : row)));
  }
  function addMetadataRow() {
    syncMetadata([...metadataRows, { key: "", pattern: "" }]);
  }
  function removeMetadataRow(index: number) {
    syncMetadata(metadataRows.filter((_, i) => i !== index));
  }

  async function runPreview() {
    setPreviewBusy(true);
    setPreviewProblem(undefined);
    try {
      const result = await request<LayoutProposal>("/layouts/propose", {
        method: "POST",
        body: previewBody(file, password, draft),
      });
      setPreview(result);
      setPreviewedJson(JSON.stringify(draft));
    } catch (error) {
      setPreview(undefined);
      setPreviewedJson(undefined);
      setPreviewProblem(problemText(error, "This draft could not be previewed. Try again."));
    } finally {
      setPreviewBusy(false);
    }
  }

  async function runSave() {
    setSaveBusy(true);
    setSaveProblem(undefined);
    try {
      const profile = await request<LayoutProfile>("/layouts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(draft),
      });
      await onSaved(profile);
    } catch (error) {
      setSaveProblem(problemText(error, "This layout could not be saved. Try again."));
    } finally {
      setSaveBusy(false);
    }
  }

  const trimmedName = draft.name.trim();
  const sameName = !seed.remap && trimmedName.length > 0 && savedNames.includes(trimmedName);
  const matchesPreview = previewedJson !== undefined && previewedJson === JSON.stringify(draft);
  const canSave = matchesPreview
    && preview !== undefined
    && preview.reconciliation?.status === "matched"
    && (preview.failures ?? []).length === 0
    && preview.identifies === true
    && preview.accountNumberCaptures === 1
    && Boolean(draft.accountType);

  return (
    <section className="account-proposal layout-mapper" aria-labelledby={headingId}>
      <header>
        <p className="eyebrow">{seed.remap ? "Remap this layout" : "Map this layout"}</p>
        <h3 id={headingId}>{seed.remap ? `Remap ${seed.draft.name}` : "Map an unseen statement layout"}</h3>
        <p>
          Preview against this file before saving. Save is enabled only once the preview matches
          what is mapped below, reconciles, identifies the file and captures one account number.
        </p>
      </header>

      {seed.reason && <p className="inline-alert">{seed.reason}</p>}

      <div className="account-proposal-fields">
        <label>
          Name (required)
          <input
            disabled={seed.remap}
            required
            value={draft.name}
            onChange={(event) => change("name", event.target.value)}
          />
        </label>
        <label>
          Account type (required)
          <select
            required
            value={draft.accountType ?? ""}
            onChange={(event) => change("accountType", event.target.value || null)}
          >
            <option value="">Choose a type…</option>
            {ACCOUNT_TYPES.map((type) => <option key={type} value={type}>{type}</option>)}
          </select>
        </label>
        <label>
          Date format (required)
          <select value={draft.dateFormat} onChange={(event) => change("dateFormat", event.target.value)}>
            {DATE_FORMATS.map((format) => <option key={format} value={format}>{format}</option>)}
          </select>
        </label>
        <label>
          Sign convention (required)
          <select
            value={draft.signConvention}
            onChange={(event) => change("signConvention", event.target.value)}
          >
            {SIGN_CONVENTIONS.map((convention) => (
              <option key={convention} value={convention}>{convention}</option>
            ))}
          </select>
        </label>
        <label className="check-field">
          <input
            checked={draft.invertsSign}
            onChange={(event) => change("invertsSign", event.target.checked)}
            type="checkbox"
          />
          Invert sign
        </label>
        <label>
          Chronological order
          <select
            value={draft.chronologicalOrder}
            onChange={(event) => change("chronologicalOrder", event.target.value)}
          >
            {CHRONOLOGICAL_ORDERS.map((order) => <option key={order} value={order}>{order}</option>)}
          </select>
        </label>
        <label>
          Row-start rule
          <select
            value={draft.rowStartRule}
            onChange={(event) => change("rowStartRule", event.target.value)}
          >
            {ROW_START_RULES.map((rule) => <option key={rule} value={rule}>{rule}</option>)}
          </select>
        </label>
        <label>
          Continuation rule
          <select
            value={draft.continuationRule ?? ""}
            onChange={(event) => change("continuationRule", event.target.value || null)}
          >
            <option value="">None</option>
            {CONTINUATION_RULES.map((rule) => <option key={rule} value={rule}>{rule}</option>)}
          </select>
        </label>
        <label className="check-field">
          <input
            checked={draft.hasRunningBalance}
            onChange={(event) => change("hasRunningBalance", event.target.checked)}
            type="checkbox"
          />
          Has running balance
        </label>
        <label className="account-proposal-wide">
          Header row pattern
          <input
            value={draft.headerRowPattern ?? ""}
            onChange={(event) => change("headerRowPattern", event.target.value || null)}
          />
        </label>
        <label>
          Opening balance pattern
          <input
            value={draft.openingBalancePattern ?? ""}
            onChange={(event) => change("openingBalancePattern", event.target.value || null)}
          />
        </label>
        <label>
          Closing balance pattern
          <input
            value={draft.closingBalancePattern ?? ""}
            onChange={(event) => change("closingBalancePattern", event.target.value || null)}
          />
        </label>
      </div>

      <fieldset className="account-proposal-institution layout-mapper-quirks">
        <legend>Quirks</legend>
        {QUIRKS.map((quirk) => (
          <label className="check-field" key={quirk}>
            <input
              checked={draft.quirks.includes(quirk)}
              onChange={() => toggleQuirk(quirk)}
              type="checkbox"
            />
            {quirk}
          </label>
        ))}
      </fieldset>

      <fieldset className="account-proposal-institution layout-mapper-list">
        <legend>Fingerprints</legend>
        {draft.fingerprints.map((value, index) => (
          <div className="layout-mapper-list-row" key={index}>
            <input
              onChange={(event) => changeListItem("fingerprints", index, event.target.value)}
              value={value}
            />
            <button
              className="text-action"
              onClick={() => removeListItem("fingerprints", index)}
              type="button"
            >Remove</button>
          </div>
        ))}
        <button className="text-action" onClick={() => addListItem("fingerprints")} type="button">
          Add fingerprint
        </button>
      </fieldset>

      <fieldset className="account-proposal-institution layout-mapper-list">
        <legend>Skip line patterns</legend>
        {draft.skipLinePatterns.map((value, index) => (
          <div className="layout-mapper-list-row" key={index}>
            <input
              onChange={(event) => changeListItem("skipLinePatterns", index, event.target.value)}
              value={value}
            />
            <button
              className="text-action"
              onClick={() => removeListItem("skipLinePatterns", index)}
              type="button"
            >Remove</button>
          </div>
        ))}
        <button className="text-action" onClick={() => addListItem("skipLinePatterns")} type="button">
          Add skip line pattern
        </button>
      </fieldset>

      <fieldset className="account-proposal-institution layout-mapper-list layout-mapper-columns">
        <legend>Column bands</legend>
        {draft.columns.map((column, index) => (
          <div className="layout-mapper-list-row layout-mapper-column-row" key={index}>
            <select
              onChange={(event) => changeColumn(index, { field: event.target.value })}
              value={column.field}
            >
              {COLUMN_FIELDS.map((field) => <option key={field} value={field}>{field}</option>)}
            </select>
            <input
              inputMode="decimal"
              onChange={(event) => {
                const xMin = event.target.valueAsNumber;
                if (!Number.isNaN(xMin)) changeColumn(index, { xMin });
              }}
              type="number"
              value={column.xMin}
            />
            <input
              inputMode="decimal"
              onChange={(event) => {
                const xMax = event.target.valueAsNumber;
                if (!Number.isNaN(xMax)) changeColumn(index, { xMax });
              }}
              type="number"
              value={column.xMax}
            />
            <input
              onChange={(event) => changeColumn(index, { label: event.target.value || null })}
              placeholder="Label"
              value={column.label ?? ""}
            />
            <button className="text-action" onClick={() => removeColumn(index)} type="button">
              Remove
            </button>
          </div>
        ))}
        <button className="text-action" onClick={addColumn} type="button">Add column</button>
      </fieldset>

      <fieldset className="account-proposal-institution layout-mapper-list">
        <legend>Metadata patterns</legend>
        {metadataRows.map((row, index) => (
          <div className="layout-mapper-list-row" key={index}>
            <input
              onChange={(event) => changeMetadataKey(index, event.target.value)}
              placeholder="Key"
              value={row.key}
            />
            <input
              onChange={(event) => changeMetadataPattern(index, event.target.value)}
              placeholder="Pattern"
              value={row.pattern}
            />
            <button className="text-action" onClick={() => removeMetadataRow(index)} type="button">
              Remove
            </button>
          </div>
        ))}
        <button className="text-action" onClick={addMetadataRow} type="button">
          Add metadata pattern
        </button>
      </fieldset>

      {sameName && (
        <p className="account-proposal-note">
          Saving will create the next version of the existing layout “{trimmedName}”.
        </p>
      )}

      {previewProblem && <p className="inline-alert" role="alert">{previewProblem}</p>}

      <button
        className="text-action"
        disabled={previewBusy || saveBusy}
        onClick={() => void runPreview()}
        type="button"
      >{previewBusy ? "Previewing…" : "Preview"}</button>

      {preview && (
        <div className="import-summary layout-mapper-preview">
          <p className={preview.reconciliation?.status === "matched" ? "import-verdict" : "inline-alert"}>
            {preview.reconciliation?.message ?? "This draft extracted no reconciliation evidence."}
          </p>
          <p>
            {preview.identifies ? "This draft identifies the file." : "This draft does not identify the file."}
            {" · "}
            Account number captures: {preview.accountNumberCaptures ?? 0}
            {" · "}
            Rows extracted: {preview.rowsExtracted ?? 0}
          </p>
          {(preview.failures ?? []).length > 0 && (
            <ul className="import-failures">
              {(preview.failures ?? []).map((failure) => (
                <li key={`${failure.sourcePageNumber}-${failure.sourceRowNumber}`}>
                  Page {failure.sourcePageNumber ?? "?"}, row {failure.sourceRowNumber} — {failure.reason}
                </li>
              ))}
            </ul>
          )}
          {preview.rows.length > 0 && (
            <ul className="import-failures" aria-label="Mapped rows">
              {preview.rows.map((row) => (
                <li key={`${row.pageNumber}-${row.rowNumber}`}>
                  Page {row.pageNumber}, row {row.rowNumber} —{" "}
                  {Object.entries(row.fields).map(([key, value]) => `${key}: ${value}`).join(" · ")}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {saveProblem && <p className="inline-alert" role="alert">{saveProblem}</p>}

      <div className="layout-mapper-actions">
        <button
          className="primary-action"
          disabled={!canSave || saveBusy}
          onClick={() => void runSave()}
          type="button"
        >{saveBusy ? "Saving…" : "Save layout"}</button>
        <button className="text-action" onClick={onCancel} type="button">Cancel</button>
      </div>
    </section>
  );
}
