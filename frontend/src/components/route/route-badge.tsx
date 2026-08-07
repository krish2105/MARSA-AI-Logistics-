"use client";

import { motion, useReducedMotion } from "motion/react";
import { Gauge, Network, Route, Workflow } from "lucide-react";

import { cn } from "@/lib/utils";
import {
  ROUTE_PATHS,
  formatCost,
  formatLatency,
  type RoutePathId,
} from "@/lib/routing";

const ICONS: Record<RoutePathId, typeof Route> = {
  fast: Gauge,
  agentic: Workflow,
  graph: Network,
};

/**
 * The Route Badge — the governance surface of this product.
 *
 * The router makes an autonomous decision about how much computation a query
 * deserves. This component is what makes that decision auditable at a glance:
 * which path, why, how confident, what it cost, how long it took. It is
 * deliberately not decorative and deliberately not in a log.
 *
 * Colour alone never carries the routing decision — the path name is always
 * present as text, and each path has its own icon, so the badge survives
 * greyscale, colour-blindness and screen readers.
 */
export function RouteBadge({
  path,
  confidence,
  rationale,
  latencyMs,
  costUsd,
  className,
}: {
  path: RoutePathId;
  confidence: number;
  rationale: string;
  latencyMs?: number;
  costUsd?: number;
  className?: string;
}) {
  const meta = ROUTE_PATHS[path];
  const Icon = ICONS[path];
  const reduce = useReducedMotion();

  return (
    <motion.div
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, ease: [0.16, 1, 0.3, 1] }}
      className={cn(
        "overflow-hidden rounded-xl border border-border bg-card",
        className,
      )}
    >
      {/* Path identity */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border/70 px-4 py-3">
        <span
          className={cn(
            "inline-flex items-center gap-2 rounded-full px-3 py-1",
            "text-sm font-semibold",
            meta.accent,
            path === "fast" && "text-route-fast-foreground",
            path === "agentic" && "text-route-agentic-foreground",
            path === "graph" && "text-route-graph-foreground",
          )}
        >
          <Icon className="size-4" aria-hidden />
          Routed to: {meta.label}
        </span>

        <code className="rounded-md bg-muted px-2 py-1 font-mono text-xs text-muted-foreground">
          {meta.classification}
        </code>

        <span className="tabular ml-auto text-xs text-muted-foreground">
          confidence{" "}
          <strong className="font-semibold text-foreground">
            {(confidence * 100).toFixed(0)}%
          </strong>
        </span>
      </div>

      {/* Why — the part that turns a badge into an audit record */}
      <div className="space-y-3 px-4 py-3">
        <p className="text-sm leading-relaxed text-muted-foreground">
          {rationale}
        </p>

        <dl className="tabular flex flex-wrap gap-x-6 gap-y-2 text-xs">
          <div className="flex gap-2">
            <dt className="text-muted-foreground">Strategy</dt>
            <dd className={cn("font-medium", meta.accentText)}>
              {meta.strategy}
            </dd>
          </div>
          {latencyMs !== undefined && (
            <div className="flex gap-2">
              <dt className="text-muted-foreground">Latency</dt>
              <dd className="font-medium">{formatLatency(latencyMs)}</dd>
            </div>
          )}
          {costUsd !== undefined && (
            <div className="flex gap-2">
              <dt className="text-muted-foreground">Est. cost</dt>
              <dd className="font-medium">{formatCost(costUsd)}</dd>
            </div>
          )}
        </dl>
      </div>
    </motion.div>
  );
}
