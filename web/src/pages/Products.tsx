import React, { useState, useEffect, useCallback, useRef } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { apiClient } from '../api/client'
import { Product } from '../types'
import { useGeolocation } from '../hooks/useGeolocation'
import { SaveToListButton } from '../components/SaveToListButton'

const PAGE_SIZE = 48

type ViewMode = 'compact' | 'comfortable' | 'list'
type SortOption = 'relevance' | 'price_asc' | 'price_desc' | 'discount' | 'name' | 'newest'

const SORT_OPTIONS: { value: SortOption; label: string }[] = [
  { value: 'relevance', label: 'Best Match' },
  { value: 'price_asc', label: 'Price: Low to High' },
  { value: 'price_desc', label: 'Price: High to Low' },
  { value: 'discount', label: 'Biggest Discount' },
  { value: 'name', label: 'Name A–Z' },
  { value: 'newest', label: 'Newest' },
]

const KNOWN_CHAINS = [
  { value: 'coles', label: 'Coles' },
  { value: 'woolworths', label: 'Woolworths' },
  { value: 'aldi', label: 'ALDI' },
  { value: 'iga', label: 'IGA' },
  { value: 'drakes', label: 'Drakes' },
]

export const Products: React.FC = () => {
  const { position } = useGeolocation()
  const [searchParams] = useSearchParams()
  const initialQuery = searchParams.get('q') ?? ''
  const [products, setProducts] = useState<Product[]>([])
  const [categories, setCategories] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState('')

  // Filters — search seeds from the ?q= param so the Home hero can deep-link here.
  const [search, setSearch] = useState(initialQuery)
  const [selectedCategory, setSelectedCategory] = useState('')
  const [selectedChain, setSelectedChain] = useState('')
  const [sort, setSort] = useState<SortOption>('relevance')

  // Pagination
  const [offset, setOffset] = useState(0)
  const [hasMore, setHasMore] = useState(true)
  const [viewMode, setViewMode] = useState<ViewMode>('comfortable')

  // Track what we last fetched so we don't double-fetch
  const lastFetch = useRef({ search: '', category: '', chain: '', sort: 'relevance', offset: -1 })

  // Fetch categories on mount
  useEffect(() => {
    apiClient.get('/api/v1/products/categories').then(r => {
      setCategories(r.data || [])
    }).catch(() => {})
  }, [])

  const fetchProducts = useCallback(async (
    q: string, category: string, chain: string, sortBy: SortOption, page: number, append: boolean
  ) => {
    const params: Record<string, string | number> = { limit: PAGE_SIZE, offset: page, sort: sortBy }
    if (q.trim()) params.q = q.trim()
    if (category) params.category = category
    if (chain) params.chain = chain

    try {
      const r = await apiClient.get('/api/v1/products/search', { params })
      const items: Product[] = r.data || []
      if (append) {
        setProducts(prev => [...prev, ...items])
      } else {
        setProducts(items)
      }
      setHasMore(items.length === PAGE_SIZE)
      setError('')
    } catch {
      setError('Failed to load products. Please try again.')
    }
  }, [])

  // Initial load + filter changes: reset to page 0
  useEffect(() => {
    const lf = lastFetch.current
    if (lf.search === search && lf.category === selectedCategory && lf.chain === selectedChain && lf.sort === sort && lf.offset === 0) return
    lastFetch.current = { search, category: selectedCategory, chain: selectedChain, sort, offset: 0 }

    setOffset(0)
    setLoading(true)
    fetchProducts(search, selectedCategory, selectedChain, sort, 0, false).finally(() => setLoading(false))
  }, [search, selectedCategory, selectedChain, sort, fetchProducts])

  const loadMore = useCallback(async () => {
    if (loadingMore || !hasMore) return
    const nextOffset = offset + PAGE_SIZE
    setLoadingMore(true)
    lastFetch.current = { search, category: selectedCategory, chain: selectedChain, sort, offset: nextOffset }
    await fetchProducts(search, selectedCategory, selectedChain, sort, nextOffset, true)
    setOffset(nextOffset)
    setLoadingMore(false)
  }, [loadingMore, hasMore, offset, search, selectedCategory, selectedChain, sort, fetchProducts])

  // Trigger initial fetch on mount (uses ?q= seed if present)
  useEffect(() => {
    setLoading(true)
    lastFetch.current = { search: initialQuery, category: '', chain: '', sort: 'relevance', offset: 0 }
    fetchProducts(initialQuery, '', '', 'relevance', 0, false).finally(() => setLoading(false))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setSearch(e.target.value)
  }

  const clearFilters = () => {
    setSearch('')
    setSelectedCategory('')
    setSelectedChain('')
    setSort('relevance')
  }

  const hasActiveFilters = search || selectedCategory || selectedChain || sort !== 'relevance'

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Filter bar */}
      <div className="bg-white border-b sticky top-[72px] z-40 shadow-sm">
        <div className="max-w-[1280px] mx-auto px-6 sm:px-8 lg:px-10 py-3">
          <div className="flex flex-wrap gap-3 items-center">
            {/* Search */}
            <div className="relative flex-1 min-w-[180px] max-w-xs">
              <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
              <input
                type="text"
                placeholder="Search products..."
                value={search}
                onChange={handleSearchChange}
                className="w-full pl-9 pr-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-400"
              />
            </div>

            {/* Store filter */}
            <select
              value={selectedChain}
              onChange={e => setSelectedChain(e.target.value)}
              className="py-2 pl-3 pr-8 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-400 bg-white"
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
              className="py-2 pl-3 pr-8 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-400 bg-white max-w-[220px]"
            >
              <option value="">All Categories</option>
              {categories.map(cat => (
                <option key={cat} value={cat}>{cat}</option>
              ))}
            </select>

            {/* Sort */}
            <select
              value={sort}
              onChange={e => setSort(e.target.value as SortOption)}
              className="py-2 pl-3 pr-8 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-400 bg-white"
            >
              {SORT_OPTIONS.map(o => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>

            {hasActiveFilters && (
              <button
                onClick={clearFilters}
                className="text-sm text-gray-500 hover:text-red-500 transition flex items-center gap-1"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
                Clear
              </button>
            )}

            {/* Result count */}
            {!loading && (
              <span className="text-sm text-gray-500 ml-auto">
                {products.length}{hasMore ? '+' : ''} products
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
              {/* Compact: 6-col small tiles */}
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
              {/* Comfortable: 4-col medium tiles */}
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
              {/* List: single column rows */}
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
        {error && (
          <div className="mb-4 bg-red-50 border-l-4 border-red-400 p-3 rounded text-sm text-red-700">
            {error}
          </div>
        )}

        {loading ? (
          <div className={viewMode === 'list'
            ? 'flex flex-col gap-3'
            : viewMode === 'comfortable'
            ? 'grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-4 xl:grid-cols-4 gap-5'
            : 'grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4'
          }>
            {Array.from({ length: 24 }).map((_, i) => (
              viewMode === 'list' ? (
                <div key={i} className="bg-white rounded-2xl p-4 animate-pulse flex gap-4">
                  <div className="w-16 h-16 bg-gray-100 rounded-lg flex-shrink-0" />
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
        ) : products.length === 0 ? (
          <div className="text-center py-24 text-gray-500">
            <svg className="w-12 h-12 mx-auto mb-4 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 3h2l.4 2M7 13h10l4-8H5.4M7 13L5.4 5M7 13l-2.293 2.293c-.63.63-.184 1.707.707 1.707H17m0 0a2 2 0 100 4 2 2 0 000-4zm-8 2a2 2 0 11-4 0 2 2 0 014 0z" />
            </svg>
            <p className="font-medium">No products found</p>
            {hasActiveFilters && (
              <button onClick={clearFilters} className="mt-2 text-[#176f50] text-sm hover:underline">
                Clear filters
              </button>
            )}
          </div>
        ) : (
          <>
            <div className={viewMode === 'list'
              ? 'flex flex-col gap-3'
              : viewMode === 'comfortable'
              ? 'grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-4 gap-5'
              : 'grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4'
            }>
              {products.map(product => (
                <ProductTile key={product.id} product={product} viewMode={viewMode} />
              ))}
            </div>

            {hasMore && (
              <div className="mt-8 text-center">
                <button
                  onClick={loadMore}
                  disabled={loadingMore}
                  className="px-6 py-2.5 bg-lime-300 text-gray-900 rounded-lg font-medium text-sm hover:bg-lime-400 disabled:opacity-60 transition"
                >
                  {loadingMore ? 'Loading...' : 'Load more'}
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function ProductTile({ product, viewMode }: { product: Product; viewMode: ViewMode }) {
  const [imgError, setImgError] = useState(false)
  const hasPrice = product.lowest_price_aud != null

  if (viewMode === 'list') {
    return (
      <Link
        to={`/product/${product.id}`}
        className="group bg-white rounded-2xl p-4 hover:shadow-md transition-shadow border border-gray-100 flex gap-4 items-center"
      >
        <div className="w-16 h-16 flex-shrink-0 bg-gray-50 rounded-lg overflow-hidden flex items-center justify-center">
          {product.image_url && !imgError ? (
            <img
              src={product.image_url}
              alt={product.name}
              className="w-full h-full object-contain group-hover:scale-105 transition-transform"
              onError={() => setImgError(true)}
              loading="lazy"
            />
          ) : (
            <svg className="w-8 h-8 text-gray-200" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
            </svg>
          )}
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-gray-900 group-hover:text-[#176f50] transition-colors line-clamp-1">
            {product.name}
          </p>
          <div className="flex items-center gap-2 mt-0.5">
            {product.brand && <span className="text-xs text-gray-400">{product.brand}</span>}
            {product.category && <span className="text-xs bg-emerald-50 text-[#176f50] rounded px-1.5 py-0.5">{product.category}</span>}
          </div>
        </div>
        <div className="flex-shrink-0 flex flex-col items-end gap-1">
          {hasPrice && (
            <div className="text-right">
              <span className={`text-base font-bold ${product.is_on_sale ? 'text-red-600' : 'text-gray-900'}`}>
                ${product.lowest_price_aud!.toFixed(2)}
              </span>
              {product.was_price_aud != null && (
                <div className="text-xs text-gray-400 line-through">${product.was_price_aud.toFixed(2)}</div>
              )}
            </div>
          )}
          <div className="flex items-center gap-1.5 mt-0.5">
            {product.is_on_sale && product.discount_pct != null && (
              <span className="bg-red-100 text-red-600 text-xs font-bold px-2 py-0.5 rounded whitespace-nowrap">
                -{Math.round(product.discount_pct)}%
              </span>
            )}
            <div onClick={e => { e.preventDefault(); e.stopPropagation() }}>
              <SaveToListButton productId={product.id} size="sm" />
            </div>
          </div>
        </div>
      </Link>
    )
  }

  // Grid modes (compact + comfortable)
  const imgSize = viewMode === 'comfortable' ? 'w-14 h-14' : 'w-10 h-10'

  return (
    <Link
      to={`/product/${product.id}`}
      className="relative group bg-white rounded-2xl p-3 hover:shadow-md transition-shadow border border-gray-100 flex flex-col"
    >
      <div
        className="absolute top-2 right-2 z-10"
        onClick={e => { e.preventDefault(); e.stopPropagation() }}
      >
        <SaveToListButton productId={product.id} size="sm" />
      </div>
      <div className="aspect-square bg-gray-50 rounded-lg mb-3 overflow-hidden flex items-center justify-center relative">
        {product.image_url && !imgError ? (
          <img
            src={product.image_url}
            alt={product.name}
            className="w-full h-full object-contain group-hover:scale-105 transition-transform"
            onError={() => setImgError(true)}
            loading="lazy"
          />
        ) : (
          <svg className={`${imgSize} text-gray-200`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
          </svg>
        )}
        {product.is_on_sale && product.discount_pct != null && (
          <span className="absolute top-1.5 left-1.5 bg-red-500 text-white text-[10px] font-bold px-1.5 py-0.5 rounded">
            -{Math.round(product.discount_pct)}%
          </span>
        )}
      </div>

      <div className="flex-1 min-w-0">
        <p className={`font-medium text-gray-900 leading-snug line-clamp-2 group-hover:text-[#176f50] transition-colors ${viewMode === 'comfortable' ? 'text-sm' : 'text-xs'}`}>
          {product.name}
        </p>
        {product.brand && (
          <p className="text-xs text-gray-400 mt-0.5 truncate">{product.brand}</p>
        )}

        {hasPrice ? (
          <div className="mt-1.5 flex items-baseline gap-1.5 flex-wrap">
            <span className={`font-bold ${product.is_on_sale ? 'text-red-600' : 'text-gray-900'} ${viewMode === 'comfortable' ? 'text-base' : 'text-sm'}`}>
              ${product.lowest_price_aud!.toFixed(2)}
            </span>
            {product.was_price_aud != null && (
              <span className="text-xs text-gray-400 line-through">
                ${product.was_price_aud.toFixed(2)}
              </span>
            )}
          </div>
        ) : (
          product.category && (
            <span className="inline-block mt-1.5 text-[10px] bg-emerald-50 text-[#176f50] rounded px-1.5 py-0.5 truncate max-w-full">
              {product.category}
            </span>
          )
        )}
      </div>
    </Link>
  )
}
