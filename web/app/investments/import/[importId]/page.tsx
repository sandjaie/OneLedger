"use client";

import { use } from "react";

import { Workspace } from "@/app/workspace";

// A client page, so the dynamic segment arrives as a promise and is read with `use` — the same
// pattern `app/import/[batchId]/page.tsx` follows for Next 16.
export default function InvestmentImportResumePage({
  params,
}: {
  params: Promise<{ importId: string }>;
}) {
  const { importId } = use(params);
  return <Workspace importId={importId} section="investments-import" />;
}
