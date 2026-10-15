"use client";

import { motion } from "framer-motion";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Alert, Button, Field, inputClass } from "@/components/ui/Primitives";
import { post } from "@/lib/client";
import type { Role, User } from "@/lib/types";

const ROLES: { value: Role; label: string; blurb: string }[] = [
  {
    value: "scientist",
    label: "Formulation scientist",
    blurb: "Opens programs, runs design campaigns, triages candidates.",
  },
  {
    value: "computational",
    label: "Computational chemist",
    blurb: "Runs the docking and affinity work, reads provenance.",
  },
  {
    value: "reviewer",
    label: "Reviewer",
    blurb: "Reads any program and its audit trail; records decisions.",
  },
];

/**
 * Sign in and sign up.
 *
 * The role picker is here rather than buried in settings because the role is
 * how the audit trail describes a person later — "a reviewer rejected this"
 * carries different weight from "the scientist who generated it did" — so it
 * is worth one deliberate choice at the start.
 */
export function AuthForm({ mode, next }: { mode: "login" | "register"; next?: string }) {
  const router = useRouter();
  const registering = mode === "register";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [role, setRole] = useState<Role>("scientist");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const result = registering
      ? await post<{ user: User }>("/auth/register", {
          email,
          password,
          display_name: displayName,
          role,
        })
      : await post<{ user: User }>("/auth/login", { email, password });

    if (!result.ok) {
      setError(result.error);
      setBusy(false);
      return;
    }
    // A hard navigation, so the server components re-read with the new cookie.
    window.location.href = next && next.startsWith("/") ? next : "/";
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      {registering && (
        <Field label="Your name" hint="The audit trail names people, not identifiers.">
          <input
            id="display_name"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            required
            maxLength={120}
            autoComplete="name"
            placeholder="Ada Lovelace"
            className={inputClass}
          />
        </Field>
      )}

      <Field label="Email" htmlFor="email">
        <input
          id="email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
          autoComplete="email"
          placeholder="you@lab.org"
          className={inputClass}
        />
      </Field>

      <Field
        label="Password"
        htmlFor="password"
        hint={registering ? "At least 10 characters. Length beats punctuation." : undefined}
      >
        <input
          id="password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          minLength={registering ? 10 : undefined}
          autoComplete={registering ? "new-password" : "current-password"}
          className={inputClass}
        />
      </Field>

      {registering && (
        <fieldset>
          <legend className="text-2xs uppercase tracking-wider text-muted">What you do</legend>
          <div className="mt-2 space-y-2">
            {ROLES.map((option) => {
              const selected = role === option.value;
              return (
                <label
                  key={option.value}
                  className={`flex cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5 transition-colors ${
                    selected
                      ? "border-accent/50 bg-accent-soft"
                      : "border-hairline bg-surface-2 hover:border-hairline-strong"
                  }`}
                >
                  <input
                    type="radio"
                    name="role"
                    value={option.value}
                    checked={selected}
                    onChange={() => setRole(option.value)}
                    className="mt-0.5 accent-[var(--accent)]"
                  />
                  <span>
                    <span className="block text-xs text-ink">{option.label}</span>
                    <span className="mt-0.5 block text-2xs leading-relaxed text-muted">
                      {option.blurb}
                    </span>
                  </span>
                </label>
              );
            })}
          </div>
        </fieldset>
      )}

      {error && <Alert title={registering ? "could not register" : "sign in failed"}>{error}</Alert>}

      <motion.div whileTap={{ scale: 0.99 }}>
        <Button type="submit" disabled={busy} className="w-full py-2.5">
          {busy ? "working…" : registering ? "create account" : "sign in"}
        </Button>
      </motion.div>

      <p className="text-center text-2xs text-muted">
        {registering ? (
          <>
            Already have an account?{" "}
            <Link href="/login" className="text-accent hover:underline">
              Sign in
            </Link>
          </>
        ) : (
          <>
            New here?{" "}
            <Link href="/register" className="text-accent hover:underline">
              Create an account
            </Link>
          </>
        )}
      </p>
    </form>
  );
}
