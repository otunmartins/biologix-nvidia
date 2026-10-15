"use client";

import { AnimatePresence, motion } from "framer-motion";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { post } from "@/lib/client";
import type { Availability, User } from "@/lib/types";

/**
 * The app chrome: who you are, where you are, and whether the engine behind
 * the app is actually alive.
 *
 * The platform pill in the sidebar footer is not decoration. It is the one
 * number that tells a user whether the expensive half of the product can run
 * at all right now, and it links straight to the page that explains why.
 */

const NAV = [
  { href: "/", label: "Overview", glyph: "◧", exact: true },
  { href: "/programs", label: "Programs", glyph: "◳" },
  { href: "/activity", label: "Audit trail", glyph: "≡" },
  { href: "/platform", label: "Platform", glyph: "◬" },
];

export function Shell({
  user,
  availability,
  children,
}: {
  user: User;
  availability?: Availability;
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);

  const nims = (availability?.adapters ?? []).filter((a) => a.adapter.startsWith("bionemo-"));
  const live = nims.filter((a) => a.available).length;
  const degraded = availability?.summary.degraded_mode ?? true;

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col justify-between border-r border-hairline bg-surface-2/60 px-4 py-6 backdrop-blur-sm lg:flex">
        <div>
          <Link href="/" className="flex items-center gap-2.5 px-2">
            <ChainMark />
            <span className="text-[13px] font-semibold uppercase tracking-[0.2em] text-ink">
              Biologix
            </span>
          </Link>
          <p className="mt-2 px-2 text-2xs leading-relaxed text-muted">
            Excipient design for biologics
          </p>

          <nav className="mt-7 space-y-1">
            {NAV.map((item) => {
              const active = item.exact
                ? pathname === item.href
                : pathname.startsWith(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`relative flex items-center gap-2.5 rounded-lg px-3 py-2 text-xs transition-colors ${
                    active ? "text-ink" : "text-muted hover:text-ink-2"
                  }`}
                >
                  {active && (
                    <motion.span
                      layoutId="nav-active"
                      className="absolute inset-0 rounded-lg border border-accent/30 bg-accent-soft"
                      transition={{ type: "spring", stiffness: 420, damping: 32 }}
                    />
                  )}
                  <span aria-hidden className="relative font-mono text-2xs">
                    {item.glyph}
                  </span>
                  <span className="relative">{item.label}</span>
                </Link>
              );
            })}
          </nav>
        </div>

        <div className="space-y-3">
          <Link
            href="/platform"
            className="block rounded-lg border border-hairline bg-surface px-3 py-2.5 transition-colors hover:border-accent/40"
          >
            <div className="flex items-center justify-between">
              <span className="text-2xs uppercase tracking-wider text-muted">BioNeMo NIMs</span>
              <span
                aria-hidden
                className={`inline-block h-1.5 w-1.5 rounded-full ${degraded ? "" : "breathe"}`}
                style={{
                  background: degraded ? "var(--state-warn)" : "var(--state-good)",
                }}
              />
            </div>
            <p className="mt-1 tabular text-sm text-ink">
              {live}
              <span className="text-muted"> / {nims.length} reachable</span>
            </p>
            <p className="mt-1 text-2xs leading-relaxed text-muted">
              {degraded
                ? "Degraded: some components cannot run here."
                : "All configured components are reachable."}
            </p>
          </Link>

          <div className="relative">
            <button
              type="button"
              onClick={() => setMenuOpen((open) => !open)}
              aria-expanded={menuOpen}
              className="flex w-full items-center gap-2.5 rounded-lg border border-hairline bg-surface px-3 py-2.5 text-left transition-colors hover:border-accent/40"
            >
              <Avatar name={user.display_name} />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs text-ink">{user.display_name}</span>
                <span className="block truncate text-2xs text-muted">{user.role}</span>
              </span>
              <span aria-hidden className="font-mono text-2xs text-muted">
                {menuOpen ? "▾" : "▸"}
              </span>
            </button>
            <AnimatePresence>
              {menuOpen && (
                <motion.div
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: 6 }}
                  transition={{ duration: 0.16 }}
                  className="absolute bottom-full left-0 mb-2 w-full overflow-hidden rounded-lg border border-hairline bg-surface shadow-xl"
                >
                  <p className="border-b border-hairline px-3 py-2 text-2xs text-muted">
                    {user.email}
                  </p>
                  <SignOut />
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </div>
      </aside>

      <div className="min-w-0 flex-1">
        <MobileBar user={user} />
        {/* Each route gets a short rise on entry, so a navigation reads as a
            change of place rather than a flicker. */}
        <AnimatePresence mode="wait">
          <motion.main
            key={pathname}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
            className="mx-auto max-w-[1500px] px-5 py-7 lg:px-9"
          >
            {children}
          </motion.main>
        </AnimatePresence>
      </div>
    </div>
  );
}

function MobileBar({ user }: { user: User }) {
  const pathname = usePathname();
  return (
    <div className="flex items-center justify-between gap-3 border-b border-hairline bg-surface-2/70 px-5 py-3 backdrop-blur-sm lg:hidden">
      <Link href="/" className="flex items-center gap-2">
        <ChainMark />
        <span className="text-xs font-semibold uppercase tracking-[0.18em] text-ink">
          Biologix
        </span>
      </Link>
      <nav className="flex items-center gap-1 overflow-x-auto">
        {NAV.map((item) => {
          const active = item.exact ? pathname === item.href : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`whitespace-nowrap rounded px-2 py-1 text-2xs uppercase tracking-wider ${
                active ? "bg-accent-soft text-ink" : "text-muted"
              }`}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>
      <Avatar name={user.display_name} />
    </div>
  );
}

function SignOut() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  return (
    <button
      type="button"
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        await post("/auth/logout");
        // A full reload, so no stale server-rendered page survives sign-out.
        window.location.href = "/login";
        router.refresh();
      }}
      className="w-full px-3 py-2 text-left text-2xs uppercase tracking-wider text-ink-2 transition-colors hover:bg-surface-3 hover:text-ink disabled:opacity-50"
    >
      {busy ? "signing out…" : "sign out"}
    </button>
  );
}

export function Avatar({ name, size = 26 }: { name: string; size?: number }) {
  const initials = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
  return (
    <span
      aria-hidden
      className="grid shrink-0 place-items-center rounded-full border border-hairline-strong bg-surface-3 font-mono text-2xs text-ink-2"
      style={{ width: size, height: size }}
    >
      {initials || "?"}
    </span>
  );
}

/**
 * The product mark: a backbone with two repeat units and a cap.
 *
 * Schematic, and deliberately not a depiction of any particular chemistry —
 * the app never draws a molecule it has not computed.
 */
function ChainMark() {
  return (
    <svg aria-hidden width="20" height="20" viewBox="0 0 20 20" className="shrink-0">
      <path
        d="M2 10h4M8 10h4M14 10h4"
        stroke="var(--accent)"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
      <circle cx="7" cy="10" r="1.9" fill="var(--surface-1)" stroke="var(--accent)" strokeWidth="1.2" />
      <circle cx="13" cy="10" r="1.9" fill="var(--surface-1)" stroke="var(--accent)" strokeWidth="1.2" />
      <circle cx="2" cy="10" r="1" fill="var(--text-muted)" />
      <circle cx="18" cy="10" r="1" fill="var(--text-muted)" />
    </svg>
  );
}
