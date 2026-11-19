import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { apiClient } from '../api/client'

const STORES = [
  { name: 'Woolworths', logo: '/logos/woolworths.svg', bg: '#007837' },
  { name: 'Coles',      logo: '/logos/coles.svg',      bg: '#E31837' },
  { name: 'ALDI',       logo: '/logos/aldi.svg',       bg: '#00529F' },
  { name: 'IGA',        logo: '/logos/iga.svg',        bg: '#F47920' },
  { name: 'Drakes',     logo: '/logos/drakes.svg',     bg: '#1A1A2E' },
]

const HOW_IT_WORKS = [
  {
    step: '01',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-6 h-6">
        <circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" />
      </svg>
    ),
    title: 'Search any product',
    body: 'Type a product name — milk, coffee, pasta, whatever you need.',
  },
  {
    step: '02',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-6 h-6">
        <path d="M3 3h18M3 9h18M3 15h18M3 21h18" />
        <rect x="9" y="3" width="6" height="18" rx="1" fill="currentColor" opacity="0.15" />
      </svg>
    ),
    title: 'Compare prices across chains',
    body: 'See live prices from Woolworths, Coles, ALDI, IGA and Drakes side by side.',
  },
  {
    step: '03',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-6 h-6">
        <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />
      </svg>
    ),
    title: 'Save the best deal',
    body: 'Add to your watchlist and get notified when prices drop.',
  },
]

const FEATURES = [
  {
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-7 h-7">
        <path d="M12 2L2 7l10 5 10-5-10-5z" />
        <path d="M2 17l10 5 10-5" />
        <path d="M2 12l10 5 10-5" />
      </svg>
    ),
    title: 'Price comparison',
    body: 'Compare the same product across every major Australian supermarket chain in one tap.',
    badge: null,
  },
  {
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-7 h-7">
        <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
      </svg>
    ),
    title: 'Price history & trend alerts',
    body: 'Track price trends over time and get notified the moment a watchlisted product drops.',
    badge: null,
  },
  {
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-7 h-7">
        <path d="M9 11l3 3L22 4" />
        <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
      </svg>
    ),
    title: 'Dietary & allergen filters',
    body: 'Filter products by dietary needs — gluten-free, vegan, nut-free and more.',
    badge: 'Coming soon',
  },
]

export const Landing: React.FC = () => {
  const [submitted, setSubmitted] = useState(false)
  const [email, setEmail] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleNotify(e: React.FormEvent) {
    e.preventDefault()
    const val = email.trim()
    if (!val) return
    setSubmitting(true)
    setError(null)
    try {
      await apiClient.post('/api/v1/waitlist/subscribe', { email: val })
      setSubmitted(true)
    } catch {
      setError('Something went wrong — please try again.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="bg-white text-gray-900 overflow-x-hidden">

      {/* ── Nav ── */}
      <nav className="sticky top-0 z-50 bg-white/80 backdrop-blur border-b border-gray-100">
        <div className="max-w-6xl mx-auto px-5 sm:px-8 h-14 flex items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <svg className="w-5 h-5 text-emerald-500" viewBox="0 0 24 24" fill="currentColor">
              <path d="M17 8C8 10 5.9 16.17 3.82 19.14L5.71 21l1-1c.5.5 1 1 1.71 1C13 21 20 15 21 7c-1.5.5-3 .5-4 1z" />
            </svg>
            <span className="text-[15px] font-bold tracking-tight text-gray-900">dealfindio</span>
          </div>
          <Link
            to="/demo"
            className="hidden sm:inline-flex items-center gap-1.5 bg-emerald-500 hover:bg-emerald-600 text-white text-sm font-semibold px-4 py-2 rounded-lg transition-colors"
          >
            Try the demo
            <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4"><path fillRule="evenodd" d="M3 10a.75.75 0 01.75-.75h10.638L10.23 5.29a.75.75 0 111.04-1.08l5.5 5.25a.75.75 0 010 1.08l-5.5 5.25a.75.75 0 11-1.04-1.08l4.158-3.96H3.75A.75.75 0 013 10z" clipRule="evenodd"/></svg>
          </Link>
        </div>
      </nav>

      {/* ── Hero ── */}
      <section className="relative overflow-hidden bg-gradient-to-br from-emerald-50 via-white to-amber-50">
        {/* Background decoration */}
        <div aria-hidden="true" className="absolute inset-0 overflow-hidden pointer-events-none">
          <div className="absolute -top-32 -right-32 w-96 h-96 rounded-full bg-emerald-100/60 blur-3xl" />
          <div className="absolute -bottom-20 -left-20 w-72 h-72 rounded-full bg-amber-100/50 blur-3xl" />
        </div>

        <div className="relative max-w-6xl mx-auto px-5 sm:px-8 pt-16 pb-20 sm:pt-24 sm:pb-28 flex flex-col items-center text-center gap-8">
          {/* Eyebrow */}
          <span className="inline-flex items-center gap-2 bg-emerald-100 text-emerald-700 text-xs font-semibold px-3 py-1.5 rounded-full uppercase tracking-wide">
            <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="currentColor"><path d="M17 8C8 10 5.9 16.17 3.82 19.14L5.71 21l1-1c.5.5 1 1 1.71 1C13 21 20 15 21 7c-1.5.5-3 .5-4 1z"/></svg>
            Australia's grocery price comparison app
          </span>

          {/* Headline */}
          <h1 className="text-4xl sm:text-5xl lg:text-6xl font-extrabold text-gray-900 leading-[1.1] max-w-3xl">
            Stop overpaying at
            <span className="text-emerald-500"> the supermarket.</span>
          </h1>

          {/* Subheadline */}
          <p className="text-lg sm:text-xl text-gray-500 max-w-xl leading-relaxed">
            Compare grocery prices across Woolworths, Coles, ALDI, IGA&nbsp;&amp;&nbsp;more.
            In seconds, on your phone.
          </p>

          {/* CTAs */}
          <div className="flex flex-col sm:flex-row gap-3 w-full sm:w-auto">
            <Link
              to="/demo"
              className="inline-flex items-center justify-center gap-2 bg-emerald-500 hover:bg-emerald-600 text-white text-base font-bold px-8 py-4 rounded-xl transition-colors w-full sm:w-auto min-h-[52px]"
            >
              Try the demo
              <svg viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4"><path fillRule="evenodd" d="M3 10a.75.75 0 01.75-.75h10.638L10.23 5.29a.75.75 0 111.04-1.08l5.5 5.25a.75.75 0 010 1.08l-5.5 5.25a.75.75 0 11-1.04-1.08l4.158-3.96H3.75A.75.75 0 013 10z" clipRule="evenodd"/></svg>
            </Link>
            <a
              href="#notify"
              className="inline-flex items-center justify-center gap-2 bg-white border border-gray-200 text-gray-700 hover:border-emerald-400 hover:text-emerald-600 text-base font-semibold px-8 py-4 rounded-xl transition-colors w-full sm:w-auto min-h-[52px]"
            >
              Notify me at launch
            </a>
          </div>

          {/* Hero image */}
          <div className="mt-4 w-full max-w-md mx-auto">
            <div className="relative rounded-2xl overflow-hidden shadow-xl border border-gray-100">
              <img
                src="/images/hero-groceries.png"
                alt="Grocery basket with fresh produce"
                className="w-full h-48 sm:h-64 object-cover"
                loading="eager"
              />
              {/* Floating price badge */}
              <div className="absolute bottom-4 left-4 bg-white rounded-xl shadow-lg px-4 py-2.5 flex items-center gap-3">
                <div className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                <div>
                  <p className="text-[11px] text-gray-400 font-medium uppercase tracking-wide">Best price</p>
                  <p className="text-[15px] font-bold text-gray-900">Best price guaranteed</p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── Store logos ── */}
      <section aria-label="Supported supermarkets" className="bg-gray-50 border-y border-gray-100 py-8">
        <div className="max-w-6xl mx-auto px-5 sm:px-8">
          <p className="text-center text-xs font-semibold text-gray-400 uppercase tracking-widest mb-6">
            Prices from Australia's major supermarkets
          </p>
          <div className="flex flex-wrap items-center justify-center gap-3 sm:gap-4">
            {STORES.map(({ name, logo }) => (
              <div
                key={name}
                className="h-11 px-5 flex items-center justify-center rounded-xl bg-white border border-gray-150 shadow-sm hover:shadow-md transition-shadow"
                style={{ minWidth: 100 }}
              >
                <img src={logo} alt={name} className="h-7 w-auto object-contain" loading="lazy" />
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── How it works ── */}
      <section className="max-w-6xl mx-auto px-5 sm:px-8 py-20 sm:py-28" aria-labelledby="how-title">
        <div className="text-center mb-14">
          <span className="text-xs font-semibold text-emerald-600 uppercase tracking-widest">Simple by design</span>
          <h2 id="how-title" className="mt-2 text-3xl sm:text-4xl font-extrabold text-gray-900">
            Three steps. Zero confusion.
          </h2>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-8">
          {HOW_IT_WORKS.map(({ step, icon, title, body }) => (
            <div key={step} className="flex flex-col items-center text-center gap-4">
              <div className="relative">
                <div className="w-14 h-14 rounded-2xl bg-emerald-50 flex items-center justify-center text-emerald-600">
                  {icon}
                </div>
                <span className="absolute -top-2 -right-2 w-5 h-5 rounded-full bg-amber-400 text-gray-900 text-[10px] font-bold flex items-center justify-center leading-none">
                  {step.slice(1)}
                </span>
              </div>
              <h3 className="text-[17px] font-bold text-gray-900">{title}</h3>
              <p className="text-[14px] text-gray-500 leading-relaxed max-w-[240px]">{body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ── Feature cards ── */}
      <section className="bg-gray-50 border-y border-gray-100 py-20 sm:py-28" aria-labelledby="features-title">
        <div className="max-w-6xl mx-auto px-5 sm:px-8">
          <div className="text-center mb-14">
            <span className="text-xs font-semibold text-emerald-600 uppercase tracking-widest">Built for your pocket</span>
            <h2 id="features-title" className="mt-2 text-3xl sm:text-4xl font-extrabold text-gray-900">
              Everything a smart shopper needs.
            </h2>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-6">
            {FEATURES.map(({ icon, title, body, badge }) => (
              <div
                key={title}
                className="bg-white rounded-2xl border border-gray-100 shadow-sm p-7 flex flex-col gap-4 hover:shadow-md transition-shadow"
              >
                <div className="w-12 h-12 rounded-xl bg-emerald-50 flex items-center justify-center text-emerald-600">
                  {icon}
                </div>
                <div className="flex items-start gap-2">
                  <h3 className="text-[16px] font-bold text-gray-900 leading-snug">{title}</h3>
                  {badge && (
                    <span className="flex-shrink-0 mt-0.5 text-[10px] font-semibold bg-amber-100 text-amber-700 px-2 py-0.5 rounded-full">
                      {badge}
                    </span>
                  )}
                </div>
                <p className="text-[14px] text-gray-500 leading-relaxed">{body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── App store placeholders ── */}
      <section className="max-w-6xl mx-auto px-5 sm:px-8 py-20 sm:py-28 text-center" aria-labelledby="app-title">
        <span className="text-xs font-semibold text-emerald-600 uppercase tracking-widest">Mobile app</span>
        <h2 id="app-title" className="mt-2 text-3xl sm:text-4xl font-extrabold text-gray-900 mb-3">
          Coming to your phone soon.
        </h2>
        <p className="text-gray-500 text-[15px] max-w-sm mx-auto mb-10">
          The full dealfindio experience — camera receipt scanning, location-aware prices, push alerts — launching on iOS and Android.
        </p>
        <div className="flex flex-col sm:flex-row gap-4 justify-center">
          {/* App Store badge */}
          <div
            className="inline-flex items-center gap-3 bg-gray-900 text-white px-6 py-3.5 rounded-xl opacity-70 cursor-default select-none"
            aria-label="App Store — Coming Soon"
          >
            <svg viewBox="0 0 24 24" fill="currentColor" className="w-6 h-6 flex-shrink-0">
              <path d="M18.71 19.5c-.83 1.24-1.71 2.45-3.05 2.47-1.34.03-1.77-.79-3.29-.79-1.53 0-2 .77-3.27.82-1.31.05-2.3-1.32-3.14-2.53C4.25 17 2.94 12.45 4.7 9.39c.87-1.52 2.43-2.48 4.12-2.51 1.28-.02 2.5.87 3.29.87.78 0 2.26-1.07 3.8-.91.65.03 2.47.26 3.64 1.98-.09.06-2.17 1.28-2.15 3.81.03 3.02 2.65 4.03 2.68 4.04-.03.07-.42 1.44-1.38 2.83M13 3.5c.73-.83 1.94-1.46 2.94-1.5.13 1.17-.34 2.35-1.04 3.19-.69.85-1.83 1.51-2.95 1.42-.15-1.15.41-2.35 1.05-3.11z"/>
            </svg>
            <div className="text-left">
              <p className="text-[10px] leading-none opacity-70 mb-0.5">Coming soon on</p>
              <p className="text-[15px] font-semibold leading-tight">App Store</p>
            </div>
          </div>
          {/* Google Play badge */}
          <div
            className="inline-flex items-center gap-3 bg-gray-900 text-white px-6 py-3.5 rounded-xl opacity-70 cursor-default select-none"
            aria-label="Google Play — Coming Soon"
          >
            <svg viewBox="0 0 24 24" fill="currentColor" className="w-6 h-6 flex-shrink-0">
              <path d="M3.18 23.76c.37.2.8.2 1.17 0L13.5 18 9.5 14 3.18 23.76zm17.44-11.53L17.5 10.5l-3.41 3.41 3.41 3.41 3.12-1.73a1.75 1.75 0 000-3.36zM3 .24L14.5 12 9.5 17 3.18.24A1 1 0 003 .24zM13.5 6L4.35.24c-.37-.2-.8-.2-1.17 0L9.5 7 13.5 6z"/>
            </svg>
            <div className="text-left">
              <p className="text-[10px] leading-none opacity-70 mb-0.5">Coming soon on</p>
              <p className="text-[15px] font-semibold leading-tight">Google Play</p>
            </div>
          </div>
        </div>
      </section>

      {/* ── Email capture ── */}
      <section
        id="notify"
        className="bg-emerald-600 py-16 sm:py-20"
        aria-labelledby="notify-title"
      >
        <div className="max-w-xl mx-auto px-5 sm:px-8 text-center">
          <h2 id="notify-title" className="text-2xl sm:text-3xl font-extrabold text-white mb-3">
            Be first to know when we launch.
          </h2>
          <p className="text-emerald-100 text-[15px] mb-8">
            Drop your email and we'll reach out the moment dealfindio is live.
          </p>
          {submitted ? (
            <div className="bg-emerald-500 rounded-xl px-6 py-5 flex items-center justify-center gap-3 text-white font-semibold text-[15px]">
              <svg viewBox="0 0 20 20" fill="currentColor" className="w-5 h-5 flex-shrink-0">
                <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.857-9.809a.75.75 0 00-1.214-.882l-3.483 4.79-1.88-1.88a.75.75 0 10-1.06 1.061l2.5 2.5a.75.75 0 001.137-.089l4-5.5z" clipRule="evenodd"/>
              </svg>
              You're on the list — we'll be in touch!
            </div>
          ) : (
            <form onSubmit={handleNotify} className="flex flex-col sm:flex-row gap-3">
              <input
                type="email"
                required
                value={email}
                onChange={e => setEmail(e.target.value)}
                placeholder="your@email.com"
                className="flex-1 px-5 py-4 rounded-xl text-gray-900 text-[15px] placeholder-gray-400 border border-transparent focus:outline-none focus:ring-2 focus:ring-white min-h-[52px]"
                aria-label="Email address"
                disabled={submitting}
              />
              <button
                type="submit"
                disabled={submitting}
                className="bg-amber-400 hover:bg-amber-300 disabled:opacity-60 text-gray-900 text-[15px] font-bold px-7 py-4 rounded-xl transition-colors whitespace-nowrap min-h-[52px]"
              >
                {submitting ? 'Sending…' : 'Notify me'}
              </button>
            </form>
          )}
          {error && (
            <p className="mt-3 text-red-300 text-[13px]">{error}</p>
          )}
          <p className="mt-4 text-emerald-200 text-[12px]">No spam. Unsubscribe any time.</p>
        </div>
      </section>

      {/* ── Footer ── */}
      <footer className="bg-[#0B1120] border-t border-slate-800/60">
        <div className="max-w-6xl mx-auto px-5 sm:px-8 py-10 flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <svg className="w-4 h-4 text-emerald-400" viewBox="0 0 24 24" fill="currentColor">
              <path d="M17 8C8 10 5.9 16.17 3.82 19.14L5.71 21l1-1c.5.5 1 1 1.71 1C13 21 20 15 21 7c-1.5.5-3 .5-4 1z"/>
            </svg>
            <span className="text-[13px] font-bold text-white tracking-tight">dealfindio</span>
            <span className="text-slate-600 text-[12px] ml-1">© {new Date().getFullYear()}</span>
          </div>
          <div className="flex items-center gap-5">
            <a href="/privacy" className="text-[12px] text-slate-500 hover:text-slate-300 transition-colors">Privacy Policy</a>
            <a href="/terms" className="text-[12px] text-slate-500 hover:text-slate-300 transition-colors">Terms of Use</a>
            <span className="text-[12px] text-slate-600">dealfindio.com.au</span>
          </div>
        </div>
      </footer>

    </div>
  )
}
