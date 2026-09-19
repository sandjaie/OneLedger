"use client";

import type { ReactNode } from "react";
import { useState } from "react";
import Link from "next/link";
import {
  ArrowLeftRightIcon, CalendarDaysIcon, CandlestickChartIcon, ChartPieIcon,
  ClipboardListIcon, FilesIcon, LandmarkIcon, LayoutDashboardIcon, LineChartIcon,
  MenuIcon, Repeat2Icon, SettingsIcon, ShieldCheckIcon, TagsIcon, TrendingUpIcon,
  UploadIcon, WalletCardsIcon,
} from "lucide-react";

import { WorkspaceSearch } from "@/app/ui/workspace-search";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import {
  Sidebar, SidebarContent, SidebarGroup, SidebarGroupContent, SidebarGroupLabel,
  SidebarMenu, SidebarMenuButton, SidebarMenuItem,
} from "@/components/ui/sidebar";
import type { apiFetch } from "@/lib/api/client";
import type { HouseholdCurrency, HouseholdResponse } from "@/types/api";

const NAV_GROUPS = [
  { label: "Money", links: [
    { label: "Overview", href: "/", sections: ["overview"], icon: LayoutDashboardIcon },
    { label: "Accounts", href: "/accounts", sections: ["accounts"], icon: LandmarkIcon },
    { label: "Transactions", href: "/transactions", sections: ["transactions"], icon: ArrowLeftRightIcon },
    { label: "Spending", href: "/spending", sections: ["spending"], icon: ChartPieIcon },
    { label: "Subscriptions", href: "/subscriptions", sections: ["subscriptions", "subscriptions-detail"], icon: CalendarDaysIcon },
    { label: "Recurring", href: "/recurring", sections: ["recurring"], icon: Repeat2Icon },
  ] },
  { label: "Build & plan", links: [
    { label: "Investments", href: "/investments", sections: ["investments", "investments-entry", "investments-holding"], icon: TrendingUpIcon },
    { label: "Salary", href: "/salary", sections: ["salary"], icon: WalletCardsIcon },
    { label: "Plans", href: "/plans", sections: ["plans"], icon: ClipboardListIcon },
    { label: "Wealth", href: "/wealth", sections: ["wealth"], icon: LineChartIcon },
  ] },
  { label: "Review", links: [
    { label: "Import", href: "/import", sections: ["import", "review"], icon: UploadIcon },
    { label: "Import investments", href: "/investments/import", sections: ["investments-import"], icon: CandlestickChartIcon },
    { label: "Classify", href: "/review", sections: ["classification-review"], icon: TagsIcon },
    { label: "Documents", href: "/documents", sections: ["documents"], icon: FilesIcon },
    { label: "Coverage", href: "/coverage", sections: ["coverage"], icon: ShieldCheckIcon },
  ] },
  { label: "Workspace", links: [
    { label: "Settings", href: "/settings", sections: ["settings"], icon: SettingsIcon },
  ] },
];

function Brand() {
  return <div className="console-brand"><span className="brand-mark" aria-hidden="true"><LandmarkIcon /></span><span>OneLedger</span></div>;
}

function WorkspaceNavGroups({ activeSection, onNavigate }: { activeSection: string; onNavigate?: () => void }) {
  return NAV_GROUPS.map((group) => (
    <SidebarGroup className={`workspace-nav-group workspace-nav-group-${group.label === "Workspace" ? "last" : "main"}`} key={group.label}>
      <SidebarGroupLabel className="workspace-nav-label">{group.label}</SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu>
          {group.links.map((link) => {
            const active = link.sections.includes(activeSection);
            const Icon = link.icon;
            return (
              <SidebarMenuItem key={link.href}>
                <SidebarMenuButton isActive={active} render={<Link aria-current={active ? "page" : undefined} href={link.href} onClick={onNavigate} />}>
                  <Icon aria-hidden="true" />
                  <span>{link.label}</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            );
          })}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  ));
}

type AppShellProps = {
  activeSection: string;
  children: ReactNode;
  currency: HouseholdCurrency;
  household: HouseholdResponse;
  logoutFailed: boolean;
  navigate: (path: string) => void;
  onCurrencyChange: (currency: HouseholdCurrency) => void;
  onLogout: () => void;
  request: typeof apiFetch;
  signingOut: boolean;
};

export function AppShell({
  activeSection, children, currency, household, logoutFailed, navigate,
  onCurrencyChange, onLogout, request, signingOut,
}: AppShellProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <>
      <a className="workspace-skip-link" href="#workspace-content">Skip to content</a>
      <header className="console-topbar app-toolbar">
        <Sheet open={menuOpen} onOpenChange={setMenuOpen}>
          <SheetTrigger render={<Button aria-label="Open workspace menu" className="workspace-menu-toggle" size="icon" type="button" variant="outline" />}>
            <MenuIcon aria-hidden="true" data-icon="inline-start" />
          </SheetTrigger>
          <SheetContent className="workspace-mobile-sheet" side="left">
            <SheetTitle>Workspace navigation</SheetTitle>
            <Brand />
            <nav aria-label="Mobile workspace" className="workspace-mobile-nav">
              <WorkspaceNavGroups activeSection={activeSection} onNavigate={() => setMenuOpen(false)} />
            </nav>
            <div className="workspace-mobile-utilities">
              <div className="currency-toggle" role="group" aria-label="Reporting currency">
                {household.currencies.map((option) => (
                  <Button aria-pressed={currency.code === option.code} key={option.code} onClick={() => onCurrencyChange(option)} type="button" variant="ghost">
                    {option.code}
                  </Button>
                ))}
              </div>
              <Button disabled={signingOut} onClick={onLogout} type="button" variant="outline">
                {signingOut ? "Signing out…" : logoutFailed ? "Retry sign out" : "Sign out"}
              </Button>
            </div>
          </SheetContent>
        </Sheet>
        <WorkspaceSearch navigate={navigate} request={request} />
        <div className="console-utilities">
          <span className="console-context">Personal workspace</span>
          <div className="currency-toggle" role="group" aria-label="Reporting currency">
            {household.currencies.map((option) => (
              <Button aria-pressed={currency.code === option.code} key={option.code} onClick={() => onCurrencyChange(option)} type="button" variant="ghost">
                {option.code}
              </Button>
            ))}
          </div>
          <Avatar aria-label={`${household.ownerDisplayName} account`} className="owner-badge" title={household.ownerDisplayName}>
            <AvatarFallback>{household.ownerDisplayName.trim().slice(0, 1).toUpperCase()}</AvatarFallback>
          </Avatar>
          <Button disabled={signingOut} onClick={onLogout} type="button" variant="outline">
            {signingOut ? "Signing out…" : logoutFailed ? "Retry sign out" : "Sign out"}
          </Button>
        </div>
      </header>
      <main className="workspace-grid">
        <Sidebar className="console-rail" collapsible="none">
          <SidebarContent>
            <Brand />
            <nav className="workspace-nav" aria-label="Workspace" id="workspace-navigation">
              <WorkspaceNavGroups activeSection={activeSection} />
            </nav>
          </SidebarContent>
        </Sidebar>
        <div className="workspace-content-anchor" id="workspace-content" tabIndex={-1} />
        {children}
      </main>
    </>
  );
}
