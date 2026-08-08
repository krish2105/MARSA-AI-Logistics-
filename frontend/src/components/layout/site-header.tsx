import Link from "next/link";

import { ThemeToggle } from "@/components/theme/theme-toggle";

const NAV = [
  { href: "/", label: "Copilot" },
  { href: "/data", label: "Data provenance" },
  { href: "/regulatory", label: "Instruments" },
  { href: "/theme", label: "Design tokens" },
];

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-40 border-b border-border/80 bg-background/80 backdrop-blur-md">
      <div className="mx-auto flex h-16 max-w-6xl items-center gap-4 px-4 sm:px-6">
        <Link
          href="/"
          className="group flex shrink-0 items-center gap-2.5 rounded-md"
        >
          <AnchorMark />
          <span className="flex items-baseline gap-1.5">
            <span className="text-[0.95rem] font-semibold tracking-tight">
              MARSA AI
            </span>
            <span
              lang="ar"
              dir="rtl"
              className="text-sm text-muted-foreground transition-colors group-hover:text-primary"
            >
              مرسى
            </span>
          </span>
        </Link>

        <nav aria-label="Main" className="ml-auto hidden sm:block">
          <ul className="flex items-center gap-1">
            {NAV.map((item) => (
              <li key={item.href}>
                <Link
                  href={item.href}
                  className="rounded-md px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                >
                  {item.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>

        <ThemeToggle className="ml-auto sm:ml-0" />
      </div>
    </header>
  );
}

/** Stylised anchor — the "marsa" (harbour) mark. */
function AnchorMark() {
  return (
    <svg
      viewBox="0 0 24 24"
      className="size-6 text-primary"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <circle cx="12" cy="4.5" r="2" />
      <path d="M12 6.5V21" />
      <path d="M7.5 10h9" />
      <path d="M4 14a8 8 0 0 0 16 0" />
    </svg>
  );
}
