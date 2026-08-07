"use client";

import { ThemeProvider as NextThemesProvider } from "next-themes";
import type { ComponentProps } from "react";

/**
 * Thin wrapper over next-themes so the rest of the app never imports the
 * library directly and the configuration lives in exactly one place.
 *
 * `attribute="class"` — next-themes writes `.light` / `.dark` onto <html>,
 *   which is what the `@custom-variant dark` rule in globals.css keys off.
 * `defaultTheme="system"` with `enableSystem` — the app honours the OS until
 *   the user makes an explicit choice, which is the accessible default.
 * `disableTransitionOnChange` — suppresses CSS transitions during a swap.
 *   The theme animation is owned entirely by the View Transitions API in
 *   ThemeToggle; letting CSS transitions run as well would double-animate.
 *
 * No-flash guarantee: next-themes injects a blocking inline script that sets
 * the class before first paint, so there is no light-mode flash on a dark
 * reload. That script is why <html> needs `suppressHydrationWarning`.
 */
export function ThemeProvider({
  children,
  ...props
}: ComponentProps<typeof NextThemesProvider>) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      enableColorScheme
      disableTransitionOnChange
      storageKey="marsa-theme"
      {...props}
    >
      {children}
    </NextThemesProvider>
  );
}
