"use client";

import { useEffect, useState } from "react";

import { type apiFetch, errorText } from "@/lib/api/client";
import type { ClassifyResult, RuleRow, TransactionRow } from "@/types/api";

type Preview = {
  affected: number;
  unclassified: number;
};

type Props = {
  categoryId: string;
  categoryName: string;
  onApplied: () => Promise<void>;
  onClose: () => void;
  onJustOne: () => Promise<void>;
  request: typeof apiFetch;
  row: TransactionRow;
};

/** Longest usable substring of this one row, never an identifier-bearing pattern. */
function proposedPattern(description: string): string {
  return description
    .split(/[\p{Nd}\/*#:]+/u)
    .map((part) => part.trim())
    .filter((part) => part.length >= 3)
    .reduce((longest, part) => (part.length > longest.length ? part : longest), "");
}

function usable(pattern: string, description: string): boolean {
  const trimmed = pattern.trim();
  return (
    trimmed.length >= 3 &&
    !/[\p{Nd}\/*#:]/u.test(trimmed) &&
    description.toLocaleLowerCase().includes(trimmed.toLocaleLowerCase())
  );
}

function ruleBody(pattern: string, categoryId: string, categoryName: string) {
  return {
    name: `Mark ${categoryName}`,
    priority: 100,
    isActive: true,
    matchDescriptionPattern: pattern.trim(),
    matchPatternKind: "contains",
    matchDirection: "any",
    setCategoryId: categoryId,
  };
}

export function ClassifyPrompt({
  categoryId,
  categoryName,
  onApplied,
  onClose,
  onJustOne,
  request,
  row,
}: Props) {
  const description = row.descriptionClean ?? row.descriptionRaw;
  const [pattern, setPattern] = useState(() => proposedPattern(description));
  const [preview, setPreview] = useState<Preview>();
  const [result, setResult] = useState<ClassifyResult>();
  const [problem, setProblem] = useState<string>();
  const [busy, setBusy] = useState(false);
  const valid = usable(pattern, description);

  useEffect(() => {
    if (!valid) return;
    let current = true;
    void request<Preview>("/rules/preview", {
      method: "POST",
      body: JSON.stringify(ruleBody(pattern, categoryId, categoryName)),
      headers: { "Content-Type": "application/json" },
    })
      .then((next) => {
        if (current) setPreview(next);
      })
      .catch((error: unknown) => {
        if (current) setProblem(errorText(error, "This pattern could not be checked. Retry."));
      });
    return () => {
      current = false;
    };
  }, [categoryId, categoryName, pattern, request, valid]);

  async function always() {
    if (!valid || !preview) return;
    setBusy(true);
    setProblem(undefined);
    try {
      const saved = await request<RuleRow>("/rules", {
        method: "POST",
        body: JSON.stringify(ruleBody(pattern, categoryId, categoryName)),
        headers: { "Content-Type": "application/json" },
      });
      const applied = await request<ClassifyResult>(`/classify?ruleId=${saved.id}`, {
        method: "POST",
      });
      setResult(applied);
      await onApplied();
    } catch (error) {
      setProblem(errorText(error, "This category rule was not applied. Retry."));
    } finally {
      setBusy(false);
    }
  }

  async function once() {
    setBusy(true);
    setProblem(undefined);
    try {
      await onJustOne();
      onClose();
    } catch (error) {
      setProblem(errorText(error, "This transaction was not changed. Retry."));
      setBusy(false);
    }
  }

  return (
    <section aria-label={`Classify ${row.descriptionRaw}`} className="account-form">
      <h2>Mark rows like this?</h2>
      <p className="lede">
        Save a private rule for matching unclassified rows, or change only this transaction.
      </p>
      <label className="text-field">
        <span>Pattern</span>
        <input
          disabled={busy || result !== undefined}
          onChange={(event) => {
            setPattern(event.target.value);
            setPreview(undefined);
            setProblem(undefined);
          }}
          value={pattern}
        />
      </label>
      {!valid && (
        <p className="inline-alert" role="alert">
          Use at least three characters from this description, without digits or / * # :.
        </p>
      )}
      {problem && <p className="inline-alert" role="alert">{problem}</p>}
      {preview && result === undefined && (
        <p role="status">
          {preview.affected} matching rows · {preview.unclassified} need a category.
        </p>
      )}
      {result && (
        <p role="status">
          {result.changed} {result.changed === 1 ? "row" : "rows"} categorized.
          {preview && preview.unclassified > result.changed
            ? ` An earlier rule decided ${preview.unclassified - result.changed} matching ${
                preview.unclassified - result.changed === 1 ? "row" : "rows"
              } before this rule.`
            : ""}
        </p>
      )}
      <div className="account-form-actions">
        <button
          className="primary-action"
          disabled={busy || !valid || !preview || result !== undefined}
          onClick={() => void always()}
          type="button"
        >
          Always mark rows like this
        </button>
        <button disabled={busy || result !== undefined} onClick={() => void once()} type="button">
          Just this one
        </button>
        <button disabled={busy} onClick={onClose} type="button">Close</button>
      </div>
    </section>
  );
}
