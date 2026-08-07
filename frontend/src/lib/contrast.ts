/**
 * WCAG contrast measurement for the token explorer.
 *
 * The tokens are authored in OKLCH, which cannot be turned into sRGB with a
 * one-line formula. Rather than ship a colour-conversion dependency, this
 * resolves colours the way the browser already does: paint the value onto a
 * 1×1 canvas and read the pixel back. Whatever the browser renders is, by
 * definition, the colour the user sees — so the reported ratios describe the
 * real output rather than a re-implementation of it.
 *
 * Browser-only: every function here touches the DOM.
 */

export type Rgb = [number, number, number];

/** Resolve any CSS colour string (oklch, hex, rgb, …) to sRGB 0–255. */
export function resolveColor(value: string): Rgb | null {
  const canvas = document.createElement("canvas");
  canvas.width = 1;
  canvas.height = 1;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) return null;

  // An invalid fillStyle assignment is ignored and leaves the previous value,
  // so seed with a sentinel and bail if the assignment did not take.
  ctx.fillStyle = "#000000";
  ctx.fillStyle = value;
  if (ctx.fillStyle === "#000000" && value.trim() !== "#000000") {
    // Could legitimately be black; verify by seeding with a different colour.
    ctx.fillStyle = "#ffffff";
    ctx.fillStyle = value;
    if (ctx.fillStyle === "#ffffff") return null;
  }

  ctx.fillRect(0, 0, 1, 1);
  const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
  return [r, g, b];
}

/** Read a custom property off <html> and resolve it. */
export function resolveToken(token: string): Rgb | null {
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue(token)
    .trim();
  return raw ? resolveColor(raw) : null;
}

/** Raw authored value of a custom property, for display. */
export function tokenValue(token: string): string {
  return getComputedStyle(document.documentElement)
    .getPropertyValue(token)
    .trim();
}

export function toHex([r, g, b]: Rgb): string {
  return `#${[r, g, b].map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

/** WCAG 2.1 relative luminance. */
function luminance([r, g, b]: Rgb): number {
  const channel = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

/** WCAG 2.1 contrast ratio, 1–21. */
export function contrastRatio(a: Rgb, b: Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

export type ContrastUse = "body" | "large" | "ui" | "decorative";

/**
 * Minimum ratio for a given use, per WCAG 2.1 AA.
 *
 * `decorative` has no threshold on purpose. WCAG 1.4.11 applies to boundaries
 * that are *required to identify* a control or its state — it explicitly does
 * not apply to purely aesthetic dividers and container edges. Holding a subtle
 * card border to 3:1 would force it to read as a hard rule and would make the
 * report cry wolf, which is worse than not reporting at all.
 */
export const MINIMUM: Record<ContrastUse, number> = {
  body: 4.5, // 1.4.3 — normal text
  large: 3, // 1.4.3 — 18pt+, or 14pt+ bold
  ui: 3, // 1.4.11 — non-text contrast (icons, chart marks, control edges)
  decorative: 0,
};

export function passes(ratio: number, use: ContrastUse): boolean {
  return ratio >= MINIMUM[use];
}
