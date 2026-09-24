import React, { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { apiClient } from '../api/client'
import { useGeolocation } from '../hooks/useGeolocation'
import { DealCard } from '../components/DealCard'
import type { Deal } from '../types'

export const Home: React.FC = () => {
  const navigate = useNavigate()
  const { position } = useGeolocation()
  const [query, setQuery] = useState('')
  const [deals, setDeals] = useState<Deal[]>([])
  const [loading, setLoading] = useState(true)
  const [category, setCategory] = useState('')
  const [categories, setCategories] = useState<string[]>([])

  useEffect(() => {
    apiClient.get<string[]>('/api/v1/products/categories')
      .then(({ data }) => setCategories(data || []))
      .catch(() => setCategories([]))
  }, [])

  useEffect(() => {
    let active = true
    const params: Record<string, string | number> = { limit: 8 }
    if (position) {
      params.lat = position.lat
      params.lon = position.lon
      params.radius_km = 50
    }
    if (category) params.category = category
    setLoading(true)
    apiClient.get<Deal[]>('/api/v1/deals/feed', { params })
      .then(({ data }) => { if (active) setDeals(data || []) })
      .catch(() => { if (active) setDeals([]) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [position?.lat, position?.lon, category])

  function submitSearch(event: React.FormEvent) {
    event.preventDefault()
    const q = query.trim()
    navigate(q ? `/products?q=${encodeURIComponent(q)}` : '/products')
  }

  return (
    <div className="df-home">
      <section className="df-intro">
        <div className="df-intro-inner">
          <div className="df-intro-copy">
            <span className="df-eyebrow">THE SMARTER GROCERY SHOP</span>
            <h1>Find a better price for <em>what’s on your list.</em></h1>
            <p>Search groceries across local stores, compare the offers, and save the ones worth watching.</p>
            <form className="df-search" onSubmit={submitSearch} role="search">
              <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="7" /><path d="m20 20-4-4" /></svg>
              <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Try milk, coffee or pasta" aria-label="Search groceries" />
              <button type="submit">Compare prices <span aria-hidden="true">→</span></button>
            </form>
            <div className="df-quick-links">
              <Link to="/deals">Explore today’s deals <span aria-hidden="true">↗</span></Link>
              <Link to="/stores">{position?.label ?? 'Find stores near you'} <span aria-hidden="true">↗</span></Link>
            </div>
          </div>
          <div className="df-intro-art" aria-hidden="true">
            <div className="df-art-orbit" />
            <img src="/images/hero-groceries.png" alt="" />
            <div className="df-art-note"><span>SHOP SMARTER</span><strong>Good food.<br />Better value.</strong></div>
          </div>
        </div>
      </section>

      <section className="df-deals-section" aria-labelledby="deals-title">
        <div className="df-section-heading">
          <div>
            <span className="df-eyebrow">YOUR NEXT FIND</span>
            <h2 id="deals-title">{position ? 'Deals around you' : 'Deals to explore'}</h2>
            <p>See what’s on offer, then compare the price before you buy.</p>
          </div>
          <Link className="df-view-all" to="/deals">View all deals <span aria-hidden="true">→</span></Link>
        </div>
        {categories.length > 0 && (
          <div className="df-categories" aria-label="Filter deals by category">
            <button className={!category ? 'is-active' : ''} onClick={() => setCategory('')} aria-pressed={!category}>All deals</button>
            {categories.map(c => <button key={c} className={category === c ? 'is-active' : ''} onClick={() => setCategory(c)} aria-pressed={category === c}>{c}</button>)}
          </div>
        )}
        {loading ? (
          <div className="df-grid" aria-label="Loading deals">{Array.from({ length: 4 }, (_, i) => <div className="df-loading-card" key={i} />)}</div>
        ) : deals.length ? (
          <div className="df-grid">{deals.map(deal => <DealCard key={deal.id} deal={deal} viewMode="comfortable" />)}</div>
        ) : (
          <div className="df-empty">
            <strong>{category ? `No ${category} deals right now` : 'No deals available right now'}</strong>
            <p>Try another category or search the full product range.</p>
            <Link to="/products">Search products →</Link>
          </div>
        )}
      </section>
    </div>
  )
}
