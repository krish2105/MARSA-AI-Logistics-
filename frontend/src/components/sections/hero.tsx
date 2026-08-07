import Link from "next/link";

export function Hero() {
  return (
    <section className="relative overflow-hidden border-b border-border/80">
      {/* Harbour glow. Pure CSS gradient — no image, no WebGL, no layout cost.
          The R3F globe with animated trade-lane arcs replaces this in the 3D
          phase, lazy-loaded behind progressive enhancement. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10 opacity-70"
        style={{
          background:
            "radial-gradient(60% 55% at 50% -10%, var(--route-fast-muted) 0%, transparent 70%)",
        }}
      />

      <div className="mx-auto max-w-6xl px-4 py-20 sm:px-6 sm:py-28">
        <p className="mb-6 inline-flex items-center gap-2 rounded-full border border-border bg-card px-3 py-1 text-xs text-muted-foreground">
          <span
            aria-hidden
            className="size-1.5 rounded-full bg-route-fast"
          />
          Adaptive-RAG · Dubai trade &amp; logistics
        </p>

        <h1
          className="max-w-4xl font-semibold tracking-tight text-balance"
          style={{ fontSize: "var(--text-hero)", lineHeight: 1.02 }}
        >
          Not all questions deserve the{" "}
          <span className="text-route-fast">same computation</span>.
        </h1>

        <p className="mt-6 max-w-2xl text-pretty leading-relaxed text-muted-foreground">
          MARSA AI routes every trade-compliance and logistics query to the
          cheapest retrieval path that can actually answer it correctly —
          hybrid search, agentic reasoning, or graph traversal — then reports
          the accuracy, cost and latency of that decision instead of assuming
          more complexity is better.
        </p>

        <div className="mt-8 flex flex-wrap items-center gap-3">
          <Link
            href="#console"
            className="rounded-lg bg-primary px-5 py-2.5 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-90"
          >
            See the router decide
          </Link>
          <Link
            href="/theme"
            className="rounded-lg border border-border px-5 py-2.5 text-sm font-medium transition-colors hover:bg-muted"
          >
            Design tokens
          </Link>
        </div>
      </div>
    </section>
  );
}
