import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Biologix · excipient design",
  description:
    "Design and triage polymer excipients for biologic formulations, with NVIDIA BioNeMo components. Every result carries its evidence tier.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="dark">
      <body className="min-h-screen bg-plane text-ink antialiased">
        {/* The instrument ground, fixed behind everything. */}
        <div aria-hidden className="pointer-events-none fixed inset-0 plane-grid" />
        <div
          aria-hidden
          className="pointer-events-none fixed inset-x-0 top-0 h-80"
          style={{
            background:
              "radial-gradient(70% 100% at 50% 0%, rgba(57,135,229,0.10) 0%, rgba(57,135,229,0) 72%)",
          }}
        />
        <div className="relative">{children}</div>
      </body>
    </html>
  );
}
