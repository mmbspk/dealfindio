import React, { useState, useEffect, useRef } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { apiClient } from '../api/client'
import { useGeolocation } from '../hooks/useGeolocation'
import { DealCard } from '../components/DealCard'
import type { Deal } from '../types'

// ── Benefit tile ──────────────────────────────────────────────────────────────
const Benefit: React.FC<{ icon: React.ReactNode; title: string; text: string }> = ({ icon, title, text }) => (
  <div className="flex items-start gap-3">
    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-emerald-100/70 text-[#059669]">
      {icon}
    </div>
    <div>
      <div className="text-sm font-semibold text-[#0F172A]">{title}</div>
      <div className="mt-0.5 text-sm text-[#94A3B8]">{text}</div>
    </div>
  </div>
)

// ── Hero right column ─────────────────────────────────────────────────────────
const HeroVisual: React.FC = () => (
  <div className="relative flex min-h-[390px] items-center justify-center overflow-visible lg:min-h-[520px] lg:justify-end">

    {/* Background blob — centred behind the bag */}
    <div className="absolute right-[5%] top-[8%] h-[320px] w-[320px] rounded-full bg-emerald-200/30 blur-[2px] lg:h-[390px] lg:w-[390px]" />

    {/* Grocery bag — the visual anchor */}
    <img
      src="/images/hero-groceries.png"
      alt="Fresh groceries"
      className="relative z-10 w-[250px] object-contain drop-shadow-[0_24px_40px_rgba(15,23,42,0.12)] sm:w-[300px] md:w-[340px] lg:w-[420px] xl:w-[460px] pointer-events-none select-none"
    />

    {/* ── Callout 1: "Compare / Save / Shop smarter"
         Left of the bag, arrow curves right toward the bag. ── */}
    <div
      className="absolute z-20 hidden -rotate-6 lg:block pointer-events-none"
      style={{ left: '6%', top: '22%' }}
    >
      <div className="text-[24px] leading-[0.95] text-[#059669] xl:text-[27px]" style={{ fontFamily: '"Caveat", cursive' }}>
        <div>Compare</div>
        <div>Save</div>
        <div>Shop smarter</div>
      </div>
      {/* Arrow curves right → toward bag centre */}
      <svg className="mt-1 ml-3" width="64" height="36" viewBox="0 0 64 36" fill="none">
        <path d="M4 6 C 18 2, 46 14, 60 30" stroke="#059669" strokeWidth="2" strokeLinecap="round" />
        <path d="M52 24 L60 30 L52 34" stroke="#059669" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </div>

    {/* ── Floating info card — upper-right of bag (xl+) ── */}
    <div
      className="absolute z-20 hidden rounded-2xl bg-white px-5 py-4 shadow-[0_12px_32px_rgba(15,23,42,0.08)] xl:block pointer-events-none"
      style={{ right: '0%', top: '12%' }}
    >
      <div className="flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-emerald-50">
          <svg className="h-5 w-5 text-[#059669]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M3 17l5-5 4 4 7-8" />
            <path d="M14 8h5v5" />
          </svg>
        </div>
        <div>
          <div className="font-semibold text-[#0F172A]">Real savings in your area</div>
          <div className="mt-0.5 text-sm text-[#64748B]">Same products. Lower prices.</div>
        </div>
      </div>
    </div>

    {/* ── Callout 2: "Better deals / for a brighter / tomorrow"
         Right of bag, clear of the bag itself. Arrow curves left → toward bag. ── */}
    <div
      className="absolute z-20 hidden rotate-[-3deg] xl:block pointer-events-none"
      style={{ right: '-1%', bottom: '16%' }}
    >
      {/* Arrow first: points left toward the bag, sits above text */}
      <svg className="mb-1 ml-auto" width="64" height="34" viewBox="0 0 64 34" fill="none">
        <path d="M60 6 C 44 4, 16 16, 4 28" stroke="#059669" strokeWidth="2" strokeLinecap="round" />
        <path d="M10 22 L4 28 L12 30" stroke="#059669" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <div className="text-[22px] leading-[1.05] text-[#059669] text-right" style={{ fontFamily: '"Caveat", cursive' }}>
        <div>Better deals</div>
        <div>for a brighter</div>
        <div>tomorrow</div>
      </div>
    </div>

  </div>
)

export const Home: React.FC = () => {
  const { position } = useGeolocation()
  const navigate = useNavigate()
  const chipsRef = useRef<HTMLDivElement>(null)
  const [query, setQuery] = useState('')
  const [deals, setDeals] = useState<Deal[]>([])
  const [dealsLoading, setDealsLoading] = useState(false)
  const [categories, setCategories] = useState<string[]>([])
  const [activeCategory, setActiveCategory] = useState('')

  useEffect(() => {
    apiClient
      .get<string[]>('/api/v1/products/categories')
      .then((r) => setCategories(r.data || []))
      .catch(() => setCategories([]))
  }, [])

  useEffect(() => {
    const params: Record<string, unknown> = { limit: 8 }
    if (position) {
      params.lat = position.lat
      params.lon = position.lon
      params.radius_km = 50
    }
    if (activeCategory) params.category = activeCategory
    setDealsLoading(true)
    apiClient
      .get<Deal[]>('/api/v1/deals/feed', { params })
      .then((r) => setDeals(r.data))
      .catch(() => setDeals([]))
      .finally(() => setDealsLoading(false))
  }, [position?.lat, position?.lon, activeCategory])

  function handleSearch(e: React.FormEvent) {
    e.preventDefault()
    const q = query.trim()
    navigate(q ? `/products?q=${encodeURIComponent(q)}` : '/products')
  }

  return (
    <div className="min-h-screen bg-white font-sans">

      {/* ── Hero ─────────────────────────────────────────────────────────── */}
      <section className="bg-[#F0FDF4] border-b border-gray-100 overflow-hidden">
        <div className="mx-auto grid max-w-[1280px] grid-cols-1 gap-10 px-5 py-14 md:px-8 lg:grid-cols-[1.08fr_0.92fr] lg:items-center lg:gap-12 lg:px-10 lg:py-20">

          {/* ── Left ── */}
          <div className="relative z-10">

            <p className="mb-5 text-xs font-semibold uppercase tracking-[0.22em] text-[#059669]">
              Australian grocery deals
            </p>

            <h1 className="max-w-[680px] text-[42px] font-bold leading-[1.02] tracking-[-0.04em] text-[#0F172A] sm:text-[52px] lg:text-[66px]">
              Compare grocery prices{' '}
              <span className="text-[#059669]">with confidence.</span>
            </h1>

            <p className="mt-6 max-w-[620px] text-[17px] leading-8 text-[#64748B] lg:text-[19px]">
              Compare live prices across Coles, Woolworths, ALDI and more — find the best deal near you in seconds.
            </p>

            {/* Search */}
            <form onSubmit={handleSearch} className="mt-8 flex max-w-[650px] items-center gap-2 rounded-2xl border border-[#E5E7EB] bg-white p-2 shadow-[0_8px_30px_rgba(15,23,42,0.06)]">
              <div className="flex min-w-0 flex-1 items-center gap-3 px-3">
                <svg className="h-5 w-5 shrink-0 text-slate-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="11" cy="11" r="7" />
                  <path d="m20 20-3.6-3.6" />
                </svg>
                <input
                  type="text"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search products... (e.g., milk, bread, pasta)"
                  className="min-w-0 flex-1 bg-transparent py-3 text-[15px] text-[#0F172A] outline-none placeholder:text-slate-400"
                  aria-label="Search products"
                />
              </div>
              <button
                type="submit"
                className="shrink-0 rounded-xl bg-[#F59E0B] px-6 py-3.5 text-sm font-semibold text-[#0F172A] transition hover:bg-[#D97706] cursor-pointer"
              >
                Search
              </button>
            </form>

            {/* Actions */}
            <div className="mt-4 flex flex-wrap gap-3">
              <Link
                to="/deals"
                className="inline-flex min-h-[48px] items-center gap-2 rounded-xl bg-[#059669] px-5 py-3 text-sm font-semibold text-white transition hover:bg-[#047857] cursor-pointer"
              >
                <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M20.6 13.4 11 23l-8-8 9.6-9.6a2 2 0 0 1 2.8 0l5.2 5.2a2 2 0 0 1 0 2.8Z" />
                  <circle cx="14" cy="10" r="1" />
                </svg>
                Browse all deals
              </Link>

              <Link
                to="/stores"
                className="inline-flex min-h-[48px] items-center gap-2 rounded-xl border border-[#E5E7EB] bg-white px-5 py-3 text-sm font-medium text-[#475569] shadow-sm hover:border-emerald-400 hover:text-emerald-700 transition cursor-pointer"
              >
                <svg className="h-4 w-4 text-[#059669]" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M20 10c0 5-8 12-8 12S4 15 4 10a8 8 0 1 1 16 0Z" />
                  <circle cx="12" cy="10" r="2" />
                </svg>
                {position?.label ?? 'Set location'}
                <svg className="h-3.5 w-3.5 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                </svg>
              </Link>
            </div>

            {/* Benefits */}
            <div className="mt-10 grid max-w-[720px] grid-cols-1 gap-5 sm:grid-cols-3">
              <Benefit
                icon={<svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M20.6 13.4 11 23l-8-8 9.6-9.6a2 2 0 0 1 2.8 0l5.2 5.2a2 2 0 0 1 0 2.8Z" /><circle cx="14" cy="10" r="1" /></svg>}
                title="Real prices"
                text="from your local area"
              />
              <Benefit
                icon={<svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>}
                title="Save time"
                text="no more leaflet hunting"
              />
              <Benefit
                icon={<svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M20 4C12 4 6 8 6 14c0 3 2 5 5 5 6 0 9-7 9-15Z" /><path d="M4 20c3-5 7-8 12-10" /></svg>}
                title="A fairer tomorrow"
                text="less waste, more value"
              />
            </div>

            {/* Mobile grocery image */}
            <div className="mt-10 flex justify-center lg:hidden">
              <img
                src="/images/hero-groceries.png"
                alt="Fresh groceries"
                className="object-contain"
                style={{ height: 240, width: 'auto', maxWidth: '100%' }}
              />
            </div>
          </div>

          {/* ── Right ── */}
          <HeroVisual />

        </div>
      </section>

      {/* ── Retailer trust strip ──────────────────────────────────────────── */}
      <section className="bg-white border-b border-gray-100" style={{ minHeight: 88 }}>
        <div className="max-w-[1280px] mx-auto px-6 sm:px-8 lg:px-10">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 py-5">
            <div className="flex flex-wrap items-center gap-x-8 gap-y-2">
              <span className="text-[12px] text-gray-400 font-medium flex-shrink-0">Prices from</span>
              {[
                { name: 'Coles',        colour: '#E31F26', chain: 'coles' },
                { name: 'Woolworths',   colour: '#0B8B3D', chain: 'woolworths' },
                { name: 'ALDI',         colour: '#0072C6', chain: 'aldi' },
                { name: 'IGA',          colour: '#D0021B', chain: 'iga' },
                { name: "Dan Murphy's", colour: '#1B3A2D', chain: 'dan_murphys' },
                { name: 'Drakes',       colour: '#CA8A04', chain: 'drakes' },
              ].map(({ name, colour, chain }) => (
                <Link
                  key={name}
                  to={`/deals?chain=${chain}`}
                  className="text-[15px] font-bold tracking-tight transition-opacity hover:opacity-70 cursor-pointer"
                  style={{ color: colour }}
                >
                  {name}
                </Link>
              ))}
            </div>
            <div className="flex items-center gap-2.5 flex-shrink-0">
              <svg className="w-5 h-5 text-emerald-600 flex-shrink-0" fill="currentColor" viewBox="0 0 24 24">
                <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z" />
              </svg>
              <div>
                <p className="text-[14px] font-bold text-[#0f172a] leading-tight">Real prices. Real savings.</p>
                <p className="text-[12px] text-gray-400">From your local area.</p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── Deals section ────────────────────────────────────────────────── */}
      <section className="bg-[#f8fafc]" style={{ paddingTop: 64, paddingBottom: 80 }}>
        <div className="max-w-[1280px] mx-auto px-6 sm:px-8 lg:px-10">

          <div className="flex items-end justify-between mb-5 gap-4">
            <div>
              <h2 className="font-bold text-[#0f172a]" style={{ fontSize: 'clamp(22px, 3vw, 30px)', letterSpacing: '-0.01em' }}>
                Deals near you
              </h2>
              {categories.length > 0 && (
                <p className="text-[13px] text-gray-400 mt-1">
                  Across {categories.length} categories · updated daily
                </p>
              )}
            </div>
            <Link to="/deals" className="text-emerald-600 hover:text-emerald-700 text-[14px] font-semibold whitespace-nowrap flex items-center gap-1 cursor-pointer transition-colors">
              See all deals
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
              </svg>
            </Link>
          </div>

          {/* Category chips */}
          {categories.length > 0 && (
            <div className="relative mb-8">
              <div ref={chipsRef} className="flex items-center gap-2 overflow-x-auto pb-1 scrollbar-hide">
                <button
                  onClick={() => setActiveCategory('')}
                  className={`flex-shrink-0 text-[13px] font-semibold transition-colors cursor-pointer ${activeCategory === '' ? 'bg-emerald-600 text-white' : 'bg-white text-gray-600 border border-gray-200 hover:border-emerald-400 hover:text-emerald-600'}`}
                  style={{ height: 34, paddingLeft: 14, paddingRight: 14, borderRadius: 20 }}
                >
                  All
                </button>
                {categories.map((cat) => (
                  <button
                    key={cat}
                    onClick={() => setActiveCategory(cat)}
                    className={`flex-shrink-0 text-[13px] font-semibold transition-colors cursor-pointer ${activeCategory === cat ? 'bg-emerald-600 text-white' : 'bg-white text-gray-600 border border-gray-200 hover:border-emerald-400 hover:text-emerald-600'}`}
                    style={{ height: 34, paddingLeft: 14, paddingRight: 14, borderRadius: 20 }}
                  >
                    {cat}
                  </button>
                ))}
              </div>
              <button
                onClick={() => chipsRef.current?.scrollBy({ left: 200, behavior: 'smooth' })}
                className="absolute right-0 top-0 bottom-1 w-12 flex items-center justify-end pr-1 cursor-pointer"
                style={{ background: 'linear-gradient(to left, #f8fafc 40%, transparent)' }}
                aria-label="Scroll categories"
              >
                <span className="flex h-6 w-6 items-center justify-center rounded-full bg-white shadow-sm border border-gray-200">
                  <svg className="w-3.5 h-3.5 text-gray-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M9 5l7 7-7 7" />
                  </svg>
                </span>
              </button>
            </div>
          )}

          {dealsLoading ? (
            <div className="flex justify-center py-20">
              <svg className="w-9 h-9 text-emerald-600 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            </div>
          ) : deals.length === 0 ? (
            <div className="text-center py-20 text-gray-400">
              <p className="mb-3 text-[15px]">{activeCategory ? `No deals in ${activeCategory} right now.` : 'No deals available yet.'}</p>
              {activeCategory && (
                <button onClick={() => setActiveCategory('')} className="text-emerald-600 hover:underline text-[14px] font-semibold cursor-pointer">
                  Clear filter
                </button>
              )}
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
              {deals.map((deal) => (
                <DealCard key={deal.id} deal={deal} viewMode="comfortable" />
              ))}
            </div>
          )}
        </div>
      </section>

    </div>
  )
}
