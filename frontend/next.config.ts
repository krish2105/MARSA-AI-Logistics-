import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emits `.next/standalone/server.js` with only the traced dependencies, so
  // the runtime image does not carry node_modules. Vercel ignores this and uses
  // its own build output; it exists for the container path (Render, Fly, plain
  // Docker), which is why it is unconditional rather than env-gated.
  //
  // `server.js` does NOT serve `public/` or `.next/static/` on its own — the
  // Dockerfile copies both in explicitly. Miss that and the app boots, returns
  // 200, and renders unstyled: every asset 404s while the HTML is fine.
  output: "standalone",

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
