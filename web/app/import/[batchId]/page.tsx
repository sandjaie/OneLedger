"use client";

import { use } from "react";

import { Workspace } from "@/app/workspace";

// A client page, so the dynamic segment arrives as a promise and is read with `use` — the
// pattern Next 16 documents for a Client Component page.
export default function ReviewBatchPage({ params }: { params: Promise<{ batchId: string }> }) {
  const { batchId } = use(params);
  return <Workspace batchId={batchId} section="review" />;
}
