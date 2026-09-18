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
