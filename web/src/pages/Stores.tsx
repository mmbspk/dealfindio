import React, { useState, useEffect, useCallback, useRef } from 'react'
import { Link } from 'react-router-dom'
import { apiClient } from '../api/client'
import { StoresMap } from '../components/StoresMap'
import { useGeolocation } from '../hooks/useGeolocation'

const RADIUS_KEY = 'dealfindio_radius'

function loadStoredRadius(): number {
  try {
    const v = localStorage.getItem(RADIUS_KEY)
    if (v) {
      const n = parseInt(v, 10)
      if (n >= 1 && n <= 20) return n
    }
  } catch {}
  return 10
}

interface NearbyStore {
  id: string
  chain: string
  name: string
  address?: string
  suburb?: string
  state?: string
  postcode?: string
  lat: number
  lon: number
  distance_km: number
  phone?: string
  hours?: string
}


const CHAIN_META: Record<string, { color: string; bg: string; border: string; label: string }> = {
  coles:       { color: 'text-red-700',    bg: 'bg-red-50 border-red-200',       border: 'border-red-200',    label: 'Coles' },
  woolworths:  { color: 'text-green-700',  bg: 'bg-green-50 border-green-200',   border: 'border-green-200',  label: 'Woolworths' },
  aldi:        { color: 'text-blue-700',   bg: 'bg-blue-50 border-blue-200',     border: 'border-blue-200',   label: 'ALDI' },
  iga:         { color: 'text-orange-700', bg: 'bg-orange-50 border-orange-200', border: 'border-orange-200', label: 'IGA' },
  dan_murphys: { color: 'text-purple-700', bg: 'bg-purple-50 border-purple-200', border: 'border-purple-200', label: "Dan Murphy's" },
  drakes:      { color: 'text-yellow-700', bg: 'bg-yellow-50 border-yellow-200', border: 'border-yellow-200', label: 'Drakes' },
}

const CHAINS = [
  { value: '', label: 'All Stores' },
  { value: 'coles', label: 'Coles' },
  { value: 'woolworths', label: 'Woolworths' },
  { value: 'aldi', label: 'ALDI' },
  { value: 'iga', label: 'IGA' },
  { value: 'dan_murphys', label: "Dan Murphy's" },
  { value: 'drakes', label: 'Drakes' },
]

function chainMeta(chain: string) {
  return CHAIN_META[chain.toLowerCase()] ?? { color: 'text-gray-700', bg: 'bg-gray-50 border-gray-200', border: 'border-gray-200', label: chain }
}

function ChainIcon({ chain }: { chain: string }) {
  const c = chain.toLowerCase()
  if (c === 'coles')
    return (
      <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
        <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8z"/>
      </svg>
    )
  if (c === 'woolworths')
    return (
      <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
        <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>
      </svg>
    )
  return (
    <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M3 3h2l.4 2M7 13h10l4-8H5.4M7 13L5.4 5M7 13l-2.293 2.293c-.63.63-.184 1.707.707 1.707H17m0 0a2 2 0 100 4 2 2 0 000-4zm-8 2a2 2 0 11-4 0 2 2 0 014 0z"/>
    </svg>
  )
}

// Geocode an Australian postcode or suburb via Nominatim (no API key needed)
async function geocodeAU(query: string): Promise<{ lat: number; lon: number; display_name: string } | null> {
  const q = encodeURIComponent(`${query}, Australia`)
  const url = `https://nominatim.openstreetmap.org/search?q=${q}&format=json&limit=1&countrycodes=au`
  try {
    const r = await fetch(url, { headers: { 'Accept-Language': 'en' } })
    const data = await r.json()
    if (!data.length) return null
    return { lat: parseFloat(data[0].lat), lon: parseFloat(data[0].lon), display_name: data[0].display_name }
  } catch {
    return null
  }
}

export const Stores: React.FC = () => {
  const { position, requestLocation, clearLocation } = useGeolocation()

  // Display label for the current location
  const [locationLabel, setLocationLabel] = useState<string>(() => {
    if (typeof window !== 'undefined') {
      try {
        const raw = localStorage.getItem('dealfindio_location')
        if (raw) {
          const p = JSON.parse(raw)
          if (p.label) return p.label
          if (typeof p.lat === 'number') return `${p.lat.toFixed(4)}, ${p.lon.toFixed(4)}`
        }
      } catch {}
    }
    return ''
  })

  const [gpsStatus, setGpsStatus] = useState<'idle' | 'requesting' | 'denied' | 'error'>('idle')
  const [gpsErrorMsg, setGpsErrorMsg] = useState('')
  const [stores, setStores] = useState<NearbyStore[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [radiusKm, setRadiusKm] = useState(() => loadStoredRadius())
  const [pendingRadius, setPendingRadius] = useState(() => loadStoredRadius())
  const [selectedChain, setSelectedChain] = useState('')
  const [showManual, setShowManual] = useState(false)
  const [manualQuery, setManualQuery] = useState('')
  const [geocoding, setGeocoding] = useState(false)
  const [geocodeError, setGeocodeError] = useState('')
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Persist radius whenever it changes
  useEffect(() => {
    localStorage.setItem(RADIUS_KEY, String(radiusKm))
  }, [radiusKm])

  // Listen for radius changes from other pages (e.g. Product page)
  useEffect(() => {
    const handler = (e: StorageEvent) => {
      if (e.key !== RADIUS_KEY || !e.newValue) return
      const n = parseInt(e.newValue, 10)
      if (n >= 1 && n <= 100) {
        setRadiusKm(n)
        setPendingRadius(n)
      }
    }
    window.addEventListener('storage', handler)
    return () => window.removeEventListener('storage', handler)
  }, [])

  const handleGpsRequest = useCallback(() => {
    setGpsStatus('requesting')
    setGpsErrorMsg('')
    requestLocation()
  }, [requestLocation])

  // Watch geo hook status to update our local GPS status
  useEffect(() => {
    if (position) {
      setGpsStatus('idle')
      setLocationLabel(position.label || `${position.lat.toFixed(4)}, ${position.lon.toFixed(4)}`)
    }
  }, [position])

  const fetchStores = useCallback(async (lat: number, lon: number, radius: number, chain: string) => {
    setLoading(true)
    setError('')
    try {
      const params: Record<string, unknown> = { lat, lon, radius_km: radius, limit: 100 }
      if (chain) params.chain = chain
      const r = await apiClient.get('/api/v1/stores/nearby', { params })
      setStores(r.data.stores || [])
    } catch {
      setError('Could not load nearby stores. Please try again.')
      setStores([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!position) return
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      fetchStores(position.lat, position.lon, radiusKm, selectedChain)
    }, 300)
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current) }
  }, [position, radiusKm, selectedChain, fetchStores])

  const handleRadiusChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = Number(e.target.value)
    setPendingRadius(v)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => setRadiusKm(v), 400)
  }

  const handleManualSearch = async () => {
    if (!manualQuery.trim()) return
    setGeocoding(true)
    setGeocodeError('')
    const result = await geocodeAU(manualQuery.trim())
    setGeocoding(false)
    if (!result) {
      setGeocodeError('Location not found. Try a suburb name or postcode (e.g. "Bondi" or "2026").')
      return
    }
    const parts = result.display_name.split(',')
    const label = parts.slice(0, 2).join(',').trim()
    setLocationLabel(label)
    // Write to shared localStorage key and dispatch storage event so useGeolocation syncs
    const stored = { lat: result.lat, lon: result.lon, accuracy: 1000, label }
    localStorage.setItem('dealfindio_location', JSON.stringify(stored))
    window.dispatchEvent(new StorageEvent('storage', {
      key: 'dealfindio_location',
      newValue: JSON.stringify(stored),
    }))
    setShowManual(false)
    setManualQuery('')
  }

  const handleClearLocation = () => {
    clearLocation()
    setLocationLabel('')
    setGpsStatus('idle')
    setGpsErrorMsg('')
    setStores([])
  }

  const chainCounts = stores.reduce<Record<string, number>>((acc, s) => {
    const k = s.chain.toLowerCase()
    acc[k] = (acc[k] ?? 0) + 1
    return acc
  }, {})

  // Location entry screen
  if (!position) {
    const denied = gpsStatus === 'denied'
    const requesting = gpsStatus === 'requesting'

    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center px-4">
        <div className="max-w-md w-full">
          <div className="text-center mb-8">
            <div className="relative w-24 h-24 mx-auto mb-6">
              {requesting && <div className="absolute inset-0 bg-emerald-100 rounded-full animate-ping opacity-40" />}
              <div className={`relative w-24 h-24 rounded-full flex items-center justify-center shadow-lg ${requesting ? 'bg-emerald-400' : denied ? 'bg-amber-500' : 'bg-emerald-600'}`}>
                <svg className="w-12 h-12 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"/>
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"/>
                </svg>
              </div>
            </div>

            <h1 className="text-2xl font-bold text-gray-900 mb-2">
              {requesting ? 'Getting your location…' : denied ? 'Location Access Blocked' : 'Set Your Location'}
            </h1>
            <p className="text-gray-500 text-sm leading-relaxed">
              {requesting
                ? 'Please allow location access when your browser prompts you.'
                : 'Set your location once here — it persists across Deals, Products, and Stores until you change it.'}
            </p>
          </div>

          {gpsErrorMsg && (
            <div className="mb-4 bg-red-50 border border-red-200 rounded-xl p-3 text-sm text-red-700 flex items-start gap-2">
              <svg className="w-4 h-4 mt-0.5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
              </svg>
              {gpsErrorMsg}
            </div>
          )}

          {!requesting && (
            <button
              onClick={handleGpsRequest}
              className="w-full inline-flex items-center justify-center gap-2 bg-amber-500 text-gray-900 px-6 py-3.5 rounded-xl font-semibold text-base hover:bg-amber-600 transition shadow-md cursor-pointer mb-4"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"/>
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"/>
              </svg>
              {denied ? 'Try Location Again' : 'Use My Current Location'}
            </button>
          )}

          {!requesting && (
            <div className="flex items-center gap-3 my-4">
              <div className="flex-1 h-px bg-gray-200" />
              <span className="text-xs text-gray-400 font-medium">OR</span>
              <div className="flex-1 h-px bg-gray-200" />
            </div>
          )}

          {!requesting && (
            <div>
              <p className="text-sm font-medium text-gray-700 mb-2">Enter suburb or postcode</p>
              <div className="flex gap-2">
                <input
                  type="text"
                  placeholder="e.g. Bondi Beach or 2026"
                  value={manualQuery}
                  onChange={e => { setManualQuery(e.target.value); setGeocodeError('') }}
                  onKeyDown={e => e.key === 'Enter' && handleManualSearch()}
                  className="flex-1 px-4 py-3 border border-gray-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400"
                />
                <button
                  onClick={handleManualSearch}
                  disabled={geocoding || !manualQuery.trim()}
                  className="px-5 py-3 bg-amber-500 text-gray-900 rounded-xl font-semibold text-sm hover:bg-amber-600 transition disabled:opacity-50 cursor-pointer"
                >
                  {geocoding ? '…' : 'Go'}
                </button>
              </div>
              {geocodeError && (
                <p className="mt-2 text-sm text-red-600">{geocodeError}</p>
              )}
            </div>
          )}

          {denied && (
            <div className="mt-4 bg-amber-50 border border-amber-200 rounded-xl p-4 text-sm text-amber-800">
              <p className="font-semibold mb-1">To re-enable GPS in Chrome:</p>
              <p>Click the lock icon in the address bar → Site settings → Location → Allow, then reload the page.</p>
            </div>
          )}

          <p className="mt-6 text-center text-xs text-gray-400">
            Your location is saved in your browser only and never sent to our servers.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Sticky filter bar */}
      <div className="bg-white border-b sticky top-[65px] z-40 shadow-sm">
        <div className="max-w-[1280px] mx-auto px-6 sm:px-8 lg:px-10 py-3 space-y-2.5">

          {/* Row 1: location + radius + store count + clear */}
          <div className="flex items-center gap-3">

            {/* Location pill — click to change */}
            <button
              onClick={() => setShowManual(v => !v)}
              className="flex items-center gap-2 text-sm text-emerald-600 bg-emerald-50 px-3 py-1.5 rounded-full border border-emerald-100 flex-shrink-0 hover:bg-emerald-100 transition cursor-pointer"
              title="Change location"
            >
              <svg className="w-3.5 h-3.5 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M5.05 4.05a7 7 0 119.9 9.9L10 18.9l-4.95-4.95a7 7 0 010-9.9zM10 11a2 2 0 100-4 2 2 0 000 4z" clipRule="evenodd"/>
              </svg>
              <span className="font-medium truncate max-w-[180px]">
                {locationLabel || `${position.lat.toFixed(4)}, ${position.lon.toFixed(4)}`}
              </span>
              <svg className="w-3 h-3 flex-shrink-0 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536M9 11l6-6 3 3-6 6H9v-3z"/>
              </svg>
            </button>

            {/* Radius slider */}
            <div className="flex items-center gap-2 flex-1 max-w-xs">
              <span className="text-sm text-gray-500 whitespace-nowrap">Radius:</span>
              <input
                type="range"
                min={1}
                max={20}
                step={1}
                value={pendingRadius}
                onChange={handleRadiusChange}
                className="flex-1 h-2 rounded-full appearance-none cursor-pointer accent-emerald-600"
                aria-label="Search radius in kilometres"
              />
              <span className="text-sm font-semibold text-emerald-700 w-12 text-right whitespace-nowrap">
                {pendingRadius} km
              </span>
            </div>

            <div className="flex items-center gap-3 ml-auto">
              {!loading && stores.length > 0 && (
                <span className="text-sm text-gray-500 whitespace-nowrap">
                  {stores.length} store{stores.length !== 1 ? 's' : ''}
                </span>
              )}
              <button
                onClick={handleClearLocation}
                className="text-xs text-gray-400 hover:text-red-500 transition cursor-pointer whitespace-nowrap"
              >
                Clear location
              </button>
            </div>
          </div>

          {/* Row 2: chain filter pills */}
          <div className="flex items-center gap-2 overflow-x-auto scrollbar-hide pb-0.5">
            {CHAINS.map(c => (
              <button
                key={c.value}
                onClick={() => setSelectedChain(c.value)}
                className={`flex-shrink-0 px-3 py-1 text-xs font-medium rounded-full border transition cursor-pointer ${
                  selectedChain === c.value
                    ? 'bg-emerald-600 text-white border-emerald-600'
                    : 'bg-white text-gray-600 border-gray-200 hover:border-emerald-300 hover:text-emerald-600'
                }`}
              >
                {c.label}
                {c.value && chainCounts[c.value] ? (
                  <span className={`ml-1.5 ${selectedChain === c.value ? 'text-emerald-200' : 'text-gray-400'}`}>
                    {chainCounts[c.value]}
                  </span>
                ) : null}
              </button>
            ))}
          </div>

          {/* Inline location change */}
          {showManual && (
            <div className="mt-3 flex gap-2 items-start flex-wrap">
              <button
                onClick={() => { setShowManual(false); handleGpsRequest() }}
                className="inline-flex items-center gap-1.5 px-4 py-2 bg-emerald-50 text-emerald-700 border border-emerald-200 rounded-lg text-sm font-medium hover:bg-emerald-100 transition cursor-pointer"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"/>
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"/>
                </svg>
                Use GPS
              </button>
              <input
                type="text"
                placeholder="Suburb or postcode (e.g. Bondi or 2026)"
                value={manualQuery}
                onChange={e => { setManualQuery(e.target.value); setGeocodeError('') }}
                onKeyDown={e => e.key === 'Enter' && handleManualSearch()}
                autoFocus
                className="flex-1 min-w-[200px] px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400"
              />
              <button
                onClick={handleManualSearch}
                disabled={geocoding || !manualQuery.trim()}
                className="px-4 py-2 bg-amber-500 text-gray-900 rounded-lg text-sm font-semibold hover:bg-amber-600 transition disabled:opacity-50 cursor-pointer whitespace-nowrap"
              >
                {geocoding ? 'Searching…' : 'Search'}
              </button>
              <button
                onClick={() => { setShowManual(false); setManualQuery(''); setGeocodeError('') }}
                className="px-3 py-2 text-gray-400 hover:text-gray-600 transition cursor-pointer"
                aria-label="Cancel"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12"/>
                </svg>
              </button>
              {geocodeError && (
                <p className="text-sm text-red-600 w-full">{geocodeError}</p>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="max-w-[1280px] mx-auto px-6 sm:px-8 lg:px-10 py-6">
        {error && (
          <div className="mb-4 bg-red-50 border-l-4 border-red-400 p-3 rounded text-sm text-red-700">
            {error}
          </div>
        )}

        {loading ? (
          <StoresSkeleton />
        ) : (
          <>
            <StoresMap
              lat={position.lat}
              lon={position.lon}
              radiusKm={radiusKm}
              stores={stores}
              onStoreClick={id => {
                const el = document.getElementById(`store-${id}`)
                el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
                el?.classList.add('ring-2', 'ring-emerald-400')
                setTimeout(() => el?.classList.remove('ring-2', 'ring-emerald-400'), 2000)
              }}
            />

            {stores.length === 0 ? (
              <EmptyState
                radius={radiusKm}
                chain={selectedChain}
                onClearChain={() => setSelectedChain('')}
                onExpandRadius={() => { setPendingRadius(20); setRadiusKm(20) }}
              />
            ) : (
              <StoreGrid stores={stores} />
            )}
          </>
        )}
      </div>
    </div>
  )
}

function StoreGrid({ stores }: { stores: NearbyStore[] }) {
  return (
    <div className="mt-6 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
      {stores.map(store => <StoreCard key={store.id} store={store} />)}
    </div>
  )
}

function StoreCard({ store }: { store: NearbyStore }) {
  const meta = chainMeta(store.chain)
  const locationLine = [store.address, store.suburb, store.state, store.postcode].filter(Boolean).join(', ')
  const distanceLabel = store.distance_km < 1
    ? `${Math.round(store.distance_km * 1000)} m`
    : `${store.distance_km.toFixed(1)} km`
  const mapsUrl = `https://www.google.com/maps/dir/?api=1&destination=${store.lat},${store.lon}`

  return (
    <div
      id={`store-${store.id}`}
      className={`bg-white rounded-xl border ${meta.border} p-4 hover:shadow-md transition-all flex flex-col`}
    >
      {/* Header row */}
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className={`w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0 ${meta.bg}`}>
            <span className={meta.color}><ChainIcon chain={store.chain} /></span>
          </div>
          <div className="min-w-0">
            <p className="font-semibold text-gray-900 text-sm leading-tight truncate">{store.name}</p>
            <span className={`text-xs font-medium ${meta.color}`}>{meta.label}</span>
          </div>
        </div>
        <div className="flex-shrink-0 flex items-center gap-1 bg-emerald-50 text-emerald-700 text-xs font-semibold px-2.5 py-1 rounded-full border border-emerald-100">
          <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
            <path fillRule="evenodd" d="M5.05 4.05a7 7 0 119.9 9.9L10 18.9l-4.95-4.95a7 7 0 010-9.9zM10 11a2 2 0 100-4 2 2 0 000 4z" clipRule="evenodd"/>
          </svg>
          {distanceLabel}
        </div>
      </div>

      {/* Address */}
      {locationLine && (
        <p className="text-xs text-gray-500 leading-snug mb-2 flex items-start gap-1.5">
          <svg className="w-3.5 h-3.5 mt-0.5 flex-shrink-0 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"/>
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"/>
          </svg>
          {locationLine}
        </p>
      )}

      <DistanceBar km={store.distance_km} />

      {/* Action buttons */}
      <div className="mt-3 pt-3 border-t border-gray-100 flex gap-2">
        <Link
          to={`/deals?chain=${store.chain.toLowerCase()}`}
          className={`flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold transition cursor-pointer ${meta.bg} ${meta.color} hover:opacity-80`}
        >
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z"/>
          </svg>
          View deals
        </Link>
        <a
          href={mapsUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-xs font-semibold bg-gray-50 text-gray-600 border border-gray-200 hover:bg-gray-100 transition"
        >
          <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7"/>
          </svg>
          Directions
        </a>
      </div>
    </div>
  )
}

function DistanceBar({ km }: { km: number }) {
  const pct = Math.min((km / 20) * 100, 100)
  const color = km <= 2 ? 'bg-emerald-500' : km <= 7 ? 'bg-emerald-500' : 'bg-amber-500'
  return (
    <div className="mt-2">
      <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full transition-all`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

function StoresSkeleton() {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
      {Array.from({ length: 9 }).map((_, i) => (
        <div key={i} className="bg-white rounded-xl border border-gray-100 p-4 animate-pulse">
          <div className="flex items-center gap-3 mb-3">
            <div className="w-9 h-9 bg-gray-100 rounded-lg" />
            <div className="flex-1">
              <div className="h-3.5 bg-gray-100 rounded w-2/3 mb-1.5" />
              <div className="h-3 bg-gray-100 rounded w-1/3" />
            </div>
            <div className="w-14 h-6 bg-gray-100 rounded-full" />
          </div>
          <div className="h-3 bg-gray-100 rounded w-full mb-1" />
          <div className="h-1.5 bg-gray-100 rounded-full mt-3" />
        </div>
      ))}
    </div>
  )
}

function EmptyState({
  radius,
  chain,
  onClearChain,
  onExpandRadius,
}: {
  radius: number
  chain: string
  onClearChain: () => void
  onExpandRadius: () => void
}) {
  return (
    <div className="text-center py-16">
      <div className="w-16 h-16 mx-auto mb-4 bg-gray-100 rounded-full flex items-center justify-center">
        <svg className="w-8 h-8 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"/>
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"/>
        </svg>
      </div>
      <h3 className="font-semibold text-gray-900 mb-1">No stores found within {radius} km</h3>
      <p className="text-sm text-gray-500 mb-5">Try a larger radius or a different location.</p>
      <div className="flex flex-col sm:flex-row items-center justify-center gap-3">
        {radius < 20 && (
          <button
            onClick={onExpandRadius}
            className="px-5 py-2 bg-amber-500 text-gray-900 rounded-lg text-sm font-semibold hover:bg-amber-600 transition cursor-pointer"
          >
            Expand to 20 km
          </button>
        )}
        {chain && (
          <button
            onClick={onClearChain}
            className="px-5 py-2 border border-gray-200 text-gray-600 rounded-lg text-sm font-medium hover:border-emerald-300 hover:text-emerald-600 transition cursor-pointer"
          >
            Show all chains
          </button>
        )}
      </div>
    </div>
  )
}
