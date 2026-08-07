import { QueryConsole } from "@/components/route/query-console";
import { Hero } from "@/components/sections/hero";
import { PathBento } from "@/components/sections/path-bento";

export default function HomePage() {
  return (
    <>
      <Hero />

      <div id="console" className="mx-auto max-w-3xl px-4 py-16 sm:px-6">
        <QueryConsole />
      </div>

      <PathBento />
    </>
  );
}
