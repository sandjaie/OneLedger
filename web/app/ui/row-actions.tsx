"use client";

import { type KeyboardEvent, type ReactNode, useEffect, useId, useRef, useState } from "react";
import { Button } from "@/components/ui/button";

/** Room a downward panel needs below its trigger before it opens upward instead. */
const FLIP_THRESHOLD_PX = 220;

type RowActionsProps = {
  children: ReactNode;
  id?: string;
  label: string;
  /** Rendered on the trigger; an icon-only trigger still carries `label` as its accessible name. */
  triggerContent?: ReactNode;
  triggerId?: string;
  /** Inside a console table: float the panel over the page instead of expanding the record.
   *
   * A `.data-table-region` sets `overflow-x: auto`, which makes its `overflow-y` compute to
   * `auto` as well, so an absolutely positioned panel would be clipped by the region and would
   * grow it a surprise vertical scrollbar. Anchoring the panel with `position: fixed` takes it
   * out of every ancestor's clip box; it is closed on scroll and resize so it can never sit
   * away from the trigger it belongs to. Screens that still use the inline disclosure — the
   * subscription and recurring records — are unaffected. */
  floating?: boolean;
};

/** A labelled, keyboard-operable disclosure for actions that do not belong on a record's face.
 *
 * Escape and a click outside both close it; only Escape returns focus to the trigger, because a
 * click outside is already moving focus somewhere the owner chose. */
export function RowActions({ children, id, label, triggerContent, triggerId, floating }: RowActionsProps) {
  const generatedId = useId();
  const contentId = id ?? `row-actions-${generatedId.replaceAll(":", "")}`;
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<{ top: number; left: number; flip: boolean }>();

  function close() {
    setOpen(false);
    triggerRef.current?.focus();
  }

  function toggle() {
    if (open) {
      close();
      return;
    }
    if (floating) {
      const rect = triggerRef.current?.getBoundingClientRect();
      // A fixed panel cannot be scrolled to, so a trigger near the bottom of the viewport opens
      // its panel upward instead of off the fold.
      if (rect) {
        const flip = window.innerHeight - rect.bottom < FLIP_THRESHOLD_PX;
        setAnchor({ top: flip ? rect.top - 6 : rect.bottom + 6, left: rect.right, flip });
      }
    }
    setOpen(true);
  }

  useEffect(() => {
    if (!open) return undefined;
    function onPointerDown(event: MouseEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }
    // Closed rather than repositioned: the panel belongs to one row, and a menu that drifts from
    // its trigger is worse than one the owner reopens.
    function onMoved() {
      setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    if (floating) {
      window.addEventListener("scroll", onMoved, true);
      window.addEventListener("resize", onMoved);
    }
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      window.removeEventListener("scroll", onMoved, true);
      window.removeEventListener("resize", onMoved);
    };
  }, [open, floating]);

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
    <div className="row-actions" data-floating={floating ? "true" : undefined} ref={containerRef}>
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
        data-flip={anchor?.flip ? "true" : undefined}
        style={floating && anchor ? { top: anchor.top, left: anchor.left } : undefined}
      >
        {children}
      </div>
    </div>
  );
}
