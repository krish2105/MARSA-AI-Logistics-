import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";

import { SiteFooter } from "@/components/layout/site-footer";
import { SiteHeader } from "@/components/layout/site-header";
import { ThemeProvider } from "@/components/theme/theme-provider";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

// Reserved for anything the eye compares column-to-column — HTS codes,
// latency figures, cost estimates, ruling numbers.
const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: {
    default: "MARSA AI — Adaptive-RAG Trade & Logistics Copilot",
    template: "%s · MARSA AI",
  },
  description:
    "Not all questions deserve the same amount of computation. MARSA AI routes each trade and logistics query to the cheapest retrieval path that can answer it correctly — and shows you the decision.",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f7f8fa" },
    { media: "(prefers-color-scheme: dark)", color: "#0b1220" },
  ],
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    // suppressHydrationWarning is required and expected: next-themes' blocking
    // script writes the theme class onto <html> before React hydrates, so the
    // server-rendered markup intentionally differs from the first client read.
    <html
      lang="en"
      suppressHydrationWarning
      className={`${inter.variable} ${jetbrainsMono.variable}`}
    >
      <body className="min-h-dvh bg-background text-foreground">
        <ThemeProvider>
          <a
            href="#main"
            className="sr-only rounded-md bg-primary px-4 py-2 font-medium text-primary-foreground focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50"
          >
            Skip to content
          </a>
          <div className="flex min-h-dvh flex-col">
            <SiteHeader />
            <main id="main" className="flex-1">
              {children}
            </main>
            <SiteFooter />
          </div>
        </ThemeProvider>
      </body>
    </html>
  );
}
