"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Ban, CornerDownLeft, FileText, Loader2, WifiOff } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  checkHealth,
  classify,
  type ClassifyResult,
  type Citation,
  type Evidence,
} from "@/lib/api";
import { useIsHydrated } from "@/lib/use-is-hydrated";
import { cn } from "@/lib/utils";

type Mode = "checking" | "live" | "offline";

const EXAMPLES = [
  "What HTS code applies to lithium-ion power banks?",
  "What does HQ H289765 say about essential character?",
  "Which subheading applies to a stainless steel vacuum flask?",
  "How is an air conditioning split system classified?",
];

const SIGNAL_LABELS: Record<string, string> = {
  provisionAgreement: "Provision agreement",
  armAgreement: "Arm agreement",
  lexicalAnchoring: "Lexical anchoring",
  codePresence: "Code presence",
};

function SignalBar({ label, value }: { label: string; value: number | null }) {
  const notApplicable = value === null;
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-2 text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-mono tabular-nums">
          {notApplicable ? "n/a" : value.toFixed(2)}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-muted">
        {!notApplicable && (
          <div
            className="h-full rounded-full bg-foreground/60"
            style={{ width: `${Math.round(value * 100)}%` }}
          />
        )}
      </div>
    </div>
  );
}

function CitationCard({ citation, muted }: { citation: Citation; muted?: boolean }) {
  return (
    <li
      className={cn(
        "rounded-lg border p-3 text-sm",
        muted ? "border-dashed border-border/70" : "border-border",
      )}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <a
          href={citation.url}
          target="_blank"
          rel="noreferrer"
          className="font-mono font-medium underline underline-offset-4"
        >
          {citation.rulingNumber}
        </a>
        <span className="font-mono text-xs text-muted-foreground">
          {citation.assignedCodes.slice(0, 3).join(" · ") || "no code assigned"}
        </span>
      </div>
      {citation.quote && (
        <p className="mt-2 line-clamp-3 text-xs leading-relaxed text-muted-foreground">
          {citation.quote}
        </p>
      )}
    </li>
  );
}

function EvidencePanel({ evidence }: { evidence: Evidence }) {
  return (
    <div className="rounded-xl border border-border p-4">
      <h3 className="text-sm font-medium">Evidence</h3>
      <p className="mt-1 text-xs text-muted-foreground">
        Computed from the retrieved rulings. No model is asked whether the
        evidence is sufficient — a judge that can hallucinate would be the one
        failure this check exists to prevent.
      </p>
      <div className="mt-4 space-y-3">
        {Object.entries(SIGNAL_LABELS).map(([key, label]) => (
          <SignalBar
            key={key}
            label={label}
            value={evidence[key as keyof Evidence] as number | null}
          />
        ))}
      </div>
      {evidence.missingRuling && (
        <p className="mt-4 rounded-lg bg-muted p-3 text-xs">
          Query names ruling{" "}
          <span className="font-mono">{evidence.missingRuling}</span>, which is
          not in the corpus. That vetoes the answer outright — no quantity of
          evidence about other documents is evidence about this one.
        </p>
      )}
    </div>
  );
}

/**
 * Classification console: ask, and get either a cited answer or a refusal.
 *
 * The refusal is the point. A commodity classifier that always answers is
 * indistinguishable from one that guesses, so this view gives the two outcomes
 * equal visual weight rather than treating "no answer" as an error state.
 */
export function ClassifyConsole() {
  const reduce = useReducedMotion();
  const hydrated = useIsHydrated();

  const [mode, setMode] = useState<Mode>("checking");
  const [query, setQuery] = useState(EXAMPLES[0]);
  const [result, setResult] = useState<ClassifyResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  //: Distinguishes consecutive runs whose outcome happens to be identical, so
  //: the enter animation replays. Keying on the outcome fields alone would
  //: make two identical refusals look like a dropped request.
  const [runId, setRunId] = useState(0);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    checkHealth(controller.signal).then((ok) => setMode(ok ? "live" : "offline"));
    return () => controller.abort();
  }, []);

  useEffect(() => () => abort.current?.abort(), []);

  const run = useCallback(async () => {
    if (!query.trim() || busy) return;
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;

    setBusy(true);
    setError(null);
    // The previous result deliberately stays on screen until the new one
    // arrives. Clearing it first left `AnimatePresence` mid-exit when the
    // response landed, and the replacement never mounted.
    try {
      const next = await classify(query, controller.signal);
      setRunId((n) => n + 1);
      setResult(next);
    } catch (cause) {
      if ((cause as Error).name === "AbortError") return;
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }, [query, busy]);

  const disabled = mode !== "live" || busy || !hydrated;
  const refused = result?.outcome === "insufficient_evidence";

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-border p-4">
        <label htmlFor="classify-query" className="text-sm font-medium">
          Commodity question
        </label>
        <div className="mt-2 flex flex-col gap-2 sm:flex-row">
          <input
            id="classify-query"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void run();
            }}
            className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm"
            placeholder="What HTS code applies to…"
          />
          <button
            type="button"
            onClick={() => void run()}
            disabled={disabled}
            className={cn(
              "inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2",
              "text-sm font-medium transition",
              disabled
                ? "cursor-not-allowed bg-muted text-muted-foreground"
                : "bg-foreground text-background",
            )}
          >
            {busy ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <CornerDownLeft className="size-4" />
            )}
            Classify
          </button>
        </div>

        <div className="mt-3 flex flex-wrap gap-2">
          {EXAMPLES.map((example) => (
            <button
              key={example}
              type="button"
              onClick={() => setQuery(example)}
              className="rounded-full border border-border px-3 py-1 text-xs text-muted-foreground transition hover:text-foreground"
            >
              {example.length > 46 ? `${example.slice(0, 46)}…` : example}
            </button>
          ))}
        </div>

        {mode === "offline" && (
          <p className="mt-3 inline-flex items-center gap-2 text-xs text-muted-foreground">
            <WifiOff className="size-3.5" />
            The gateway is unreachable, so this console is inert. Unlike the
            copilot it has no fixture mode: a classification you cannot trace to
            a live ruling is exactly what this page exists to refuse.
          </p>
        )}
      </div>

      {error && (
        <p className="rounded-xl border border-border p-4 text-sm text-muted-foreground">
          {error}
        </p>
      )}

      <AnimatePresence initial={false}>
        {result && (
          <motion.div
            key={runId}
            initial={reduce ? false : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            className="grid gap-6 lg:grid-cols-[1.4fr_1fr]"
          >
            <div className="space-y-4">
              <div
                className={cn(
                  "rounded-xl border p-5",
                  refused ? "border-dashed border-border" : "border-border",
                )}
              >
                <div className="flex items-center gap-2">
                  {refused ? (
                    <Ban className="size-4 text-muted-foreground" />
                  ) : (
                    <FileText className="size-4" />
                  )}
                  <span className="text-xs uppercase tracking-wide text-muted-foreground">
                    {refused ? "Insufficient evidence" : "Classified"}
                  </span>
                  <span className="ml-auto font-mono text-xs tabular-nums text-muted-foreground">
                    confidence {result.confidence.toFixed(2)}
                  </span>
                </div>

                {refused ? (
                  <>
                    <p className="mt-3 text-sm">{result.message}</p>
                    <ul className="mt-3 space-y-1.5">
                      {result.reasons.map((reason) => (
                        <li
                          key={reason}
                          className="text-xs leading-relaxed text-muted-foreground"
                        >
                          {reason}
                        </li>
                      ))}
                    </ul>
                  </>
                ) : (
                  <>
                    <p className="mt-3 font-mono text-2xl">{result.subheading}</p>
                    <p className="mt-2 text-sm text-muted-foreground">
                      {result.rationale}
                    </p>
                  </>
                )}
              </div>

              <div>
                <h3 className="text-sm font-medium">
                  {refused ? "Closest rulings" : "Binding rulings cited"}
                </h3>
                <p className="mt-1 text-xs text-muted-foreground">
                  {refused
                    ? "Shown so you can judge them yourself. None of them supports a classification here."
                    : "Every one is classified by CBP under the suggested subheading. A ruling that merely ranked well is not support."}
                </p>
                <ul className="mt-3 space-y-2">
                  {(refused ? result.nearest : result.citations).map((citation) => (
                    <CitationCard
                      key={citation.rulingNumber}
                      citation={citation}
                      muted={refused}
                    />
                  ))}
                </ul>
              </div>
            </div>

            {result.evidence && <EvidencePanel evidence={result.evidence} />}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
