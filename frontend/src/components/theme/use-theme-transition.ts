"use client";

import { useTheme } from "next-themes";
import { useCallback, useRef } from "react";
import { flushSync } from "react-dom";

export type ThemeChoice = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

export const THEME_CHOICES: readonly ThemeChoice[] = ["light", "dark", "system"];

const REVEAL_DURATION_MS = 620;
/** Same expo-out curve as the rest of the site's motion, for cohesion. */
const REVEAL_EASING = "cubic-bezier(0.16, 1, 0.3, 1)";

function systemTheme(): ResolvedTheme {
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function resolve(choice: ThemeChoice): ResolvedTheme {
  return choice === "system" ? systemTheme() : choice;
}

function wantsReducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function supportsViewTransitions(): boolean {
  return typeof document.startViewTransition === "function";
}

/**
 * Write the resolved theme to <html> *synchronously*.
 *
 * This is load-bearing. next-themes' own `setTheme` only sets React state and
 * localStorage — it applies the class from a passive effect, which React does
 * not guarantee to flush inside a `startViewTransition` callback. If we relied
 * on it alone the browser would snapshot an unchanged DOM and animate a wipe
 * between two identical frames. Doing it here makes the swap deterministic;
 * next-themes' effect then re-applies the same value as a no-op.
 */
function applyResolvedTheme(resolved: ResolvedTheme) {
  const root = document.documentElement;
  root.classList.remove("light", "dark");
  root.classList.add(resolved);
  root.style.colorScheme = resolved;
}

/** Distance from (x, y) to the farthest corner of the viewport. */
function radiusToFarthestCorner(x: number, y: number): number {
  const { innerWidth: w, innerHeight: h } = window;
  return Math.hypot(Math.max(x, w - x), Math.max(y, h - y));
}

export interface UseThemeTransitionResult {
  /** The user's explicit choice: "light" | "dark" | "system". */
  choice: ThemeChoice | undefined;
  /** What that choice currently paints as. */
  resolved: ResolvedTheme | undefined;
  /** What the OS is asking for, regardless of the current choice. */
  system: ResolvedTheme | undefined;
  /** Change theme, animating outward from `origin` when possible. */
  setThemeAt: (next: ThemeChoice, origin?: { x: number; y: number }) => void;
}

/**
 * Theme switching with a circular-reveal View Transition.
 *
 * The animation always moves the *dark* layer, in both directions:
 *   → dark:  the new dark snapshot grows from the click point.
 *   → light: the old dark snapshot shrinks back into the click point.
 * So the mental model stays consistent — a dark curtain drawn across or away —
 * and, in particular, switching to light never blasts an expanding white disc
 * across a dark viewport.
 *
 * Degrades cleanly, in this order:
 *   1. `prefers-reduced-motion: reduce` → instant swap, no transition at all.
 *   2. No View Transitions API (Firefox < 144, older Safari) → instant swap.
 *   3. No visible change (e.g. "system" picked while already matching) →
 *      persist the choice only, don't animate a no-op.
 */
export function useThemeTransition(): UseThemeTransitionResult {
  const { theme, resolvedTheme, systemTheme: sysTheme, setTheme } = useTheme();
  const inFlight = useRef(false);

  const commit = useCallback(
    (next: ThemeChoice) => {
      flushSync(() => setTheme(next));
      applyResolvedTheme(resolve(next));
    },
    [setTheme],
  );

  const setThemeAt = useCallback(
    (next: ThemeChoice, origin?: { x: number; y: number }) => {
      const nextResolved = resolve(next);
      const currentResolved =
        (document.documentElement.classList.contains("dark")
          ? "dark"
          : "light") as ResolvedTheme;

      const noVisualChange = nextResolved === currentResolved;

      if (
        noVisualChange ||
        !origin ||
        inFlight.current ||
        wantsReducedMotion() ||
        !supportsViewTransitions()
      ) {
        commit(next);
        return;
      }

      const { x, y } = origin;
      const radius = radiusToFarthestCorner(x, y);
      const goingDark = nextResolved === "dark";
      const root = document.documentElement;

      // Tells globals.css which snapshot to stack on top. "peel" lifts the
      // outgoing (dark) snapshot above the incoming light one.
      if (!goingDark) root.dataset.themeTransition = "peel";

      inFlight.current = true;
      const transition = document.startViewTransition(() => commit(next));

      transition.ready
        .then(() => {
          const grow = [
            `circle(0px at ${x}px ${y}px)`,
            `circle(${radius}px at ${x}px ${y}px)`,
          ];

          root.animate(
            { clipPath: goingDark ? grow : [...grow].reverse() },
            {
              duration: REVEAL_DURATION_MS,
              easing: REVEAL_EASING,
              fill: "forwards",
              pseudoElement: goingDark
                ? "::view-transition-new(root)"
                : "::view-transition-old(root)",
            },
          );
        })
        // A skipped transition rejects `ready`; that is not an error worth
        // surfacing — the theme has already been applied either way.
        .catch(() => {});

      transition.finished.finally(() => {
        delete root.dataset.themeTransition;
        inFlight.current = false;
      });
    },
    [commit],
  );

  return {
    choice: theme as ThemeChoice | undefined,
    resolved: resolvedTheme as ResolvedTheme | undefined,
    system: sysTheme,
    setThemeAt,
  };
}
