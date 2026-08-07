"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { useRef } from "react";

import { useIsHydrated } from "@/lib/use-is-hydrated";
import { cn } from "@/lib/utils";
import { useThemeTransition, type ThemeChoice } from "./use-theme-transition";

const OPTIONS = [
  { value: "light", label: "Light", Icon: Sun },
  { value: "dark", label: "Dark", Icon: Moon },
  { value: "system", label: "System", Icon: Monitor },
] as const satisfies readonly {
  value: ThemeChoice;
  label: string;
  Icon: typeof Sun;
}[];

/**
 * Three-state theme control: Light / Dark / System.
 *
 * Implemented as an ARIA `radiogroup` rather than three independent buttons or
 * a `switch`, because the three options are mutually exclusive and "System" is
 * not an on/off state. That earns the standard radio keyboard contract, which
 * is implemented here in full:
 *
 *   - Roving tabindex: the group is one tab stop, not three.
 *   - Arrow keys move *and* select (WAI-ARIA radiogroup behaviour).
 *   - Home / End jump to the first / last option.
 *
 * Before hydration `choice` is undefined, so nothing is marked checked and the
 * thumb is not rendered — server and first client render agree, which is what
 * keeps this out of hydration-mismatch territory. The control reserves its
 * final size from the first paint, so there is no layout shift on mount.
 */
export function ThemeToggle({ className }: { className?: string }) {
  const { choice, resolved, system, setThemeAt } = useThemeTransition();
  const reduceMotion = useReducedMotion();
  const mounted = useIsHydrated();
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const activeIndex = OPTIONS.findIndex((o) => o.value === choice);

  function select(next: ThemeChoice, index: number, fromKeyboard: boolean) {
    const el = refs.current[index];
    let origin: { x: number; y: number } | undefined;

    if (el) {
      // Keyboard activations have no pointer coordinates, so the reveal
      // originates from the centre of the option that was just chosen —
      // the animation stays anchored to the thing the user acted on.
      const rect = el.getBoundingClientRect();
      origin = { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    }

    setThemeAt(next, origin);
    if (fromKeyboard) el?.focus();
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    const last = OPTIONS.length - 1;
    const from = activeIndex === -1 ? 0 : activeIndex;
    let next: number | null = null;

    switch (event.key) {
      case "ArrowRight":
      case "ArrowDown":
        next = from === last ? 0 : from + 1;
        break;
      case "ArrowLeft":
      case "ArrowUp":
        next = from === 0 ? last : from - 1;
        break;
      case "Home":
        next = 0;
        break;
      case "End":
        next = last;
        break;
      default:
        return;
    }

    event.preventDefault();
    select(OPTIONS[next].value, next, true);
  }

  // Exactly one element in the group is tabbable. Before hydration that is the
  // first option; afterwards it is whichever option is currently selected.
  const tabbableIndex = activeIndex === -1 ? 0 : activeIndex;

  return (
    <div
      role="radiogroup"
      aria-label="Colour theme"
      onKeyDown={onKeyDown}
      className={cn(
        "relative inline-flex items-center gap-0.5 rounded-full",
        "border border-border bg-muted/60 p-1",
        "supports-[backdrop-filter:blur(0px)]:bg-muted/40 supports-[backdrop-filter:blur(0px)]:backdrop-blur-sm",
        className,
      )}
    >
      {OPTIONS.map((option, index) => {
        const checked = mounted && choice === option.value;
        const { Icon } = option;

        // "System" gets a suffix so a screen-reader user knows what the OS is
        // currently asking for without having to switch to it and find out.
        const accessibleLabel =
          option.value === "system" && mounted && system
            ? `System (currently ${system})`
            : option.label;

        return (
          <button
            key={option.value}
            ref={(el) => {
              refs.current[index] = el;
            }}
            type="button"
            role="radio"
            aria-checked={checked}
            aria-label={accessibleLabel}
            tabIndex={index === tabbableIndex ? 0 : -1}
            onClick={(event) =>
              select(
                option.value,
                index,
                // A click with no real coordinates is a keyboard-synthesised
                // one (Space / Enter on a focused radio).
                event.detail === 0,
              )
            }
            className={cn(
              "relative inline-flex size-8 items-center justify-center rounded-full",
              "transition-colors duration-150 outline-none",
              "text-muted-foreground hover:text-foreground",
              "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
              checked && "text-primary-foreground",
            )}
          >
            {checked && (
              <motion.span
                aria-hidden
                layoutId={reduceMotion ? undefined : "theme-toggle-thumb"}
                transition={{ type: "spring", stiffness: 420, damping: 34 }}
                className="absolute inset-0 -z-10 rounded-full bg-primary shadow-sm"
              />
            )}
            <Icon className="size-4" strokeWidth={2} aria-hidden />
          </button>
        );
      })}

      {/*
        Politely announces the *result* of the change. aria-checked already
        covers keyboard users navigating the group, but a pointer user who
        clicks "System" gets no feedback otherwise — and "System" is precisely
        the option whose outcome is not self-evident from its label.
      */}
      <span aria-live="polite" className="sr-only">
        {mounted && resolved ? `${resolved} theme active` : ""}
      </span>
    </div>
  );
}
