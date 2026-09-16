"use client";

import { use } from "react";

import { Workspace } from "@/app/workspace";

// A client page, so the dynamic segment arrives as a promise — the same pattern
// `app/investments/[holdingId]/page.tsx` follows for Next 16.
export default function SubscriptionDetailPage({
  params,
}: {
  params: Promise<{ seriesId: string }>;
}) {
  const { seriesId } = use(params);
  return <Workspace section="subscriptions-detail" seriesId={seriesId} />;
}
