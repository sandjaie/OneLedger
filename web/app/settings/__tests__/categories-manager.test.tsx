// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CategoriesManager } from "@/app/settings/categories-manager";
import type { apiFetch } from "@/lib/api/client";
import type { CategoryRow } from "@/types/api";

const seeded: CategoryRow = {
  id: "cat-food",
  parentId: null,
  name: "Food & Dining",
  slug: "food-dining",
  kind: "expense",
  bucket: "want",
  sortOrder: 140,
  isSystem: true,
  isActive: true,
};

const own: CategoryRow = {
  id: "cat-own",
  parentId: null,
  name: "Sidegigs",
  slug: "sidegigs",
  kind: "income",
  bucket: "income",
  sortOrder: 900,
  isSystem: false,
  isActive: true,
};

function renderManager(request: typeof apiFetch = vi.fn()) {
  return render(
    <CategoriesManager categories={[seeded, own]} onChanged={vi.fn()} request={request} />,
  );
}

afterEach(cleanup);

describe("the categories manager", () => {
  it("keeps a seeded name read-only while its bucket stays editable", async () => {
    const request = vi.fn().mockResolvedValue(seeded);
    renderManager(request as unknown as typeof apiFetch);
    expect(screen.queryByLabelText("Rename Food & Dining")).toBeNull();
    fireEvent.change(screen.getByLabelText("Bucket for Food & Dining"), {
      target: { value: "need" },
    });
    await waitFor(() => expect(request).toHaveBeenCalled());
    const [url, init] = request.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/categories/cat-food");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body)).bucket).toBe("need");
  });

  it("renames the household's own category on blur", async () => {
    const request = vi.fn().mockResolvedValue(own);
    renderManager(request as unknown as typeof apiFetch);
    const field = screen.getByLabelText("Rename Sidegigs");
    fireEvent.change(field, { target: { value: "Side income" } });
    fireEvent.blur(field);
    await waitFor(() => expect(request).toHaveBeenCalled());
    const [url, init] = request.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/categories/cat-own");
    expect(JSON.parse(String(init.body)).name).toBe("Side income");
  });

  it("adds a subcategory by parent alone — kind and bucket come from the parent", async () => {
    const request = vi.fn().mockResolvedValue(seeded);
    renderManager(request as unknown as typeof apiFetch);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Meal plans" } });
    fireEvent.change(screen.getByLabelText("Parent"), { target: { value: "cat-food" } });
    fireEvent.submit(screen.getByRole("form", { name: "Add a category" }));
    await waitFor(() => expect(request).toHaveBeenCalled());
    const [url, init] = request.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/categories");
    const body = JSON.parse(String(init.body));
    expect(body).toEqual({ name: "Meal plans", parentId: "cat-food" });
  });
});
