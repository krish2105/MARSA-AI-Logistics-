# Running and deploying MARSA AI

Every command here has been executed against this codebase. Where something
does not work, or works differently than you would expect, it says so.

---

## Part 1 — Running it locally

### The fastest path: Docker Compose

One command, no accounts, no API keys, no Python or Node installed:

```bash
git clone https://github.com/krish2105/MARSA-AI-Logistics-.git
cd MARSA-AI-Logistics-
docker compose up --build
```

- Frontend → <http://localhost:3000>
- Gateway → <http://localhost:8000/docs>

The first build takes a few minutes: the backend image installs its
dependencies and then bakes the corpora, supply graph and risk models into
itself (~30s of that). Postgres comes up with pgvector, and the backend builds
the fast-path index on boot (~2s).

**What you get without API keys:** everything except the LLM. The router falls
back to a deterministic heuristic classifier, `/health` says so in plain text,
and every audit record carries a `classifier_not_llm` warning. All three paths
run and return real answers over the synthetic corpora.

To add a provider, put it in `.env` before `docker compose up`:

```bash
cp .env.example .env
# then set GEMINI_API_KEY=... (Groq and Cerebras also work)
```

### Running from source

Useful if you are changing code. Needs Python 3.11+, Node 22+, and Postgres 16
with pgvector.

**1. Database.** Either use the compose service on its own:

```bash
docker compose up -d postgres
```

…or point at any Postgres 16 that has the `vector` extension available. Neon's
free tier does.

**2. Backend.**

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

export DATABASE_URL=postgresql://marsa:marsa@127.0.0.1:5432/marsa

marsa-ingest fixtures      # synthetic corpora, seeded and deterministic
marsa-ml train             # late-delivery risk model
marsa-ml congestion        # port congestion tiers
marsa-graph build          # supply graph
marsa-index build          # chunk, embed, write to pgvector, build BM25

uvicorn marsa.api.main:app --reload
```

Check it: `curl localhost:8000/health`. The `resources` block should show
`fastPathIndex: true` and `supplyGraph: true`.

**3. Frontend.** In another terminal:

```bash
cd frontend
npm ci
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 npm run dev
```

Open <http://localhost:3000>. The query console should show a green
**"Live — routed by the FastAPI gateway over SSE"** banner. If it says
*"unreachable — replaying local fixtures"* instead, the frontend could not
reach the gateway — see the troubleshooting note below, which is the mistake
almost everyone makes first.

### Verifying the whole thing

```bash
cd backend
pytest                     # 340 tests
ruff check src tests
marsa-eval run             # full harness → RESULTS.md

cd ../frontend
npm run lint && npx tsc --noEmit && npm run build
```

### Troubleshooting: "unreachable — replaying local fixtures"

The console degrades to fixtures whenever the gateway does not answer its
health probe within 2.5 seconds. Three causes, in order of likelihood:

1. **You opened `127.0.0.1:3000` but the allowlist has `localhost:3000`** (or
   vice versa). To a browser these are *different origins*, and the request is
   blocked before it is sent — so the gateway logs nothing and looks fine. Both
   spellings are allowed by default now; if you set `CORS_ALLOWED_ORIGINS`
   yourself, include both.
2. **`NEXT_PUBLIC_API_BASE_URL` was set at run time instead of build time.** It
   is inlined into the client bundle by `next build`, not read from the
   environment by the running server. Setting it on `npm start` does nothing —
   rebuild.
3. The gateway genuinely is not running. `curl localhost:8000/health`.

---

## Part 2 — Deploying

Frontend on **Vercel**, gateway on **Render**, database on **Neon**. All three
have free tiers that need no card. Total cost: nothing.

> **Order matters.** The gateway needs the frontend's origin for CORS, and the
> frontend needs the gateway's URL baked into its bundle — so each needs a value
> the other produces. Step 4 resolves the loop; do not skip it.

### 1. Database — Neon

1. Create a project at <https://neon.tech>. Choose Postgres 16.
2. In the SQL editor: `CREATE EXTENSION IF NOT EXISTS vector;`
3. Copy the **pooled** connection string. It looks like
   `postgresql://user:pass@ep-xxx-pooler.region.aws.neon.tech/neondb?sslmode=require`.

Neon rather than Render's own Postgres because Render's free database has no
pgvector and expires after 30 days.

### 2. Gateway — Render

1. <https://dashboard.render.com> → **New** → **Blueprint** → select this repo.
   Render reads `render.yaml` and configures the service itself.
2. It will prompt for the values marked `sync: false`:
   - `DATABASE_URL` — the Neon string from step 1.
   - `CORS_ALLOWED_ORIGINS` — leave a placeholder for now; step 4 fixes it.
   - `GEMINI_API_KEY` / `GROQ_API_KEY` — optional, see below.
3. Deploy. First build takes ~5 minutes.

Check it: `curl https://<your-service>.onrender.com/health`.

**The free plan sleeps after 15 minutes of inactivity.** The first request
after that takes 30–60 seconds while the container cold-starts and rebuilds its
index. The frontend's health probe times out at 2.5s, so a sleeping gateway
shows as fixture mode until it wakes. Hit `/health` directly once to wake it.

`MARSA_REQUIRE_PGVECTOR=true` is set in the blueprint on purpose: if the Neon
DSN is wrong, the index build silently falls back to an in-process numpy store
and reports success. That would give you a green service that never touches
pgvector and loses every vector on restart. With the flag set, it refuses to
start instead — a failed deploy you can see beats a working one that lies.

### 3. Frontend — Vercel

1. <https://vercel.com/new> → import this repo.
2. **Set Root Directory to `frontend`.** This is the recommended setup. If you
   leave it at the repo root the deploy still works — the root `vercel.json`
   builds the frontend workspace — but Root Directory is the cleaner path and
   makes the build logs much easier to read.
3. Environment variable:
   `NEXT_PUBLIC_API_BASE_URL = https://<your-service>.onrender.com`
   — no trailing slash.
4. Deploy.

#### If the Vercel build fails

**"No Next.js version detected"** — Vercel is building from the repo root and
did not find a `package.json` there. There genuinely isn't one; this is a
monorepo. Either set Root Directory to `frontend`, or confirm the root
`vercel.json` is present on the branch you are deploying (it supplies
`installCommand`, `buildCommand` and `outputDirectory` for exactly this case).

**"Invalid vercel.json — should NOT have additional property …"** — Vercel
validates `vercel.json` against a strict schema and rejects any key it does not
recognise, including comment keys. Only documented properties belong in that
file; explanations go in this document instead.

**The build succeeds but the deployment 404s or serves an unstyled page** —
check that `output: "standalone"` is not being applied. `next.config.ts` gates
it on `process.env.VERCEL`, because standalone is for the Docker image and
Vercel builds through its own Build Output API. If you remove that gate, Vercel
emits an artifact nothing serves.

**Environment variable changes appear to do nothing** — `NEXT_PUBLIC_*` is
inlined at build time. Changing it in the dashboard requires a redeploy before
it takes effect. This is the single most common confusion in this project.

### 4. Close the loop

Two settings each depend on the other side's URL, so they can only be set now:

1. In Render, set `CORS_ALLOWED_ORIGINS` to your exact Vercel origin —
   `https://your-app.vercel.app`, no trailing slash, no wildcard. Save; Render
   redeploys.
2. In Vercel, confirm `NEXT_PUBLIC_API_BASE_URL` points at Render, then
   **redeploy**. This is not optional: the value is compiled into the client
   bundle, so changing it in the dashboard does nothing until a new build runs.

Open the Vercel URL. Green "Live" banner means both directions are wired.

**Preview deployments will show fixture mode.** Vercel gives every preview a
unique domain, and CORS is an exact-match allowlist. That is the allowlist
working correctly, not a bug. Add specific preview origins if you need them.

### 5. Container images (optional)

`.github/workflows/images.yml` publishes both images to GHCR after CI passes:

```
ghcr.io/krish2105/marsa-backend:<branch>
ghcr.io/krish2105/marsa-frontend:<branch>
```

No secrets to configure — it uses the built-in `GITHUB_TOKEN`.

**The frontend leg fails until you set `NEXT_PUBLIC_API_BASE_URL`** as a
repository variable (Settings → Secrets and variables → Actions → Variables).
That is deliberate. The value is compiled into the client bundle, so an image
built without it is permanently pointed at `localhost` and runs in fixture mode
wherever it is deployed — showing "unreachable — replaying local fixtures" with
nothing to suggest the image is at fault. A build that stops is much cheaper to
diagnose than an image that lies, so the workflow refuses to publish one.

The backend leg is unaffected and still publishes; only the frontend is gated.

The same check rejects two values that look right and fail invisibly:

| Value | Why it is refused |
|---|---|
| `https://gw.onrender.com/` | The trailing slash makes requests `//health`, which most servers treat as a different path. It 404s, the console decides the gateway is down. |
| `http://gw.onrender.com` | Vercel serves the page over https, so the browser blocks an http gateway as mixed content *before* sending anything — indistinguishable from the gateway being offline. |

So set it once, after step 2, to your exact Render origin with no trailing
slash: `https://<your-service>.onrender.com`.

---

## What is deployed, and what it proves

**The pipeline is real. The results are not yet.** `RESULTS.md` ships stamped
`PROVISIONAL` with three blockers listed above its first number: the classifier
is the heuristic rather than the specified few-shot LLM, the corpora are
synthetic fixtures rather than CBP CROSS / UN Comtrade / DataCo / World Bank,
and there is no LLM judge. That is the evaluation harness refusing to publish
figures it is not entitled to claim.

Deploying does not change that. To get a `FINAL` report you need an API key and
the Phase A ingestors run against live sources from somewhere their egress is
permitted. Then re-run `marsa-eval run`. No code changes are required.

---

## Environment variables

| Variable | Where | Required | Notes |
|---|---|---|---|
| `DATABASE_URL` | backend | yes in prod | Postgres 16 with pgvector. Without it, an in-process numpy store is used and vectors die on restart. |
| `NEXT_PUBLIC_API_BASE_URL` | frontend | yes | **Build time only.** Baked into the client bundle. |
| `CORS_ALLOWED_ORIGINS` | backend | yes in prod | Exact origins, comma-separated. Never `*`. |
| `MARSA_REQUIRE_PGVECTOR` | backend | no | `true` refuses to start on a silent numpy fallback. Set in `render.yaml`. |
| `MARSA_BOOTSTRAP_INDEX` | backend | no | `auto` (default) / `never`. Controls the boot-time index build. |
| `GEMINI_API_KEY` | backend | no | Enables the real few-shot classifier. Groq and Cerebras also supported. |
| `EMBEDDING_BACKEND` | backend | no | `hashed` (default in the image) or `local`. `local` needs the `ml` extra and pulls torch. |
| `DATA_DIR` | backend | no | Set to `/app/data` in the image; the default only resolves correctly from a source checkout. |
| `RATE_LIMIT_PER_MINUTE` | backend | no | Default 60, per IP. |
