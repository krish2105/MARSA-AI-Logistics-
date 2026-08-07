"use client";

import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

import { useIsHydrated } from "@/lib/use-is-hydrated";
import {
  MINIMUM,
  contrastRatio,
  passes,
  resolveToken,
  toHex,
  tokenValue,
  type ContrastUse,
} from "@/lib/contrast";
import { cn } from "@/lib/utils";

interface Pair {
  label: string;
  bg: string;
  fg: string;
  use: ContrastUse;
  note?: string;
}

const SURFACES: Pair[] = [
  { label: "Page", bg: "--background", fg: "--foreground", use: "body" },
  { label: "Card", bg: "--card", fg: "--card-foreground", use: "body" },
  { label: "Popover", bg: "--popover", fg: "--popover-foreground", use: "body" },
  {
    label: "Muted",
    bg: "--muted",
    fg: "--muted-foreground",
    use: "body",
    note: "Secondary text — the most common AA failure in dashboards",
  },
  { label: "Secondary", bg: "--secondary", fg: "--secondary-foreground", use: "body" },
  { label: "Accent", bg: "--accent", fg: "--accent-foreground", use: "body" },
  { label: "Primary", bg: "--primary", fg: "--primary-foreground", use: "body" },
  {
    label: "Destructive",
    bg: "--destructive",
    fg: "--destructive-foreground",
    use: "body",
  },
];

const ROUTES: Pair[] = [
  {
    label: "Fast Path badge",
    bg: "--route-fast",
    fg: "--route-fast-foreground",
    use: "body",
  },
  {
    label: "Agentic Path badge",
    bg: "--route-agentic",
    fg: "--route-agentic-foreground",
    use: "body",
  },
  {
    label: "Graph Path badge",
    bg: "--route-graph",
    fg: "--route-graph-foreground",
    use: "body",
  },
];

const RISKS: Pair[] = [
  { label: "Risk · low", bg: "--risk-low-muted", fg: "--risk-low", use: "body" },
  {
    label: "Risk · medium",
    bg: "--risk-medium-muted",
    fg: "--risk-medium",
    use: "body",
  },
  { label: "Risk · high", bg: "--risk-high-muted", fg: "--risk-high", use: "body" },
];

/** Tokens measured as marks on the page background, not as text pairs. */
const ON_BACKGROUND: { label: string; token: string; use: ContrastUse }[] = [
  { label: "Fast (text/mark)", token: "--route-fast", use: "ui" },
  { label: "Agentic (text/mark)", token: "--route-agentic", use: "ui" },
  { label: "Graph (text/mark)", token: "--route-graph", use: "ui" },
  { label: "Chart 1", token: "--chart-1", use: "ui" },
  { label: "Chart 2", token: "--chart-2", use: "ui" },
  { label: "Chart 3", token: "--chart-3", use: "ui" },
  { label: "Chart 4", token: "--chart-4", use: "ui" },
  { label: "Chart 5", token: "--chart-5", use: "ui" },
  // Reported without a threshold — see MINIMUM in lib/contrast.ts.
  { label: "Border (decorative)", token: "--border", use: "decorative" },
];

interface Measured {
  ratio: number;
  bgHex: string;
  fgHex: string;
  bgRaw: string;
  fgRaw: string;
}

function measure(bg: string, fg: string): Measured | null {
  const bgRgb = resolveToken(bg);
  const fgRgb = resolveToken(fg);
  if (!bgRgb || !fgRgb) return null;
  return {
    ratio: contrastRatio(bgRgb, fgRgb),
    bgHex: toHex(bgRgb),
    fgHex: toHex(fgRgb),
    bgRaw: tokenValue(bg),
    fgRaw: tokenValue(fg),
  };
}

/**
 * Live token explorer.
 *
 * Contrast ratios are measured from the rendered output at runtime (canvas
 * pixel readback), so switching theme re-measures against whatever the browser
 * actually painted. Nothing here is a hardcoded claim.
 */
export function TokenExplorer() {
  const { resolvedTheme } = useTheme();
  const [tick, setTick] = useState(0);
  const mounted = useIsHydrated();

  // Re-measure after the theme class lands. A frame of delay lets the View
  // Transition commit before we read computed styles.
  useEffect(() => {
    if (!mounted) return;
    const id = requestAnimationFrame(() => setTick((t) => t + 1));
    return () => cancelAnimationFrame(id);
  }, [resolvedTheme, mounted]);

  if (!mounted) {
    return (
      <p className="rounded-xl border border-dashed border-border px-4 py-12 text-center text-sm text-muted-foreground">
        Measuring tokens…
      </p>
    );
  }

  return (
    <div key={tick} className="space-y-12">
      <PairTable
        title="Surfaces & text"
        description="Every surface paired with the text token that sits on it. AA requires 4.5:1 for body text. Colour values are the browser's computed form — Chromium canonicalises OKLCH to lab(), so these are what it resolved to, not what was authored; the authored OKLCH lives in globals.css."
        pairs={SURFACES}
      />
      <PairTable
        title="Routing paths"
        description="Solid badge fills with their own foreground. The three hues are separated by hue distance (cyan 210° / violet 296° / emerald 156°), not lightness, so they stay distinguishable under deuteranopia."
        pairs={ROUTES}
      />
      <PairTable
        title="Risk tiers"
        description="Tinted-background chips for late-delivery and port-congestion scoring."
        pairs={RISKS}
      />
      <MarkTable />
      <TypeScale />
    </div>
  );
}

function PairTable({
  title,
  description,
  pairs,
}: {
  title: string;
  description: string;
  pairs: Pair[];
}) {
  return (
    <section>
      <h2 className="font-semibold tracking-tight" style={{ fontSize: "var(--text-step-2)" }}>
        {title}
      </h2>
      <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted-foreground">
        {description}
      </p>

      <div className="mt-5 overflow-x-auto">
        <table className="w-full min-w-[44rem] border-separate border-spacing-y-2 text-sm">
          <caption className="sr-only">
            {title} — measured WCAG contrast ratios
          </caption>
          <thead>
            <tr className="text-left text-xs uppercase tracking-wider text-muted-foreground">
              <th scope="col" className="px-3 py-1 font-medium">Sample</th>
              <th scope="col" className="px-3 py-1 font-medium">Background</th>
              <th scope="col" className="px-3 py-1 font-medium">Foreground</th>
              <th scope="col" className="px-3 py-1 font-medium">Ratio</th>
            </tr>
          </thead>
          <tbody>
            {pairs.map((pair) => {
              const m = measure(pair.bg, pair.fg);
              return (
                <tr key={pair.label}>
                  <td className="rounded-l-lg border border-r-0 border-border p-2">
                    <div
                      className="flex h-14 items-center rounded-md px-3"
                      style={{
                        background: `var(${pair.bg})`,
                        color: `var(${pair.fg})`,
                      }}
                    >
                      <span className="text-sm font-medium">{pair.label}</span>
                    </div>
                    {pair.note && (
                      <p className="px-1 pt-1.5 text-xs text-muted-foreground">
                        {pair.note}
                      </p>
                    )}
                  </td>
                  <td className="border-y border-border px-3 py-2 align-top">
                    <TokenCell raw={m?.bgRaw} hex={m?.bgHex} name={pair.bg} />
                  </td>
                  <td className="border-y border-border px-3 py-2 align-top">
                    <TokenCell raw={m?.fgRaw} hex={m?.fgHex} name={pair.fg} />
                  </td>
                  <td className="rounded-r-lg border border-l-0 border-border px-3 py-2 align-top">
                    {m ? <RatioChip ratio={m.ratio} use={pair.use} /> : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function MarkTable() {
  return (
    <section>
      <h2 className="font-semibold tracking-tight" style={{ fontSize: "var(--text-step-2)" }}>
        Marks on the page background
      </h2>
      <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted-foreground">
        Chart series, accent text and borders measured against{" "}
        <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
          --background
        </code>
        . WCAG 1.4.11 requires 3:1 for non-text UI components.
      </p>

      <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {ON_BACKGROUND.map((item) => {
          const m = measure("--background", item.token);
          return (
            <div
              key={item.token}
              className="flex items-center gap-3 rounded-lg border border-border bg-card px-3 py-3"
            >
              <span
                aria-hidden
                className="size-8 shrink-0 rounded-md"
                style={{ background: `var(${item.token})` }}
              />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{item.label}</p>
                <p className="truncate font-mono text-xs text-muted-foreground">
                  {m?.fgHex ?? "—"}
                </p>
              </div>
              {m && <RatioChip ratio={m.ratio} use={item.use} />}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function TypeScale() {
  const STEPS = [
    { token: "--text-hero", label: "Hero" },
    { token: "--text-step-5", label: "Step 5" },
    { token: "--text-step-4", label: "Step 4" },
    { token: "--text-step-3", label: "Step 3" },
    { token: "--text-step-2", label: "Step 2" },
    { token: "--text-step-1", label: "Step 1" },
    { token: "--text-step-0", label: "Step 0 · body" },
    { token: "--text-step--1", label: "Step −1" },
  ];

  return (
    <section>
      <h2 className="font-semibold tracking-tight" style={{ fontSize: "var(--text-step-2)" }}>
        Fluid type scale
      </h2>
      <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted-foreground">
        Every step is a <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">clamp()</code>{" "}
        so sizes interpolate with the viewport instead of jumping at
        breakpoints. Resize the window to see it move.
      </p>

      <div className="mt-5 divide-y divide-border rounded-xl border border-border">
        {STEPS.map((step) => (
          <div
            key={step.token}
            className="flex flex-wrap items-baseline gap-x-4 gap-y-1 px-4 py-3"
          >
            <span
              className="font-semibold tracking-tight"
              style={{ fontSize: `var(${step.token})` }}
            >
              Jebel Ali
            </span>
            <span className="ml-auto font-mono text-xs text-muted-foreground">
              {step.token}
            </span>
            <span className="tabular w-full font-mono text-xs text-muted-foreground sm:w-auto">
              {step.label}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}

function TokenCell({
  name,
  raw,
  hex,
}: {
  name: string;
  raw?: string;
  hex?: string;
}) {
  return (
    <div className="space-y-0.5">
      <p className="font-mono text-xs text-muted-foreground">{name}</p>
      <p className="font-mono text-xs">{raw ?? "—"}</p>
      <p className="font-mono text-xs text-muted-foreground">{hex ?? ""}</p>
    </div>
  );
}

function RatioChip({ ratio, use }: { ratio: number; use: ContrastUse }) {
  if (use === "decorative") {
    return (
      <span className="tabular inline-flex items-center gap-1.5 whitespace-nowrap rounded-md bg-muted px-2 py-1 text-xs font-medium text-muted-foreground">
        {ratio.toFixed(2)}:1
        <span className="opacity-70">no threshold</span>
      </span>
    );
  }

  const ok = passes(ratio, use);
  const label = use === "ui" ? "AA · UI" : "AA";
  return (
    <span
      className={cn(
        "tabular inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-2 py-1 text-xs font-medium",
        ok
          ? "bg-risk-low-muted text-risk-low"
          : "bg-risk-high-muted text-risk-high",
      )}
    >
      {ratio.toFixed(2)}:1
      <span className="opacity-70">
        {ok ? `${label} pass` : `${label} fail (needs ${MINIMUM[use]}:1)`}
      </span>
    </span>
  );
}
