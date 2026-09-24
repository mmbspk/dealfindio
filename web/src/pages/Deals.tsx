import React, { useState, useEffect, useCallback, useRef } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { apiClient } from '../api/client'
import { useGeolocation } from '../hooks/useGeolocation'
import { DealCard } from '../components/DealCard'
import type { Deal } from '../types'

const PAGE_SIZE = 24

const KNOWN_CHAINS = [
  { value: 'coles', label: 'Coles' },
  { value: 'woolworths', label: 'Woolworths' },
  { value: 'aldi', label: 'ALDI' },
  { value: 'iga', label: 'IGA' },
  { value: 'dan_murphys', label: "Dan Murphy's" },
  { value: 'drakes', label: 'Drakes' },
]

type SortMode = 'discount' | 'price'
type ViewMode = 'compact' | 'comfortable' | 'list'

export const Deals: React.FC = () => {
  const { position } = useGeolocation()
  const [searchParams] = useSearchParams()
  const [deals, setDeals] = useState<Deal[]>([])
  const [categories, setCategories] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState('')
  const [hasMore, setHasMore] = useState(true)
  const [offset, setOffset] = useState(0)
  const [sortMode, setSortMode] = useState<SortMode>('discount')
  const [viewMode, setViewMode] = useState<ViewMode>('list')

  // Filters — initialise chain from ?chain= query param if present
  const [selectedChain, setSelectedChain] = useState(() => searchParams.get('chain') || '')
  const [selectedCategory, setSelectedCategory] = useState('')

  const lastFetch = useRef({ chain: '\0', category: '\0', offset: -1 })

  useEffect(() => {
    apiClient.get('/api/v1/products/categories').then(r => {
      setCategories(r.data || [])
    }).catch(() => {})
  }, [])

  const fetchDeals = useCallback(async (
    chain: string, category: string, page: number, append: boolean
  ) => {
    const params: Record<string, string | number> = { limit: PAGE_SIZE, offset: page }
    if (chain) params.chain = chain
    if (category) params.category = category
    if (position) {
      params.lat = position.lat
      params.lon = position.lon
      params.radius_km = 50
    }
    try {
      const r = await apiClient.get<Deal[]>('/api/v1/deals/feed', { params })
      const items = r.data || []
      if (append) {
        setDeals(prev => [...prev, ...items])
      } else {
        setDeals(items)
      }
      setHasMore(items.length === PAGE_SIZE)
      setError('')
    } catch {
      setError('Failed to load deals. Please try again.')
    }
  }, [position?.lat, position?.lon]) // eslint-disable-line react-hooks/exhaustive-deps

  // Sync selectedChain from URL when navigating to this page (e.g. from Stores)
  useEffect(() => {
    const chainParam = searchParams.get('chain') || ''
    setSelectedChain(chainParam)
  }, [searchParams])

  // Reset + reload when filters or position change
  useEffect(() => {
    const lf = lastFetch.current
    if (lf.chain === selectedChain && lf.category === selectedCategory && lf.offset === 0) return
    lastFetch.current = { chain: selectedChain, category: selectedCategory, offset: 0 }
    setOffset(0)
    setLoading(true)
    fetchDeals(selectedChain, selectedCategory, 0, false).finally(() => setLoading(false))
  }, [selectedChain, selectedCategory, fetchDeals])

  const loadMore = useCallback(async () => {
    if (loadingMore || !hasMore) return
    const nextOffset = offset + PAGE_SIZE
    setLoadingMore(true)
    lastFetch.current = { chain: selectedChain, category: selectedCategory, offset: nextOffset }
    await fetchDeals(selectedChain, selectedCategory, nextOffset, true)
    setOffset(nextOffset)
    setLoadingMore(false)
  }, [loadingMore, hasMore, offset, selectedChain, selectedCategory, fetchDeals])

  const clearFilters = () => {
    setSelectedChain('')
    setSelectedCategory('')
  }

  const hasActiveFilters = selectedChain || selectedCategory

  const sortedDeals = [...deals].sort((a, b) => {
    if (sortMode === 'price') return a.price_aud - b.price_aud
    return (b.discount_pct ?? 0) - (a.discount_pct ?? 0)
  })

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Filter bar */}
      <div className="bg-white border-b sticky top-[72px] z-40 shadow-sm">
        <div className="max-w-[1280px] mx-auto px-6 sm:px-8 lg:px-10 py-3">
          <div className="flex flex-wrap gap-3 items-center">
            {/* Chain filter */}
            <select
              value={selectedChain}
              onChange={e => setSelectedChain(e.target.value)}
              className="py-2 pl-3 pr-8 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-400 bg-white cursor-pointer"
            >
              <option value="">All Stores</option>
              {KNOWN_CHAINS.map(c => (
                <option key={c.value} value={c.value}>{c.label}</option>
              ))}
            </select>

            {/* Category filter */}
            <select
              value={selectedCategory}
              onChange={e => setSelectedCategory(e.target.value)}
              className="py-2 pl-3 pr-8 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-400 bg-white cursor-pointer max-w-[220px]"
            >
              <option value="">All Categories</option>
              {categories.map(cat => (
                <option key={cat} value={cat}>{cat}</option>
              ))}
            </select>

            {/* Sort toggle */}
            <div className="flex items-center gap-1 bg-gray-100 rounded-lg p-1">
              <button
                onClick={() => setSortMode('discount')}
                className={`px-3 py-1 text-xs font-medium rounded-md transition cursor-pointer ${
                  sortMode === 'discount'
                    ? 'bg-white text-[#176f50] shadow-sm'
                    : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                Best discount
              </button>
              <button
                onClick={() => setSortMode('price')}
                className={`px-3 py-1 text-xs font-medium rounded-md transition cursor-pointer ${
                  sortMode === 'price'
                    ? 'bg-white text-[#176f50] shadow-sm'
                    : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                Lowest price
              </button>
            </div>

            {hasActiveFilters && (
              <button
                onClick={clearFilters}
                className="text-sm text-gray-500 hover:text-red-500 transition flex items-center gap-1 cursor-pointer"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
                Clear
              </button>
            )}

            {!loading && (
              <span className="text-sm text-gray-500 ml-auto">
                {deals.length}{hasMore ? '+' : ''} deals
              </span>
            )}

            {/* Location indicator / change link */}
            {position ? (
              <Link
                to="/stores"
                className="flex items-center gap-1.5 text-xs text-[#176f50] bg-emerald-50 px-2.5 py-1.5 rounded-full border border-emerald-100 hover:bg-emerald-100 transition whitespace-nowrap"
                title="Change location"
              >
                <svg className="w-3 h-3 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M5.05 4.05a7 7 0 119.9 9.9L10 18.9l-4.95-4.95a7 7 0 010-9.9zM10 11a2 2 0 100-4 2 2 0 000 4z" clipRule="evenodd"/>
                </svg>
                {position.label ?? 'Location active'}
              </Link>
            ) : (
              <Link
                to="/stores"
                className="flex items-center gap-1.5 text-xs text-gray-500 bg-gray-50 px-2.5 py-1.5 rounded-full border border-gray-200 hover:border-[#a8cdb1] hover:text-[#176f50] transition whitespace-nowrap"
              >
                <svg className="w-3 h-3 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"/>
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"/>
                </svg>
                Set location
              </Link>
            )}

            {/* View toggle */}
            <div className="flex items-center gap-1 bg-gray-100 rounded-lg p-1">
              <button
                onClick={() => setViewMode('compact')}
                title="Compact"
                className={`p-1.5 rounded cursor-pointer transition ${viewMode === 'compact' ? 'bg-white shadow-sm text-[#176f50]' : 'text-gray-400 hover:text-gray-600'}`}
              >
                <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 16 16">
                  <rect x="0" y="0" width="4" height="4" rx="0.5" />
                  <rect x="5" y="0" width="4" height="4" rx="0.5" />
                  <rect x="10" y="0" width="4" height="4" rx="0.5" />
                  <rect x="0" y="5" width="4" height="4" rx="0.5" />
                  <rect x="5" y="5" width="4" height="4" rx="0.5" />
                  <rect x="10" y="5" width="4" height="4" rx="0.5" />
                  <rect x="0" y="10" width="4" height="4" rx="0.5" />
                  <rect x="5" y="10" width="4" height="4" rx="0.5" />
                  <rect x="10" y="10" width="4" height="4" rx="0.5" />
                </svg>
              </button>
              <button
                onClick={() => setViewMode('comfortable')}
                title="Comfortable"
                className={`p-1.5 rounded cursor-pointer transition ${viewMode === 'comfortable' ? 'bg-white shadow-sm text-[#176f50]' : 'text-gray-400 hover:text-gray-600'}`}
              >
                <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 16 16">
                  <rect x="0" y="0" width="7" height="7" rx="0.5" />
                  <rect x="9" y="0" width="7" height="7" rx="0.5" />
                  <rect x="0" y="9" width="7" height="7" rx="0.5" />
                  <rect x="9" y="9" width="7" height="7" rx="0.5" />
                </svg>
              </button>
              <button
                onClick={() => setViewMode('list')}
                title="List"
                className={`p-1.5 rounded cursor-pointer transition ${viewMode === 'list' ? 'bg-white shadow-sm text-[#176f50]' : 'text-gray-400 hover:text-gray-600'}`}
              >
                <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 16 16">
                  <rect x="0" y="1" width="16" height="3" rx="0.5" />
                  <rect x="0" y="6" width="16" height="3" rx="0.5" />
                  <rect x="0" y="11" width="16" height="3" rx="0.5" />
                </svg>
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="max-w-[1280px] mx-auto px-6 sm:px-8 lg:px-10 py-6">
        {/* Page header */}
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-gray-900">
            {position ? 'Deals Near You' : 'All Current Deals'}
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            On-sale products from your local stores, updated weekly
          </p>
        </div>

        {error && (
          <div className="mb-4 bg-red-50 border-l-4 border-red-400 p-3 rounded text-sm text-red-700">
            {error}
          </div>
        )}

        {loading ? (
          <div className={viewMode === 'list'
            ? 'flex flex-col gap-4'
            : viewMode === 'comfortable'
            ? 'grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-5'
            : 'grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4'
          }>
            {Array.from({ length: 12 }).map((_, i) => (
              viewMode === 'list' ? (
                <div key={i} className="bg-white rounded-2xl p-4 animate-pulse flex gap-4">
                  <div className="w-20 h-20 bg-gray-100 rounded-lg flex-shrink-0" />
                  <div className="flex-1 space-y-2 pt-1">
                    <div className="h-3 bg-gray-100 rounded w-3/4" />
                    <div className="h-3 bg-gray-100 rounded w-1/2" />
                    <div className="h-3 bg-gray-100 rounded w-1/3" />
                  </div>
                </div>
              ) : (
                <div key={i} className="bg-white rounded-2xl p-3 animate-pulse">
                  <div className="aspect-square bg-gray-100 rounded-lg mb-3" />
                  <div className="h-3 bg-gray-100 rounded mb-2 w-3/4" />
                  <div className="h-3 bg-gray-100 rounded w-1/2" />
                </div>
              )
            ))}
          </div>
        ) : sortedDeals.length === 0 ? (
          <div className="text-center py-24 text-gray-500">
            <svg className="w-12 h-12 mx-auto mb-4 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z" />
            </svg>
            <p className="font-medium">No deals found</p>
            <p className="text-sm mt-1 text-gray-400">
              {selectedChain
                ? `${KNOWN_CHAINS.find(c => c.value === selectedChain)?.label ?? selectedChain} has no active specials right now — check back later.`
                : 'Try clearing your filters or checking back later.'}
            </p>
            {hasActiveFilters && (
              <button onClick={clearFilters} className="mt-3 text-[#176f50] text-sm hover:underline cursor-pointer">
                Show all chains
              </button>
            )}
          </div>
        ) : (
          <>
            <div className={viewMode === 'list'
              ? 'flex flex-col gap-4'
              : viewMode === 'comfortable'
              ? 'grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-5'
              : 'grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4'
            }>
              {sortedDeals.map(deal => (
                <DealCard key={deal.id} deal={deal} viewMode={viewMode} />
              ))}
            </div>

            {hasMore && (
              <div className="mt-8 text-center">
                <button
                  onClick={loadMore}
                  disabled={loadingMore}
                  className="px-6 py-2.5 bg-lime-300 text-gray-900 rounded-lg font-medium text-sm hover:bg-lime-400 disabled:opacity-60 transition cursor-pointer"
                >
                  {loadingMore ? 'Loading...' : 'Load more deals'}
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
