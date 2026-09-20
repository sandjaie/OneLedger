"use client";

import { type KeyboardEvent, type ReactNode, useEffect, useId, useRef, useState } from "react";
import { Button } from "@/components/ui/button";

type RowActionsProps = {
  children: ReactNode;
  id?: string;
  label: string;
  /** Rendered on the trigger; an icon-only trigger still carries `label` as its accessible name. */
  triggerContent?: ReactNode;
  triggerId?: string;
};

/** A labelled, keyboard-operable disclosure for actions that do not belong on a record's face.
 *
 * The panel floats over the table instead of expanding its row, so opening one row's actions
 * cannot push every row below it down (Phase 4.8: "row operations do not cause layout jumps").
 * Escape and a click outside both close it; only Escape returns focus to the trigger, because a
 * click outside is already moving focus somewhere the owner chose. */
export function RowActions({ children, id, label, triggerContent, triggerId }: RowActionsProps) {
  const generatedId = useId();
  const contentId = id ?? `row-actions-${generatedId.replaceAll(":", "")}`;
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);

  function close(restoreFocus = true) {
    setOpen(false);
    if (restoreFocus) triggerRef.current?.focus();
  }

  function toggle() {
    if (open) {
      close();
      return;
    }
    setOpen(true);
  }

  useEffect(() => {
    if (!open) return undefined;
    function onPointerDown(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  function handleTriggerKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      toggle();
    }
  }

  function handleContentKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    }
  }

  return (
    <div className="row-actions" ref={containerRef}>
      <Button
        aria-controls={contentId}
        aria-expanded={open}
        aria-label={triggerContent ? label : undefined}
        className="row-actions-trigger"
        id={triggerId}
        onClick={toggle}
        onKeyDown={handleTriggerKeyDown}
        ref={triggerRef}
        size={triggerContent ? "icon-xs" : "sm"}
        type="button"
        variant="outline"
      >{triggerContent ?? label}</Button>
      <div
        aria-label={label}
        className="row-actions-content"
        hidden={!open}
        id={contentId}
        onKeyDown={handleContentKeyDown}
        role="region"
      >
        {children}
      </div>
    </div>
  );
}
