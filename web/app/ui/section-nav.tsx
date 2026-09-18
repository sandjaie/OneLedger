"use client";

import { type KeyboardEvent, useRef } from "react";
import { Button } from "@/components/ui/button";

export type SectionNavItem = {
  id: string;
  label: string;
};

type SectionNavProps = {
  activeId: string;
  items: Array<SectionNavItem>;
  onSelect: (id: string) => void;
};

/** A local section selector. URL and focus-target ownership remain with its screen. */
export function SectionNav({ activeId, items, onSelect }: SectionNavProps) {
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>, currentIndex: number) {
    let nextIndex: number | undefined;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      nextIndex = (currentIndex + 1) % items.length;
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      nextIndex = (currentIndex - 1 + items.length) % items.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = items.length - 1;
    }

    if (nextIndex === undefined || items.length === 0) return;
    event.preventDefault();
    const next = items[nextIndex];
    onSelect(next.id);
    itemRefs.current[nextIndex]?.focus();
  }

  return (
    <nav aria-label="Section navigation" className="section-nav">
      {items.map((item, index) => (
        <Button
          aria-current={item.id === activeId ? "page" : undefined}
          key={item.id}
          onClick={() => onSelect(item.id)}
          onKeyDown={(event) => handleKeyDown(event, index)}
          ref={(element) => { itemRefs.current[index] = element; }}
          type="button"
          variant={item.id === activeId ? "secondary" : "ghost"}
        >{item.label}</Button>
      ))}
    </nav>
  );
}
