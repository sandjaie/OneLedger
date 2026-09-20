// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RowActions } from "@/app/ui/row-actions";

afterEach(cleanup);

describe("RowActions", () => {
  it("keeps named actions closed until the trigger is activated", () => {
    render(
      <RowActions id="record-actions" label="Review record actions">
        <button type="button">Rename record</button>
      </RowActions>,
    );

    const trigger = screen.getByRole("button", { name: "Review record actions" });
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(trigger.getAttribute("aria-controls")).toBe("record-actions");
    expect(document.getElementById("record-actions")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Review record actions" })).toBeNull();

    fireEvent.click(trigger);

    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("region", { name: "Review record actions" }).getAttribute("id")).toBe("record-actions");
  });

  it("floats its panel over the row instead of expanding it, and names an icon trigger", () => {
    render(
      <RowActions label="Actions for Synthetic record" triggerContent="⋯">
        <button type="button">Rename record</button>
      </RowActions>,
    );

    // An icon-only trigger still carries the full accessible name.
    const trigger = screen.getByRole("button", { name: "Actions for Synthetic record" });
    expect(trigger.textContent).toBe("⋯");
    fireEvent.click(trigger);
    // Absolute, so opening one row's actions cannot push the rows below it down.
    const panel = screen.getByRole("region", { name: "Actions for Synthetic record" });
    expect(panel.classList.contains("row-actions-content")).toBe(true);
    expect(panel.closest(".row-actions")).toBeTruthy();
  });

  it("closes on a click outside without stealing focus from where the owner clicked", () => {
    render(
      <div>
        <RowActions label="Review record actions">
          <button type="button">Rename record</button>
        </RowActions>
        <button type="button">Elsewhere</button>
      </div>,
    );

    const trigger = screen.getByRole("button", { name: "Review record actions" });
    fireEvent.click(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("true");

    const elsewhere = screen.getByRole("button", { name: "Elsewhere" });
    elsewhere.focus();
    fireEvent.mouseDown(elsewhere);

    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(elsewhere);
  });

  it("keeps enclosed controls operable and returns focus when keyboard close is requested", () => {
    const rename = vi.fn();
    render(
      <RowActions label="Review record actions">
        <button onClick={rename} type="button">Rename record</button>
      </RowActions>,
    );

    const trigger = screen.getByRole("button", { name: "Review record actions" });
    trigger.focus();
    fireEvent.keyDown(trigger, { key: "Enter" });

    const action = screen.getByRole("button", { name: "Rename record" });
    fireEvent.click(action);
    expect(rename).toHaveBeenCalledOnce();

    action.focus();
    fireEvent.keyDown(screen.getByRole("region", { name: "Review record actions" }), { key: "Escape" });

    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(trigger);
  });
});
