# MARSA AI — frontend production image.
#
# Multi-stage so the runtime layer carries the traced standalone output and
# nothing else: no npm, no source, no dev dependencies.

# ── deps ────────────────────────────────────────────────────────────────────
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ── build ───────────────────────────────────────────────────────────────────
FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# NEXT_PUBLIC_* is inlined into the client bundle at build time, not read at
# runtime — so the gateway URL must be known HERE. Passing it only to `docker
# run` produces an image that silently talks to localhost:8000 from the user's
# browser and falls back to fixture mode with no error.
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL

ENV NEXT_TELEMETRY_DISABLED=1

# `public/` is currently empty, and git does not track empty directories — so on
# a fresh clone it does not exist and the runtime COPY below fails the build.
RUN mkdir -p public && npm run build

# ── runtime ─────────────────────────────────────────────────────────────────
FROM node:22-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

# Unprivileged runtime user. The standalone server needs no write access.
RUN addgroup -g 1001 -S nodejs && adduser -u 1001 -S nextjs -G nodejs

# The standalone server serves neither `public/` nor `.next/static/` unless they
# are placed alongside it — see next.config.ts.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs
EXPOSE 3000
ENV PORT=3000
# 0.0.0.0, not the default localhost — bound to loopback inside a container the
# port mapping resolves but every request hangs.
ENV HOSTNAME=0.0.0.0

CMD ["node", "server.js"]
