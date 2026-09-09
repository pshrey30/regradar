import { Link } from 'react-router-dom'

import { Badge } from '../components/Badge'
import { RadarScene } from '../components/RadarScene'

const WATCHED_SOURCES = [
  {
    label: 'SEC',
    title: 'EDGAR filings',
    description:
      "Full-text search and the submissions feed, polled continuously — 10-Ks, 10-Qs, 8-Ks, and amendments as they're indexed.",
  },
  {
    label: 'FDA',
    title: 'Adverse events & recalls',
    description:
      'Recall notices and adverse event reports the moment they publish, before they reach a compliance mailing list.',
  },
  {
    label: 'FINRA',
    title: 'Disciplinary notices',
    description: 'Disciplinary actions and rulemaking notices, tracked alongside everything else in one place.',
  },
] as const

const PIPELINE_STEPS = [
  {
    step: '01',
    title: 'Ingest',
    description: 'A filing is detected the moment it publishes and its source PDF is pulled and stored.',
  },
  {
    step: '02',
    title: 'Triage',
    description:
      'Classified by domain and risk level in seconds — critical filings route to deeper analysis immediately.',
  },
  {
    step: '03',
    title: 'Analyze & brief',
    description:
      'Obligations, deadlines, and risk flags extracted with a citation back to the source page — then summarized for each role.',
  },
  {
    step: '04',
    title: 'Deliver',
    description: 'Sent where your team already works — Slack, email, or a signed webhook — no manual triage required.',
  },
] as const

const ROLES = [
  {
    name: 'Analyst',
    focus: 'Obligations & deadlines',
    description: 'A structured list of what has to happen and by when, not a wall of extracted JSON.',
  },
  {
    name: 'Legal Counsel',
    focus: 'Precedent search',
    description: 'Ask what the SEC has said about a topic before and get a grounded answer with citations.',
  },
  {
    name: 'Compliance Officer',
    focus: 'Board-level risk',
    description: "What happened, why it matters, and the risk level — in under fifty words, for the people who don't read filings.",
  },
  {
    name: 'Engineering Lead',
    focus: 'Pipeline health',
    description: 'Faithfulness, latency, and cost-per-filing against target, so quality regressions get caught before customers do.',
  },
] as const

export function Landing() {
  return (
    <div className="bg-white text-slate-900">
      <nav className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5 sm:px-10">
          <span className="font-mono text-sm font-semibold tracking-[0.2em] text-slate-900">
            REGRADAR
          </span>
          <Link
            to="/login"
            className="rounded-md bg-primary-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-primary-700"
          >
            Sign in
          </Link>
        </div>
      </nav>

      <header className="relative mx-auto grid max-w-6xl grid-cols-1 items-center gap-8 px-6 pb-16 pt-14 sm:px-10 lg:grid-cols-[1.1fr_1fr] lg:gap-4 lg:pb-24 lg:pt-20">
        <div className="relative z-10">
          <p className="mb-4 font-mono text-xs tracking-[0.3em] text-primary-600">
            REGULATORY FILING INTELLIGENCE
          </p>
          <h1 className="font-mono text-4xl font-semibold leading-[1.05] tracking-tight text-slate-900 sm:text-5xl lg:text-6xl">
            Every filing,
            <br />
            the moment it lands.
          </h1>
          <p className="mt-6 max-w-md text-base leading-relaxed text-slate-600 sm:text-lg">
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
            <span className="font-mono text-xs text-slate-500">
              No credit card. No web search — local, grounded answers only.
            </span>
          </div>
        </div>

        <div className="relative -mx-6 h-[380px] sm:mx-0 sm:h-[460px] lg:h-[560px]">
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_center,_rgba(79,70,229,0.10),_transparent_65%)]" />
          <RadarScene />
        </div>
      </header>

      <section className="border-t border-slate-200 bg-slate-50">
        <div className="mx-auto max-w-6xl px-6 py-16 sm:px-10">
          <p className="mb-2 font-mono text-xs tracking-[0.3em] text-slate-500">HOW IT WORKS</p>
          <h2 className="mb-10 text-2xl font-semibold text-slate-900 sm:text-3xl">
            From publication to inbox in under three minutes.
          </h2>
          <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">
            {PIPELINE_STEPS.map((item) => (
              <div key={item.step} className="rounded-lg border border-slate-200 bg-white p-5">
                <p className="font-mono text-sm text-primary-600">{item.step}</p>
                <p className="mt-2 text-base font-semibold text-slate-900">{item.title}</p>
                <p className="mt-2 text-sm leading-relaxed text-slate-600">{item.description}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="border-t border-slate-200">
        <div className="mx-auto max-w-6xl px-6 py-16 sm:px-10">
          <p className="mb-2 font-mono text-xs tracking-[0.3em] text-slate-500">WHAT IT WATCHES</p>
          <h2 className="mb-10 text-2xl font-semibold text-slate-900 sm:text-3xl">
            Three regulators, one queue.
          </h2>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            {WATCHED_SOURCES.map((source) => (
              <div
                key={source.label}
                className="rounded-lg border border-slate-200 bg-white p-5 transition-colors hover:border-slate-300"
              >
                <p className="font-mono text-lg font-semibold text-slate-900">{source.label}</p>
                <p className="mt-2 text-sm font-medium text-slate-700">{source.title}</p>
                <p className="mt-1 text-sm text-slate-600">{source.description}</p>
              </div>
            ))}
          </div>

          <div className="mt-6 rounded-lg border border-slate-200 bg-white p-5 sm:p-6">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <p className="font-mono text-xs tracking-[0.2em] text-slate-500">SAMPLE DETECTION</p>
              <div className="flex items-center gap-2">
                <Badge variant="domain" value="financial" />
                <Badge variant="risk" value="critical" />
              </div>
            </div>
            <p className="text-sm font-medium text-slate-900">Acme Financial Corp — 10-K</p>
            <p className="mt-1 text-sm text-slate-600">
              Flagged for material weakness in internal controls, seconds after publication —
              routed to the analyst on call with the obligation and deadline already extracted.
            </p>
          </div>
        </div>
      </section>

      <section className="border-t border-slate-200 bg-slate-50">
        <div className="mx-auto max-w-6xl px-6 py-16 sm:px-10">
          <p className="mb-2 font-mono text-xs tracking-[0.3em] text-slate-500">BUILT FOR EVERY ROLE</p>
          <h2 className="mb-10 text-2xl font-semibold text-slate-900 sm:text-3xl">
            The same filing, framed differently for each reader.
          </h2>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {ROLES.map((role) => (
              <div key={role.name} className="rounded-lg border border-slate-200 bg-white p-5">
                <p className="text-base font-semibold text-slate-900">{role.name}</p>
                <p className="mt-1 font-mono text-xs text-primary-600">{role.focus}</p>
                <p className="mt-3 text-sm leading-relaxed text-slate-600">{role.description}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="border-t border-slate-200">
        <div className="mx-auto max-w-6xl px-6 py-16 sm:px-10">
          <div className="grid grid-cols-1 items-start gap-8 lg:grid-cols-[1fr_1fr]">
            <div>
              <p className="mb-2 font-mono text-xs tracking-[0.3em] text-slate-500">
                ASK REGRADAR
              </p>
              <h2 className="mb-4 text-2xl font-semibold text-slate-900 sm:text-3xl">
                Grounded answers, not a web search.
              </h2>
              <p className="text-base leading-relaxed text-slate-600">
                Ask a question about past filings and get an answer synthesized strictly from your
                own ingested filings — never the open web, never invented facts. If the excerpts
                don&rsquo;t cover it, RegRadar says so instead of guessing.
              </p>
            </div>
            <div className="rounded-lg border border-slate-200 bg-white p-5 sm:p-6">
              <p className="font-mono text-xs text-slate-500">You asked</p>
              <p className="mt-1 text-sm font-medium text-slate-900">
                What has the SEC said about late 10-K filings?
              </p>
              <div className="mt-4 border-t border-slate-100 pt-4">
                <p className="font-mono text-xs text-slate-500">RegRadar</p>
                <p className="mt-1 text-sm text-slate-600">
                  There is no information provided about the SEC&rsquo;s views on late 10-K
                  filings in the retrieved excerpts.
                </p>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="border-t border-slate-200 bg-primary-600">
        <div className="mx-auto flex max-w-6xl flex-col items-start justify-between gap-6 px-6 py-14 sm:flex-row sm:items-center sm:px-10">
          <div>
            <h2 className="text-2xl font-semibold text-white sm:text-3xl">
              Stop finding out about filings from someone else.
            </h2>
            <p className="mt-2 text-sm text-primary-100">Sign in and see what&rsquo;s landed so far.</p>
          </div>
          <Link
            to="/login"
            className="shrink-0 rounded-md bg-white px-6 py-3 text-sm font-semibold text-primary-700 transition-colors hover:bg-primary-50"
          >
            Get started
          </Link>
        </div>
      </section>

      <footer className="border-t border-slate-200">
        <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-6 py-8 text-sm sm:flex-row sm:px-10">
          <span className="font-mono text-xs tracking-wide text-slate-500">
            © {new Date().getFullYear()} REGRADAR
          </span>
          <Link to="/login" className="font-mono text-xs text-slate-600 hover:text-slate-900">
            Sign in →
          </Link>
        </div>
      </footer>
    </div>
  )
}
