import type { ReactNode } from "react";

export function Panel({
  title,
  subtitle,
  aside,
  children,
  className = "",
}: {
  title?: string;
  subtitle?: string;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`rounded-lg border border-hairline bg-surface/90 backdrop-blur-sm ${className}`}
    >
      {(title || aside) && (
        <header className="flex items-start justify-between gap-4 border-b border-hairline px-5 py-3.5">
          <div>
            {title && (
              <h2 className="text-[13px] font-semibold uppercase tracking-[0.14em] text-ink-2">
                {title}
              </h2>
            )}
            {subtitle && <p className="mt-1 max-w-2xl text-xs leading-relaxed text-muted">{subtitle}</p>}
          </div>
          {aside && <div className="shrink-0">{aside}</div>}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  );
}
