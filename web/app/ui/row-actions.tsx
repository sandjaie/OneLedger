"use client";

import { type KeyboardEvent, type ReactNode, useId, useRef, useState } from "react";

type RowActionsProps = {
  children: ReactNode;
  id?: string;
  label: string;
};

/** A labelled, keyboard-operable disclosure for actions that do not belong on a record's face. */
export function RowActions({ children, id, label }: RowActionsProps) {
  const generatedId = useId();
  const contentId = id ?? `row-actions-${generatedId.replaceAll(":", "")}`;
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);

  function close() {
    setOpen(false);
    triggerRef.current?.focus();
  }

  function toggle() {
    if (open) {
      close();
      return;
    }
    setOpen(true);
  }

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
    <div className="row-actions">
      <button
        aria-controls={contentId}
        aria-expanded={open}
        className="text-action"
        onClick={toggle}
        onKeyDown={handleTriggerKeyDown}
        ref={triggerRef}
        type="button"
      >{label}</button>
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
