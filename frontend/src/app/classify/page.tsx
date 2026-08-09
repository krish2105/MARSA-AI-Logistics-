import type { Metadata } from "next";

import { ClassifyConsole } from "@/components/classify/classify-console";

export const metadata: Metadata = {
  title: "Classification",
  description:
    "Commodity classification grounded in binding CBP rulings — and a refusal when the rulings do not support one.",
};

export default function ClassifyPage() {
  return (
    <main className="mx-auto w-full max-w-5xl px-4 py-12 sm:px-6 lg:py-16">
      <header className="max-w-2xl">
        <p className="text-xs uppercase tracking-wide text-muted-foreground">
          Phase J
        </p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">
          Classification that declines
        </h1>
        <p className="mt-4 text-sm leading-relaxed text-muted-foreground">
          Automated HS classification is a commodity — a dozen vendors sell it,
          and most of them always return an answer. This one competes on the
          axis they are weak on: every suggestion cites the binding ruling that
          supports it, and when the rulings do not support one, it says so.
        </p>
        <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
          Five of the twenty classification questions in the evaluation set have
          no answering document in the ingested corpus. Before this page
          existed, the system answered all five with five sources apiece. Under
          a reasonable-care standard a documented refusal is worth more than a
          confident guess, so a refusal here is a result, not a failure.
        </p>
      </header>

      <section className="mt-10">
        <ClassifyConsole />
      </section>

      <footer className="mt-12 border-t border-border pt-6">
        <p className="text-xs leading-relaxed text-muted-foreground">
          Suggestions are grounded in a working subset of CBP CROSS rulings, not
          the full database, and are not customs advice. The abstention
          threshold is a shipped default chosen from the measured curve in
          <code className="mx-1 font-mono">RESULTS.md</code>; it is a
          trade-off, not an optimum, and a caller with a different tolerance for
          a wrong classification should move it.
        </p>
      </footer>
    </main>
  );
}
