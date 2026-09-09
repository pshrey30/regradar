import { Link } from 'react-router-dom'

import { Badge } from '../components/Badge'
import { RadarScene } from '../components/RadarScene'

const WATCHED_SOURCES = [
  { label: 'SEC', description: 'EDGAR full-text search & filings feed' },
  { label: 'FDA', description: 'Adverse event & recall notices' },
  { label: 'FINRA', description: 'Disciplinary & rulemaking notices' },
]

export function Landing() {
  return (
    <div className="bg-ink text-slate-100">
      <nav className="mx-auto flex max-w-6xl items-center justify-between px-6 py-6 sm:px-10">
        <span className="font-mono text-sm font-medium tracking-[0.2em] text-slate-100">
          REGRADAR
        </span>
        <Link
          to="/login"
          className="rounded-md border border-slate-700 px-4 py-2 text-sm font-medium text-slate-100 transition-colors hover:border-slate-500 hover:bg-slate-900"
        >
          Sign in
        </Link>
      </nav>

      <header className="relative mx-auto grid max-w-6xl grid-cols-1 items-center gap-8 px-6 pb-16 pt-8 sm:px-10 lg:grid-cols-[1.1fr_1fr] lg:gap-4 lg:pb-24 lg:pt-16">
        <div className="relative z-10">
          <p className="mb-4 font-mono text-xs tracking-[0.3em] text-primary-400">
            REGULATORY FILING INTELLIGENCE
          </p>
          <h1 className="font-mono text-4xl font-semibold leading-[1.05] tracking-tight text-white sm:text-5xl lg:text-6xl">
            Every filing,
            <br />
            the moment it lands.
          </h1>
          <p className="mt-6 max-w-md text-base leading-relaxed text-slate-400 sm:text-lg">
            RegRadar watches SEC, FDA, and FINRA filings as they publish, triages what actually
            matters, and briefs the people who need to know — before it reaches your inbox any
            other way.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-4">
            <Link
              to="/login"
              className="rounded-md bg-primary-600 px-6 py-3 text-sm font-semibold text-white transition-colors hover:bg-primary-700"
            >
              Get started
            </Link>
            <span className="font-mono text-xs text-slate-500">No credit card. No web search — local, grounded answers only.</span>
          </div>
        </div>

        <div className="relative -mx-6 h-[380px] sm:mx-0 sm:h-[460px] lg:h-[560px]">
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_center,_rgba(79,70,229,0.18),_transparent_65%)]" />
          <RadarScene />
        </div>
      </header>

      <section className="border-t border-slate-800 bg-ink-panel">
        <div className="mx-auto max-w-6xl px-6 py-14 sm:px-10">
          <p className="mb-8 font-mono text-xs tracking-[0.3em] text-slate-500">WHAT IT WATCHES</p>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            {WATCHED_SOURCES.map((source) => (
              <div
                key={source.label}
                className="rounded-lg border border-slate-800 bg-ink p-5 transition-colors hover:border-slate-700"
              >
                <p className="font-mono text-lg font-semibold text-white">{source.label}</p>
                <p className="mt-2 text-sm text-slate-400">{source.description}</p>
              </div>
            ))}
          </div>

          <div className="mt-10 rounded-lg border border-slate-800 bg-ink p-5 sm:p-6">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <p className="font-mono text-xs tracking-[0.2em] text-slate-500">SAMPLE DETECTION</p>
              <div className="flex items-center gap-2">
                <Badge variant="domain" value="financial" />
                <Badge variant="risk" value="critical" />
              </div>
            </div>
            <p className="text-sm font-medium text-white">Acme Financial Corp — 10-K</p>
            <p className="mt-1 text-sm text-slate-400">
              Flagged for material weakness in internal controls, seconds after publication —
              routed to the analyst on call with the obligation and deadline already extracted.
            </p>
          </div>
        </div>
      </section>

      <footer className="border-t border-slate-800">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-6 py-8 text-sm text-slate-500 sm:flex-row sm:px-10">
          <span>© {new Date().getFullYear()} RegRadar</span>
          <Link to="/login" className="font-mono text-slate-400 hover:text-slate-200">
            Sign in →
          </Link>
        </div>
      </footer>
    </div>
  )
}
