import type { NextConfig } from "next";

// Vercel sets VERCEL=1 during its builds. Everywhere else — local, Docker, CI —
// it is unset.
const onVercel = process.env.VERCEL === "1";

const nextConfig: NextConfig = {
  // `standalone` emits .next/standalone/server.js with only the traced
  // dependencies, which is what the Docker image runs so the runtime layer
  // carries no node_modules.
  //
  // It is deliberately NOT set on Vercel. Vercel builds through its own Build
  // Output API and does not run `server.js`; asking for standalone there makes
  // it emit an artifact nothing consumes and is a known way to break the
  // deployment. Self-hosting needs it, Vercel does not, so it is gated rather
  // than unconditional.
  //
  // When it IS set, `server.js` serves neither `public/` nor `.next/static/` on
  // its own — the Dockerfile copies both in. Miss that and the app boots,
  // returns 200, and renders unstyled: every asset 404s while the HTML is fine.
  ...(onVercel ? {} : { output: "standalone" as const }),

  // Fail the production build on a type error rather than shipping it. This is
  // the default, stated explicitly because a deploy pipeline is exactly where
  // someone is tempted to switch it off under time pressure.
  //
  // There is deliberately no `eslint` key here: Next 16 removed it and no
  // longer runs ESLint during `next build`. Linting is the separate `npm run
  // lint` step, which CI runs on its own — see .github/workflows/ci.yml.
  typescript: { ignoreBuildErrors: false },
};

export default nextConfig;
