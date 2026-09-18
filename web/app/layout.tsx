import type { Metadata } from "next";
import { Inter } from "next/font/google";
import type { ReactNode } from "react";
import "./globals.css";
const inter = Inter({ display: "swap", subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "OneLedger",
  description: "A private financial operating system in progress.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={inter.variable}>
      <body>{children}</body>
    </html>
  );
}
