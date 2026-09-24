import type React from 'react'
import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { apiClient } from '../api/client'
import { PriceChart } from '../components/PriceChart'
import { PredictionCard } from '../components/PredictionCard'
import type { Prediction } from '../components/PredictionCard'
import { useGeolocation } from '../hooks/useGeolocation'
import { SaveToListButton } from '../components/SaveToListButton'
import type { PricePoint, Product as ProductType } from '../types'

const RADIUS_KEY = 'dealfindio_radius'
const DEFAULT_RADIUS = 10

function loadStoredRadius(): number {
  try {
    const v = localStorage.getItem(RADIUS_KEY)
    if (v) {
      const n = parseInt(v, 10)
      if (n >= 1 && n <= 20) return n
    }
  } catch {}
  return DEFAULT_RADIUS
}

interface ProductDetail extends ProductType {
  description?: string
  upc?: string
  last_updated?: string
}

interface StoreItem {
  id: string
  chain: string
  name: string
  suburb?: string
  state?: string
  price_aud: number
  is_on_sale: boolean
  discount_pct?: number | null
  distance_km?: number | null
}

const CHAIN_COLORS: Record<string, string> = {
  coles: 'bg-red-50 text-red-700 border-red-200',
  woolworths: 'bg-green-50 text-green-700 border-green-200',
  aldi: 'bg-blue-50 text-blue-700 border-blue-200',
  iga: 'bg-orange-50 text-orange-700 border-orange-200',
  dan_murphys: 'bg-purple-50 text-purple-700 border-purple-200',
  drakes: 'bg-yellow-50 text-yellow-700 border-yellow-200',
}

const CHAIN_LABELS: Record<string, string> = {
  coles: 'Coles',
  woolworths: 'Woolworths',
  aldi: 'ALDI',
  iga: 'IGA',
  dan_murphys: "Dan Murphy's",
  drakes: 'Drakes',
}

function ChainBadge({ chain }: { chain: string }) {
  const cls = CHAIN_COLORS[chain.toLowerCase()] ?? 'bg-gray-50 text-gray-700 border-gray-200'
  const label = CHAIN_LABELS[chain.toLowerCase()] ?? chain
  return (
    <span className={`inline-block text-xs font-semibold px-2 py-0.5 rounded border ${cls}`}>
      {label}
    </span>
  )
}

function formatStoreName(name: string, chain: string): string {
  // Strip "(catalog)" suffix — these are virtual catalog store rows, not physical locations
  const cleaned = name.replace(/\s*\(catalog\)\s*$/i, '').trim()
  // If what remains is just the bare chain name (case-insensitive), use the chain label
  if (!cleaned || cleaned.toLowerCase() === chain.toLowerCase()) {
    return CHAIN_LABELS[chain.toLowerCase()] ?? chain
  }
  return cleaned
}

export const Product: React.FC = () => {
  const { productId } = useParams<{ productId: string }>()
  const navigate = useNavigate()
  const { position, status: geoStatus, requestLocation } = useGeolocation()
  const [product, setProduct] = useState<ProductDetail | null>(null)
  const [priceHistory, setPriceHistory] = useState<PricePoint[]>([])
  const [stores, setStores] = useState<StoreItem[]>([])
  const [predictions, setPredictions] = useState<Prediction[]>([])
  const [loading, setLoading] = useState(true)
  const [storesLoading, setStoresLoading] = useState(false)
  const [imgError, setImgError] = useState(false)
  const [showAllStores, setShowAllStores] = useState(false)
  const [radiusKm, setRadiusKm] = useState(() => loadStoredRadius())
  const [pendingRadius, setPendingRadius] = useState(() => loadStoredRadius())
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Keep a ref so fetchStores always sees the current radius without being recreated
  const radiusRef = useRef(radiusKm)
  useEffect(() => { radiusRef.current = radiusKm }, [radiusKm])

  const STORES_PREVIEW = 5

  // Persist radius to localStorage so Stores page stays in sync
  useEffect(() => {
    localStorage.setItem(RADIUS_KEY, String(radiusKm))
  }, [radiusKm])

  // Listen for radius changes written by other pages (e.g. Stores page in another tab)
  useEffect(() => {
    const handler = (e: StorageEvent) => {
      if (e.key !== RADIUS_KEY || !e.newValue) return
      const n = parseInt(e.newValue, 10)
      if (n >= 1 && n <= 20) {
        setRadiusKm(n)
        setPendingRadius(n)
      }
    }
    window.addEventListener('storage', handler)
    return () => window.removeEventListener('storage', handler)
  }, [])

  const handleRadiusChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = Number(e.target.value)
    setPendingRadius(v)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => setRadiusKm(v), 400)
  }

  const fetchStores = useCallback(async (pid: string, pos: { lat: number; lon: number } | null) => {
    setStoresLoading(true)
    try {
      const params: Record<string, unknown> = {}
      if (pos) {
        params.lat = pos.lat
        params.lon = pos.lon
        params.radius_km = radiusRef.current
      }
      const r = await apiClient.get(`/api/v1/products/${pid}/stores`, { params })
      setStores(r.data.stores || [])
    } catch {
      setStores([])
    } finally {
      setStoresLoading(false)
    }
  }, []) // stable — reads radius from ref

  useEffect(() => {
    if (!productId) {
      setLoading(false)
      return
    }
    const fetchAll = async () => {
      setLoading(true)
      await Promise.allSettled([
        apiClient.get(`/api/v1/products/${productId}`).then(r => setProduct(r.data)).catch(() => {}),
        apiClient.get(`/api/v1/products/${productId}/prices`, { params: { days: 90 } })
          .then(r => {
            const raw: { date: string; price_aud: number; chain: string }[] = r.data.data || r.data || []
            setPriceHistory(raw.map(p => ({ date: p.date, price: p.price_aud, chain: p.chain })))
          })
          .catch(() => setPriceHistory([])),
        apiClient.get(`/api/v1/products/${productId}/predictions`)
          .then(r => setPredictions(r.data || []))
          .catch(() => setPredictions([])),
      ])
      setLoading(false)
      fetchStores(productId, position)
    }
    fetchAll()
  }, [productId]) // eslint-disable-line react-hooks/exhaustive-deps

  // Re-fetch stores when position or radius changes
  useEffect(() => {
    if (!productId) return
    fetchStores(productId, position)
  }, [position?.lat, position?.lon, radiusKm, productId, fetchStores])

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50">
        <div className="bg-white border-b">
          <div className="max-w-[1280px] mx-auto px-6 sm:px-8 lg:px-10 py-4 flex items-center gap-3">
            <div className="h-4 w-20 bg-gray-100 rounded animate-pulse" />
            <div className="h-4 w-4 bg-gray-100 rounded animate-pulse" />
            <div className="h-4 w-32 bg-gray-100 rounded animate-pulse" />
          </div>
        </div>
        <div className="max-w-[1280px] mx-auto px-6 sm:px-8 lg:px-10 py-8">
          <div className="grid grid-cols-1 lg:grid-cols-5 gap-8">
            <div className="lg:col-span-2">
              <div className="bg-white rounded-2xl p-6 animate-pulse space-y-4">
                <div className="aspect-square bg-gray-100 rounded-2xl max-w-[200px]" />
                <div className="h-6 bg-gray-100 rounded w-3/4" />
                <div className="h-4 bg-gray-100 rounded w-1/2" />
                <div className="h-4 bg-gray-100 rounded w-1/3" />
              </div>
            </div>
            <div className="lg:col-span-3">
              <div className="bg-white rounded-2xl p-6 animate-pulse space-y-3">
                {Array.from({ length: 4 }).map((_, i) => (
                  <div key={i} className="h-16 bg-gray-100 rounded-2xl" />
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    )
  }

  if (!product) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="text-center">
          <svg className="w-16 h-16 text-gray-300 mx-auto mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 3h2l.4 2M7 13h10l4-8H5.4M7 13L5.4 5M7 13l-2.293 2.293c-.63.63-.184 1.707.707 1.707H17m0 0a2 2 0 100 4 2 2 0 000-4zm-8 2a2 2 0 11-4 0 2 2 0 014 0z" />
          </svg>
          <h2 className="text-xl font-semibold text-gray-900 mb-2">Product not found</h2>
          <button onClick={() => navigate(-1)} className="mt-4 text-[#176f50] hover:underline text-sm font-medium">
            Go back
          </button>
        </div>
      </div>
    )
  }

  // Best price: from nearby stores when location is active, else from product data
  const lowestStorePrice = stores.length > 0 ? Math.min(...stores.map(s => s.price_aud)) : null
  const lowestPrice = lowestStorePrice ?? product.lowest_price_aud ?? null
  const onSaleStores = stores.filter(s => s.is_on_sale)
  const visibleStores = showAllStores ? stores : stores.slice(0, STORES_PREVIEW)

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Breadcrumb / sticky top bar */}
      <div className="bg-white border-b sticky top-[65px] z-40 shadow-sm">
        <div className="max-w-[1280px] mx-auto px-6 sm:px-8 lg:px-10 py-3 flex items-center gap-2 text-sm">
          <button
            onClick={() => navigate(-1)}
            className="flex items-center gap-1 text-gray-500 hover:text-[#176f50] transition font-medium cursor-pointer"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
            Back
          </button>
          <span className="text-gray-300">/</span>
          <Link to="/products" className="text-gray-500 hover:text-[#176f50] transition">Products</Link>
          <span className="text-gray-300">/</span>
          <span className="text-gray-900 font-medium truncate max-w-[200px] sm:max-w-sm">{product.name}</span>

          {/* Location indicator / prompt */}
          <div className="ml-auto flex items-center gap-2">
            {geoStatus === 'granted' && position ? (
              <span className="flex items-center gap-1.5 text-xs text-[#176f50] bg-emerald-50 px-2.5 py-1 rounded-full">
                <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z" />
                </svg>
                {position.label ?? 'Location active'}
              </span>
            ) : geoStatus !== 'requesting' ? (
              <button
                onClick={requestLocation}
                className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-[#176f50] border border-gray-200 hover:border-[#a8cdb1] px-2.5 py-1 rounded-full transition cursor-pointer"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
                Near me
              </button>
            ) : (
              <span className="text-xs text-gray-400 animate-pulse">Locating...</span>
            )}
          </div>
        </div>
      </div>

      <div className="max-w-[1280px] mx-auto px-6 sm:px-8 lg:px-10 py-6">
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">

          {/* ── Left: product info ───────────────────────────── */}
          <div className="lg:col-span-2 space-y-4">
            <div className="bg-white rounded-2xl border border-gray-100 p-6">
              {/* Image */}
              <div className="w-40 h-40 mx-auto bg-gray-50 rounded-2xl overflow-hidden flex items-center justify-center mb-5 border border-gray-100">
                {product.image_url && !imgError ? (
                  <img
                    src={product.image_url}
                    alt={product.name}
                    className="w-full h-full object-contain"
                    onError={() => setImgError(true)}
                  />
                ) : (
                  <svg className="w-16 h-16 text-gray-200" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
                  </svg>
                )}
              </div>

              {/* Name + meta */}
              <h1 className="text-xl font-bold text-gray-900 leading-snug mb-2">{product.name}</h1>

              {product.brand && (
                <p className="text-sm text-gray-500 mb-3">by <span className="font-medium text-gray-700">{product.brand}</span></p>
              )}

              {product.category && (
                <span className="inline-block px-3 py-1 bg-emerald-50 text-[#176f50] text-xs font-medium rounded-full mb-4">
                  {product.category}
                </span>
              )}

              {(product.package_size || product.unit_price != null) && (
                <div className="flex items-center gap-3 mb-4 flex-wrap">
                  {product.package_size && (
                    <span className="text-sm font-medium text-gray-700 bg-gray-100 px-2.5 py-1 rounded-md">
                      {product.package_size}
                    </span>
                  )}
                  {product.unit_price != null && (
                    <span className="text-xs text-gray-500">
                      ${product.unit_price.toFixed(2)} / {product.unit_price_label ?? 'unit'}
                    </span>
                  )}
                </div>
              )}

              {/* Best price — derived from stores list (authoritative, same source as right panel) */}
              {lowestPrice != null && (
                <div className="border-t border-gray-100 pt-4 mt-2">
                  <p className="text-xs text-gray-400 mb-1">
                    {position ? 'Best price nearby' : 'Best price available'}
                  </p>
                  <div className="flex items-baseline gap-2">
                    <span className={`text-3xl font-bold ${onSaleStores.length > 0 ? 'text-[#176f50]' : 'text-gray-900'}`}>
                      ${lowestPrice.toFixed(2)}
                    </span>
                    {onSaleStores.length > 0 && (
                      <span className="text-xs text-[#176f50] font-medium">
                        On sale at {onSaleStores.length} store{onSaleStores.length > 1 ? 's' : ''}
                      </span>
                    )}
                  </div>
                </div>
              )}

              {product.gtin && (
                <p className="text-xs text-gray-400 mt-4 font-mono">GTIN: {product.gtin}</p>
              )}

              <div className="mt-4 pt-4 border-t border-gray-100 flex items-center gap-3">
                <SaveToListButton productId={product.id} size="md" />
                <span className="text-sm text-gray-400">Save to a list</span>
              </div>
            </div>
          </div>

          {/* ── Right: stores ───────────────────────────────── */}
          <div className="lg:col-span-3">
            <div className="bg-white rounded-2xl border border-gray-100 overflow-hidden">
              <div className="px-5 py-4 border-b border-gray-100">
                <div className="flex items-center justify-between">
                  <div>
                    <h2 className="text-base font-semibold text-gray-900">
                      {position ? 'Stores Near You' : 'All Stores'}
                    </h2>
                    {!storesLoading && stores.length > 0 && (
                      <p className="text-xs text-gray-400 mt-0.5">
                        {stores.length} store{stores.length !== 1 ? 's' : ''} {position ? `within ${radiusKm} km` : 'with stock'}
                      </p>
                    )}
                  </div>
                  {position && (
                    <span className="text-xs text-emerald-500 flex items-center gap-1">
                      <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
                        <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5c-1.38 0-2.5-1.12-2.5-2.5s1.12-2.5 2.5-2.5 2.5 1.12 2.5 2.5-1.12 2.5-2.5 2.5z" />
                      </svg>
                      Sorted by distance
                    </span>
                  )}
                </div>

                {/* Radius slider — only shown when location is active */}
                {position && (
                  <div className="flex items-center gap-3 mt-3">
                    <span className="text-xs text-gray-500 whitespace-nowrap">Radius:</span>
                    <input
                      type="range"
                      min={1}
                      max={20}
                      step={1}
                      value={pendingRadius}
                      onChange={handleRadiusChange}
                      className="flex-1 h-1.5 rounded-full appearance-none cursor-pointer accent-emerald-600"
                      aria-label="Search radius in kilometres"
                    />
                    <span className="text-xs font-semibold text-[#135d46] w-12 text-right whitespace-nowrap">
                      {pendingRadius} km
                    </span>
                  </div>
                )}
              </div>

              {storesLoading ? (
                <div className="p-4 space-y-3">
                  {Array.from({ length: 4 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-4 p-3 rounded-2xl animate-pulse">
                      <div className="w-10 h-10 bg-gray-100 rounded-lg flex-shrink-0" />
                      <div className="flex-1 space-y-1.5">
                        <div className="h-3 bg-gray-100 rounded w-1/2" />
                        <div className="h-3 bg-gray-100 rounded w-1/3" />
                      </div>
                      <div className="h-6 bg-gray-100 rounded w-16" />
                    </div>
                  ))}
                </div>
              ) : stores.length === 0 ? (
                <div className="py-12 text-center px-4">
                  <svg className="w-10 h-10 text-gray-300 mx-auto mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
                  </svg>
                  <p className="text-gray-600 font-medium text-sm">
                    {position ? `No stores found within ${radiusKm} km` : 'No stores found'}
                  </p>
                  {position && (
                    <p className="text-xs text-gray-400 mt-1">Try expanding the radius slider above</p>
                  )}
                  {!position && (
                    <button
                      onClick={requestLocation}
                      className="mt-3 text-[#176f50] text-sm font-medium hover:underline cursor-pointer"
                    >
                      Enable location to find nearby stores
                    </button>
                  )}
                </div>
              ) : (
                <div className="divide-y divide-gray-50">
                  {visibleStores.map((store) => {
                    const locationLine = [store.suburb, store.state].filter(Boolean).join(', ')
                    const displayName = formatStoreName(store.name, store.chain)
                    const distanceLabel = store.distance_km != null
                      ? store.distance_km < 1
                        ? `${Math.round(store.distance_km * 1000)} m away`
                        : `${store.distance_km.toFixed(1)} km away`
                      : null
                    return (
                      <div
                        key={store.id}
                        className="flex items-center gap-4 px-5 py-3.5 hover:bg-gray-50 transition"
                      >
                        <div className="flex-shrink-0">
                          <ChainBadge chain={store.chain} />
                        </div>

                        <div className="flex-1 min-w-0">
                          <p className="font-medium text-gray-900 text-sm truncate">{displayName}</p>
                          {locationLine && (
                            <p className="text-xs text-gray-400">{locationLine}</p>
                          )}
                          {distanceLabel && (
                            <p className="text-xs text-emerald-500 font-medium">{distanceLabel}</p>
                          )}
                        </div>

                        <div className="text-right flex-shrink-0">
                          <p className={`text-base font-bold ${store.is_on_sale ? 'text-[#176f50]' : 'text-gray-900'}`}>
                            ${store.price_aud.toFixed(2)}
                          </p>
                          {store.is_on_sale ? (
                            <span className="text-[10px] text-[#176f50] font-semibold">
                              {store.discount_pct ? `-${Math.round(store.discount_pct)}%` : 'On Sale'}
                            </span>
                          ) : (
                            <span className="text-[10px] text-gray-400">Regular</span>
                          )}
                        </div>
                      </div>
                    )
                  })}

                  {stores.length > STORES_PREVIEW && (
                    <div className="px-5 py-3 text-center">
                      <button
                        onClick={() => setShowAllStores(v => !v)}
                        className="text-sm text-[#176f50] hover:text-[#135d46] font-medium transition cursor-pointer"
                      >
                        {showAllStores ? 'Show fewer stores' : `Show all ${stores.length} stores`}
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Location prompt card — shown when no location set */}
            {!position && stores.length > 0 && (
              <div className="mt-4 bg-emerald-50 border border-emerald-100 rounded-2xl px-5 py-4 flex items-center gap-4">
                <svg className="w-8 h-8 text-emerald-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
                <div className="flex-1">
                  <p className="text-sm font-medium text-emerald-800">See stores near you</p>
                  <p className="text-xs text-emerald-500">Enable location to sort by distance and find deals nearby</p>
                </div>
                <button
                  onClick={requestLocation}
                  className="flex-shrink-0 bg-lime-300 text-gray-900 text-sm font-medium px-4 py-2 rounded-lg hover:bg-lime-400 transition cursor-pointer"
                >
                  Enable
                </button>
              </div>
            )}
          </div>
        </div>

        {/* ── Price history chart ──────────────────────────── */}
        {priceHistory.length > 0 && (
          <div className="mt-6 bg-white rounded-2xl border border-gray-100 p-6">
            <h2 className="text-base font-semibold text-gray-900 mb-1">Price History</h2>
            <p className="text-xs text-gray-400 mb-5">Last 90 days across all stores</p>
            <PriceChart data={priceHistory} />
          </div>
        )}

        {/* ── ML Predictions ───────────────────────────────── */}
        {predictions.length > 0 && (
          <div className="mt-6 bg-white rounded-2xl border border-gray-100 p-6">
            <h2 className="text-base font-semibold text-gray-900 mb-1">Price Predictions</h2>
            <p className="text-xs text-gray-400 mb-5">ML forecast for the next 1–2 weeks, updated every Monday</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {predictions.map((p) => (
                <PredictionCard key={p.id} prediction={p} />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
