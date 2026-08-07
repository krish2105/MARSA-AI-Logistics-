"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Check, CornerDownLeft, Loader2, Radio, WifiOff } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { RouteBadge } from "@/components/route/route-badge";
import {
  checkHealth,
  streamQuery,
  type AuditPayload,
  type ClassifiedPayload,
  type SourcePayload,
  type StepPayload,
} from "@/lib/api";
import { ROUTE_PATHS, SAMPLE_RESPONSES, type RoutePathId } from "@/lib/routing";
import { useIsHydrated } from "@/lib/use-is-hydrated";
import { cn } from "@/lib/utils";

type Stage = "idle" | "classifying" | "routed" | "complete";
type Mode = "checking" | "live" | "fixture";

/** Playback timings for fixture mode, in ms. */
const CLASSIFY_MS = 900;
const STEP_STAGGER_MS = 520;
const ANSWER_DELAY_MS = 420;

interface Live {
  classified?: ClassifiedPayload;
  steps: StepPayload[];
  sources: SourcePayload[];
  answer?: string;
  audit?: AuditPayload;
  error?: string;
}

/**
 * Query console: classifier → Route Badge → retrieval steps → answer.
 *
 * Runs against the live FastAPI gateway when it is reachable and replays local
 * fixtures when it is not. Which one is active is stated on screen rather than
 * inferred — a demo that silently falls back to canned data is a demo that
 * lies, and this page's entire job is to make the router's behaviour auditable.
 */
export function QueryConsole() {
  const reduce = useReducedMotion();
  const hydrated = useIsHydrated();

  const [mode, setMode] = useState<Mode>("checking");
  const [index, setIndex] = useState(0);
  const [run, setRun] = useState(0);
  const [stage, setStage] = useState<Stage>("idle");
  const [visibleSteps, setVisibleSteps] = useState(0);
  const [live, setLive] = useState<Live>({ steps: [], sources: [] });

  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const abort = useRef<AbortController | null>(null);

  const fixture = SAMPLE_RESPONSES[index];

  // Probe the gateway once on mount.
  useEffect(() => {
    const controller = new AbortController();
    checkHealth(controller.signal).then((ok) => setMode(ok ? "live" : "fixture"));
    return () => controller.abort();
  }, []);

  const clearTimers = useCallback(() => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
  }, []);

  useEffect(() => {
    return () => {
      clearTimers();
      abort.current?.abort();
    };
  }, [clearTimers]);

  // ── Fixture playback ───────────────────────────────────────────────────
  const startFixture = useCallback(
    (nextIndex: number) => {
      const target = SAMPLE_RESPONSES[nextIndex];
      setVisibleSteps(0);

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
        at(CLASSIFY_MS + STEP_STAGGER_MS * (i + 1), () => setVisibleSteps(i + 1));
      });
      at(
        CLASSIFY_MS + STEP_STAGGER_MS * target.steps.length + ANSWER_DELAY_MS,
        () => setStage("complete"),
      );
    },
    [reduce],
  );

  // ── Live streaming ─────────────────────────────────────────────────────
  const startLive = useCallback(async (query: string) => {
    const controller = new AbortController();
    abort.current = controller;
    setStage("classifying");

    try {
      for await (const event of streamQuery(query, controller.signal)) {
        switch (event.event) {
          case "classified":
            setLive((s) => ({ ...s, classified: event.data }));
            setStage("routed");
            break;
          case "step":
            setLive((s) => ({ ...s, steps: [...s.steps, event.data] }));
            break;
          case "sources":
            setLive((s) => ({ ...s, sources: event.data.sources }));
            break;
          case "answer":
            setLive((s) => ({ ...s, answer: event.data.answer }));
            break;
          case "audit":
            setLive((s) => ({ ...s, audit: event.data }));
            setStage("complete");
            break;
          case "error":
            setLive((s) => ({ ...s, error: event.data.detail }));
            setStage("complete");
            break;
        }
      }
    } catch (error) {
      if (controller.signal.aborted) return;
      setLive((s) => ({
        ...s,
        error: error instanceof Error ? error.message : "Stream failed",
      }));
      setStage("complete");
      // A gateway that dies mid-stream is no longer live.
      setMode("fixture");
    }
  }, []);

  const start = useCallback(
    (nextIndex: number) => {
      clearTimers();
      abort.current?.abort();
      setIndex(nextIndex);
      setRun((r) => r + 1);
      setLive({ steps: [], sources: [] });

      if (mode === "live") {
        void startLive(SAMPLE_RESPONSES[nextIndex].query);
      } else {
        startFixture(nextIndex);
      }
    },
    [clearTimers, mode, startFixture, startLive],
  );

  // ── Derived view model ─────────────────────────────────────────────────
  const isLive = mode === "live";
  const path: RoutePathId = isLive
    ? ((live.classified?.path as RoutePathId) ?? fixture.path)
    : fixture.path;

  const steps = isLive
    ? live.steps.map((s) => ({ label: s.label, detail: s.detail }))
    : fixture.steps.slice(0, visibleSteps);

  const answer = isLive ? live.answer : fixture.answer;
  const citations = isLive
    ? live.sources.map((s) => s.ref)
    : fixture.citations;

  const confidence = isLive
    ? (live.classified?.confidence ?? 0)
    : fixture.confidence;
  const rationale = isLive
    ? (live.classified?.rationale ?? "")
    : fixture.rationale;
  const latencyMs = isLive ? live.audit?.latencyMs : fixture.latencyMs;
  const costUsd = isLive ? live.audit?.costUsd : fixture.costUsd;

  const busy =
    stage === "classifying" ||
    (stage === "routed" && !isLive && visibleSteps < fixture.steps.length);

  return (
    <section
      aria-labelledby="console-heading"
      className="rounded-2xl border border-border bg-card/60 p-4 sm:p-6"
    >
      <h2 id="console-heading" className="sr-only">
        Query console
      </h2>

      {/* Mode banner — never leave the viewer guessing which data they see. */}
      {hydrated && (
        <div
          className={cn(
            "mb-4 flex items-center gap-2 rounded-lg px-3 py-2 text-xs",
            mode === "live"
              ? "bg-risk-low-muted text-risk-low"
              : mode === "fixture"
                ? "bg-muted text-muted-foreground"
                : "bg-muted text-muted-foreground",
          )}
        >
          {mode === "live" ? (
            <>
              <Radio className="size-3.5 shrink-0" aria-hidden />
              <span>
                Live — routed by the FastAPI gateway over SSE.
              </span>
            </>
          ) : mode === "fixture" ? (
            <>
              <WifiOff className="size-3.5 shrink-0" aria-hidden />
              <span>
                Gateway unreachable — replaying local fixtures. Start the backend
                with <code className="font-mono">uvicorn marsa.api.main:app</code>{" "}
                for live routing.
              </span>
            </>
          ) : (
            <>
              <Loader2 className="size-3.5 shrink-0 animate-spin" aria-hidden />
              <span>Checking for the gateway…</span>
            </>
          )}
        </div>
      )}

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
                disabled={mode === "checking"}
                className={cn(
                  "group flex w-full items-start gap-3 rounded-xl border px-4 py-3 text-left",
                  "transition-colors disabled:opacity-60",
                  active
                    ? "border-primary/50 bg-muted"
                    : "border-border bg-background hover:bg-muted/60",
                )}
              >
                <span
                  aria-hidden
                  className={cn("mt-1.5 size-2 shrink-0 rounded-full", meta.accent)}
                />
                <span className="flex-1 text-sm leading-relaxed">{sample.query}</span>
                <CornerDownLeft
                  aria-hidden
                  className="mt-0.5 size-4 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100"
                />
              </button>
            );
          })}
        </div>
      </fieldset>

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
              path={path}
              confidence={confidence}
              rationale={rationale}
              latencyMs={stage === "complete" ? latencyMs : undefined}
              costUsd={stage === "complete" ? costUsd : undefined}
            />

            <ol className="space-y-1.5">
              <AnimatePresence initial={false}>
                {steps.map((step, i) => (
                  <motion.li
                    key={`${step.label}-${step.detail}-${i}`}
                    initial={reduce ? false : { opacity: 0, x: -8 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
                    className="flex items-center gap-3 rounded-lg bg-muted/50 px-3 py-2 text-sm"
                  >
                    <Check aria-hidden className="size-3.5 shrink-0 text-risk-low" />
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
                {live.error ? (
                  <p className="text-sm text-risk-high">{live.error}</p>
                ) : (
                  <p className="whitespace-pre-line text-sm leading-relaxed">
                    {answer}
                  </p>
                )}

                {citations.length > 0 && (
                  <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border/70 pt-3">
                    <span className="text-xs text-muted-foreground">Sources</span>
                    {citations.slice(0, 6).map((c) => (
                      <code
                        key={c}
                        className="rounded bg-muted px-2 py-0.5 font-mono text-xs text-muted-foreground"
                      >
                        {c}
                      </code>
                    ))}
                  </div>
                )}

                {isLive && live.audit && live.audit.warnings.length > 0 && (
                  <p className="mt-3 text-xs text-risk-medium">
                    {live.audit.warnings.join(" · ")}
                  </p>
                )}
              </motion.div>
            )}
          </div>
        )}
      </div>

      <p className="mt-5 text-xs text-muted-foreground">
        {isLive
          ? "Latency and cost are measured per query. Cost is priced at published per-token rates; free-tier usage bills $0."
          : "Fixture mode. Latency and cost figures are illustrative placeholders until the Phase F benchmark runner measures them."}
      </p>
    </section>
  );
}
