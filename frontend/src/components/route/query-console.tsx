"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Check, CornerDownLeft, Loader2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { RouteBadge } from "@/components/route/route-badge";
import { ROUTE_PATHS, SAMPLE_RESPONSES } from "@/lib/routing";
import { cn } from "@/lib/utils";

type Stage = "idle" | "classifying" | "routed" | "complete";

/** Playback timings for the mock stream, in ms. */
const CLASSIFY_MS = 900;
const STEP_STAGGER_MS = 520;
const ANSWER_DELAY_MS = 420;

/**
 * Query console with a staged reveal: classifier → Route Badge → retrieval
 * steps → answer.
 *
 * The sequencing is the point. Showing *why* the router picked a path before
 * showing what it found is what separates this from a chat box, so the badge
 * lands first and the steps pace the reasoning.
 *
 * Phase 1 replays local fixtures. In Phase E the same state machine is driven
 * by SSE events from the FastAPI gateway — the stages map 1:1 onto the events
 * the router already emits, so only the data source changes.
 *
 * Under `prefers-reduced-motion` the staging collapses to a single frame: the
 * full result appears at once, with no timed reveal and no transforms.
 */
export function QueryConsole() {
  const reduce = useReducedMotion();
  const [index, setIndex] = useState(0);
  const [run, setRun] = useState(0);
  const [stage, setStage] = useState<Stage>("idle");
  const [visibleSteps, setVisibleSteps] = useState(0);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  const response = SAMPLE_RESPONSES[index];

  const clearTimers = useCallback(() => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
  }, []);

  useEffect(() => clearTimers, [clearTimers]);

  const start = useCallback(
    (nextIndex: number) => {
      clearTimers();
      setIndex(nextIndex);
      setRun((r) => r + 1);
      setVisibleSteps(0);

      const target = SAMPLE_RESPONSES[nextIndex];

      if (reduce) {
        setStage("complete");
        setVisibleSteps(target.steps.length);
        return;
      }

      setStage("classifying");

      const at = (ms: number, fn: () => void) => {
        timers.current.push(setTimeout(fn, ms));
      };

      at(CLASSIFY_MS, () => setStage("routed"));

      target.steps.forEach((_, i) => {
        at(CLASSIFY_MS + STEP_STAGGER_MS * (i + 1), () =>
          setVisibleSteps(i + 1),
        );
      });

      at(
        CLASSIFY_MS +
          STEP_STAGGER_MS * target.steps.length +
          ANSWER_DELAY_MS,
        () => setStage("complete"),
      );
    },
    [clearTimers, reduce],
  );

  const busy = stage === "classifying" || (stage === "routed" && visibleSteps < response.steps.length);

  return (
    <section
      aria-labelledby="console-heading"
      className="rounded-2xl border border-border bg-card/60 p-4 sm:p-6"
    >
      <h2 id="console-heading" className="sr-only">
        Query console
      </h2>

      {/* Sample queries — the demo is the routing decision, so the queries are
          curated to hit one path each rather than left to free text. */}
      <fieldset className="space-y-3">
        <legend className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
          Try a query
        </legend>
        <div className="grid gap-2">
          {SAMPLE_RESPONSES.map((sample, i) => {
            const meta = ROUTE_PATHS[sample.path];
            const active = i === index && stage !== "idle";
            return (
              <button
                key={sample.query}
                type="button"
                onClick={() => start(i)}
                aria-pressed={active}
                className={cn(
                  "group flex w-full items-start gap-3 rounded-xl border px-4 py-3 text-left",
                  "transition-colors",
                  active
                    ? "border-primary/50 bg-muted"
                    : "border-border bg-background hover:bg-muted/60",
                )}
              >
                <span
                  aria-hidden
                  className={cn("mt-1.5 size-2 shrink-0 rounded-full", meta.accent)}
                />
                <span className="flex-1 text-sm leading-relaxed">
                  {sample.query}
                </span>
                <CornerDownLeft
                  aria-hidden
                  className="mt-0.5 size-4 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100"
                />
              </button>
            );
          })}
        </div>
      </fieldset>

      {/* Live region so screen-reader users get the staged result announced
          rather than silently appearing. */}
      <div aria-live="polite" aria-atomic="false" className="mt-6 space-y-4">
        {stage === "idle" && (
          <p className="rounded-xl border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
            Pick a query above to see which path the router chooses — and why.
          </p>
        )}

        {stage === "classifying" && (
          <div className="flex items-center gap-3 rounded-xl border border-border bg-background px-4 py-4 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" aria-hidden />
            Classifying query complexity…
          </div>
        )}

        {(stage === "routed" || stage === "complete") && (
          <div key={run} className="space-y-4">
            <RouteBadge
              path={response.path}
              confidence={response.confidence}
              rationale={response.rationale}
              latencyMs={stage === "complete" ? response.latencyMs : undefined}
              costUsd={stage === "complete" ? response.costUsd : undefined}
            />

            <ol className="space-y-1.5">
              <AnimatePresence initial={false}>
                {response.steps.slice(0, visibleSteps).map((step) => (
                  <motion.li
                    key={step.label + step.detail}
                    initial={reduce ? false : { opacity: 0, x: -8 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
                    className="flex items-center gap-3 rounded-lg bg-muted/50 px-3 py-2 text-sm"
                  >
                    <Check
                      aria-hidden
                      className="size-3.5 shrink-0 text-risk-low"
                    />
                    <span className="font-medium">{step.label}</span>
                    <span className="tabular ml-auto text-right font-mono text-xs text-muted-foreground">
                      {step.detail}
                    </span>
                  </motion.li>
                ))}
              </AnimatePresence>
            </ol>

            {busy && (
              <p className="flex items-center gap-2 px-3 text-xs text-muted-foreground">
                <Loader2 className="size-3 animate-spin" aria-hidden />
                Retrieving…
              </p>
            )}

            {stage === "complete" && (
              <motion.div
                initial={reduce ? false : { opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
                className="rounded-xl border border-border bg-background p-4"
              >
                <p className="text-sm leading-relaxed">{response.answer}</p>
                <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border/70 pt-3">
                  <span className="text-xs text-muted-foreground">Sources</span>
                  {response.citations.map((c) => (
                    <code
                      key={c}
                      className="rounded bg-muted px-2 py-0.5 font-mono text-xs text-muted-foreground"
                    >
                      {c}
                    </code>
                  ))}
                </div>
              </motion.div>
            )}
          </div>
        )}
      </div>

      <p className="mt-5 text-xs text-muted-foreground">
        Phase 1 replays local fixtures. Latency and cost figures are
        illustrative placeholders until the Phase F benchmark runner measures
        them.
      </p>
    </section>
  );
}
