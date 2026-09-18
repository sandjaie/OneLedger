// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SectionNav } from "@/app/ui/section-nav";

const sections = [
  { id: "general", label: "General" },
  { id: "data-health", label: "Data health" },
  { id: "rules", label: "Rules" },
];

afterEach(cleanup);

describe("SectionNav", () => {
  it("marks the active section and reports a clicked selection", () => {
    const onSelect = vi.fn();
    render(<SectionNav activeId="data-health" items={sections} onSelect={onSelect} />);

    const navigation = screen.getByRole("navigation", { name: "Section navigation" });
    expect(navigation.contains(screen.getByRole("button", { name: "General" }))).toBe(true);
    expect(screen.getByRole("button", { name: "Data health" }).getAttribute("aria-current")).toBe("page");

    fireEvent.click(screen.getByRole("button", { name: "Rules" }));
    expect(onSelect).toHaveBeenCalledWith("rules");
  });

  it("moves and selects sections with arrow keys", () => {
    const onSelect = vi.fn();
    render(<SectionNav activeId="general" items={sections} onSelect={onSelect} />);

    const general = screen.getByRole("button", { name: "General" });
    general.focus();
    fireEvent.keyDown(general, { key: "ArrowRight" });

    expect(onSelect).toHaveBeenCalledWith("data-health");
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Data health" }));
  });
});
