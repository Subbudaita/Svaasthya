import { PILLARS } from "@/lib/pillars";

// Today — Phase 0 shell. Empty by design: nothing is logged, nothing is scored.
export default function TodayPage() {
  return (
    <main className="mx-auto flex max-w-md flex-col gap-6 px-5 py-8">
      <header>
        <p className="text-sm text-muted">Today</p>
        <h1 className="text-2xl font-semibold tracking-tight">Swasthya</h1>
      </header>

      <section
        aria-label="Right now"
        className="rounded-2xl border border-line p-5"
      >
        <p className="text-sm text-muted">Right now</p>
        <p className="mt-1">Nothing yet. Your program begins after onboarding.</p>
      </section>

      <section aria-label="Pillars">
        <ul className="grid grid-cols-3 gap-4">
          {PILLARS.map((pillar) => (
            <li key={pillar.key} className="flex flex-col items-center gap-2">
              <span
                aria-hidden
                className="size-14 rounded-full border-4 border-line"
              />
              <span className="text-xs text-muted">{pillar.label}</span>
            </li>
          ))}
        </ul>
      </section>

      <section aria-label="Today's plates" className="text-sm text-muted">
        No plates logged today.
      </section>
    </main>
  );
}
