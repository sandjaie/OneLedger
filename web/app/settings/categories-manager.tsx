"use client";

import { useState } from "react";

import { DataTableRegion } from "@/app/ui/data-table-region";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { type apiFetch } from "@/lib/api/client";
import type { CategoryRow } from "@/types/api";

type Props = {
  categories: Array<CategoryRow>;
  request: typeof apiFetch;
  onChanged: () => void;
};

const BUCKETS = ["need", "want", "investment", "debt_service", "income", "ignore"] as const;
const KINDS = ["income", "expense", "transfer", "investment", "debt"] as const;

export function CategoriesManager({ categories, request, onChanged }: Props) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string>();
  const [draft, setDraft] = useState({ name: "", parentId: "", kind: "expense", bucket: "want" });

  const roots = categories.filter((row) => row.parentId === null);
  const childrenOf = (id: string) => categories.filter((row) => row.parentId === id);

  async function save(row: CategoryRow, changes: Partial<CategoryRow>) {
    setBusy(true);
    setProblem(undefined);
    try {
      await request<CategoryRow>(`/categories/${row.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: changes.name ?? row.name,
          bucket: changes.bucket ?? row.bucket,
          isActive: changes.isActive ?? row.isActive,
        }),
        headers: { "Content-Type": "application/json" },
      });
      onChanged();
    } catch {
      setProblem("The category change was not saved. Retry.");
    } finally {
      setBusy(false);
    }
  }

  async function add(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setProblem(undefined);
    try {
      await request<CategoryRow>("/categories", {
        method: "POST",
        body: JSON.stringify(
          draft.parentId
            ? { name: draft.name, parentId: draft.parentId }
            : { name: draft.name, kind: draft.kind, bucket: draft.bucket },
        ),
        headers: { "Content-Type": "application/json" },
      });
      setDraft({ ...draft, name: "" });
      onChanged();
    } catch {
      setProblem("The category was not created. Check the values and retry.");
    } finally {
      setBusy(false);
    }
  }

  function row(category: CategoryRow, child: boolean) {
    return (
      <tr key={category.id}>
        <td>
          {category.isSystem ? (
            <span>{child ? `— ${category.name}` : category.name}</span>
          ) : (
            <label className="settings-inline-field">
              <span className="visually-hidden">Rename {category.name}</span>
              <Input
                defaultValue={category.name}
                disabled={busy}
                onBlur={(event) => {
                  const name = event.target.value.trim();
                  if (name && name !== category.name) void save(category, { name });
                }}
              />
            </label>
          )}
        </td>
        <td>
          <label className="settings-inline-field">
            <span className="visually-hidden">Bucket for {category.name}</span>
            <NativeSelect
              disabled={busy}
              onChange={(event) => void save(category, { bucket: event.target.value })}
              value={category.bucket}
            >
              {BUCKETS.map((bucket) => (
                <NativeSelectOption key={bucket} value={bucket}>{bucket}</NativeSelectOption>
              ))}
            </NativeSelect>
          </label>
        </td>
        <td>
          <label className="check-field inline-flex items-center gap-2">
            <Checkbox
              checked={category.isActive}
              disabled={busy}
              onCheckedChange={(checked) => void save(category, { isActive: checked === true })}
            />
            <span>Active</span>
          </label>
        </td>
      </tr>
    );
  }

  return (
    <section className="settings-block settings-categories" aria-labelledby="categories-title">
      <div>
        <p className="section-label">Classification</p>
        <h2 id="categories-title" tabIndex={-1}>Categories</h2>
        <p className="lede">
          The seeded tree keeps its names; every bucket stays yours to change — it is a default,
          not a verdict.
        </p>
      </div>
      {problem && <Alert variant="destructive"><AlertDescription>{problem}</AlertDescription></Alert>}

      <form aria-label="Add a category" className="settings-category-form" onSubmit={add}>
        <label className="settings-form-field">
          <span>Name</span>
          <Input
            onChange={(event) => setDraft({ ...draft, name: event.target.value })}
            required
            value={draft.name}
          />
        </label>
        <label className="settings-form-field">
          <span>Parent</span>
          <NativeSelect
            onChange={(event) => setDraft({ ...draft, parentId: event.target.value })}
            value={draft.parentId}
          >
            <NativeSelectOption value="">None — a new top-level category</NativeSelectOption>
            {roots.map((root) => (
              <NativeSelectOption key={root.id} value={root.id}>{root.name}</NativeSelectOption>
            ))}
          </NativeSelect>
        </label>
        {!draft.parentId && (
          <>
            <label className="settings-form-field">
              <span>Kind</span>
              <NativeSelect
                onChange={(event) => setDraft({ ...draft, kind: event.target.value })}
                value={draft.kind}
              >
                {KINDS.map((kind) => (
                  <NativeSelectOption key={kind} value={kind}>{kind}</NativeSelectOption>
                ))}
              </NativeSelect>
            </label>
            <label className="settings-form-field">
              <span>Bucket</span>
              <NativeSelect
                onChange={(event) => setDraft({ ...draft, bucket: event.target.value })}
                value={draft.bucket}
              >
                {BUCKETS.map((bucket) => (
                  <NativeSelectOption key={bucket} value={bucket}>{bucket}</NativeSelectOption>
                ))}
              </NativeSelect>
            </label>
          </>
        )}
        <div className="account-form-actions">
          <Button disabled={busy} type="submit">Add category</Button>
        </div>
      </form>

      <DataTableRegion label="Category taxonomy"><table className="console-table settings-category-table">
        <caption className="visually-hidden">Category taxonomy</caption>
        <thead>
          <tr>
            <th scope="col">Category</th>
            <th scope="col">Bucket</th>
            <th scope="col">Active</th>
          </tr>
        </thead>
        <tbody>
          {roots.flatMap((root) => [
            row(root, false),
            ...childrenOf(root.id).map((child) => row(child, true)),
          ])}
        </tbody>
      </table></DataTableRegion>
    </section>
  );
}
