"use client";

import { useState } from "react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";

type Props = {
  actionLabel: string;
  busy?: boolean;
  message: string;
  onConfirm: () => Promise<void>;
  title: string;
  triggerLabel: string;
};

/** Confirmation stays next to the action, with no write until its explicit final button. */
export function ConfirmAction({ actionLabel, busy, message, onConfirm, title, triggerLabel }: Props) {
  const [open, setOpen] = useState(false);

  return (
    <AlertDialog onOpenChange={setOpen} open={open}>
      <AlertDialogTrigger render={<Button disabled={busy} type="button" variant="destructive" />}>
        {triggerLabel}
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{message}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Keep payment</AlertDialogCancel>
          <AlertDialogAction onClick={() => { setOpen(false); void onConfirm(); }} variant="destructive">
            {actionLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
