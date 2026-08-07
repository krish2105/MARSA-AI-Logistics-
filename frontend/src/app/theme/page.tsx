import type { Metadata } from "next";

import { ThemeToggle } from "@/components/theme/theme-toggle";
import { TokenExplorer } from "@/components/theme/token-explorer";

export const metadata: Metadata = {
  title: "Design tokens",
  description:
    "Live OKLCH token explorer for the Deep Harbour theme, with WCAG contrast ratios measured from the rendered output in both light and dark mode.",
};

export default function ThemePage() {
  return (
    <div className="mx-auto max-w-6xl px-4 py-14 sm:px-6">
      <header className="mb-12 max-w-3xl">
        <p className="mb-3 text-xs font-medium uppercase tracking-wider text-muted-foreground">
          Deep Harbour · design tokens
        </p>
        <h1
          className="font-semibold tracking-tight text-balance"
          style={{ fontSize: "var(--text-step-5)", lineHeight: 1.08 }}
        >
          Every token, both themes, contrast measured live
        </h1>
        <p className="mt-5 leading-relaxed text-muted-foreground">
          Dark was designed first and light re-derived from it — accents drop in
          lightness and gain chroma to survive a white ground, rather than being
          mechanically inverted. Ratios below are read back from the pixels the
          browser actually painted, so flip the theme and watch them re-measure.
        </p>
        <div className="mt-6 flex items-center gap-3">
          <ThemeToggle />
          <span className="text-sm text-muted-foreground">
            Switch to re-measure
          </span>
        </div>
      </header>

      <TokenExplorer />
    </div>
  );
}
