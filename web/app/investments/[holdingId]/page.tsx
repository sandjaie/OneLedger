"use client";

import { use } from "react";

import { Workspace } from "@/app/workspace";

// A client page, so the dynamic segment arrives as a promise — the same pattern
// `app/investments/import/[importId]/page.tsx` follows for Next 16.
export default function HoldingDetailPage({
  params,
}: {
  params: Promise<{ holdingId: string }>;
}) {
  const { holdingId } = use(params);
  return <Workspace holdingId={holdingId} section="investments-holding" />;
}
