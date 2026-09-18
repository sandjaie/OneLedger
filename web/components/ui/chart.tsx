"use client";

import * as React from "react";
import { ResponsiveContainer } from "recharts";

import { cn } from "@/lib/utils";

/** Adapted from shadcn Chart: only the responsive container is used until exact-value tooltips exist. */
export type ChartConfig = Record<string, { label: string; color: string }>;

export function ChartContainer({
  className,
  children,
  config,
  initialDimension = { width: 320, height: 200 },
  style,
  ...props
}: React.ComponentProps<"div"> & {
  config: ChartConfig;
  children: React.ComponentProps<typeof ResponsiveContainer>["children"];
  initialDimension?: { width: number; height: number };
}) {
  const colors = Object.fromEntries(Object.entries(config).map(([key, item]) => {
    if (!/^[a-z][a-z0-9-]*$/.test(key) || !/^var\(--(?:category-(?:[1-9]|1[0-2])|chart-[1-5])\)$/.test(item.color)) {
      throw new Error("Chart colors must use safe series ids and approved palette tokens");
    }
    return [`--color-${key}`, item.color];
  })) as React.CSSProperties;

  return (
    <div
      className={cn("flex aspect-video justify-center text-xs [&_.recharts-cartesian-axis-tick_text]:fill-muted-foreground [&_.recharts-cartesian-grid_line]:stroke-border/50 [&_.recharts-layer]:outline-hidden [&_.recharts-surface]:outline-hidden", className)}
      data-slot="chart"
      style={{ ...colors, ...style }}
      {...props}
    >
      <ResponsiveContainer initialDimension={initialDimension}>{children}</ResponsiveContainer>
    </div>
  );
}
