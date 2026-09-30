import { useEffect, useRef } from 'react'
import L from 'leaflet'

// Fix Leaflet's default icon URLs broken by bundlers
delete (L.Icon.Default.prototype as unknown as Record<string, unknown>)._getIconUrl
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
  iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
  shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
})

interface StorePin {
  id: string
  name: string
  chain: string
  lat: number
  lon: number
  suburb?: string
  state?: string
  distance_km: number
}

interface StoresMapProps {
  lat: number
  lon: number
  radiusKm: number
  stores: StorePin[]
  onStoreClick?: (id: string) => void
}

// Chain-brand colors as Leaflet DivIcon SVG pins
const CHAIN_COLORS: Record<string, string> = {
  coles:      '#dc2626', // red-600
  woolworths: '#16a34a', // green-600
  aldi:       '#2563eb', // blue-600
  iga:        '#f97316', // orange-500
  drakes:     '#ca8a04', // yellow-600
}

function pinIcon(color: string, size = 28): L.DivIcon {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size * 1.4}" viewBox="0 0 28 40">
    <path d="M14 0C6.27 0 0 6.27 0 14c0 9.625 14 26 14 26S28 23.625 28 14C28 6.27 21.73 0 14 0z" fill="${color}" stroke="white" stroke-width="1.5"/>
    <circle cx="14" cy="14" r="6" fill="white" opacity="0.9"/>
  </svg>`
  return L.divIcon({
    html: svg,
    className: '',
    iconSize: [size, size * 1.4],
    iconAnchor: [size / 2, size * 1.4],
    popupAnchor: [0, -(size * 1.4)],
  })
}

const userIcon = L.divIcon({
  html: `<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 22 22">
    <circle cx="11" cy="11" r="10" fill="#4f46e5" stroke="white" stroke-width="2.5"/>
    <circle cx="11" cy="11" r="4" fill="white"/>
  </svg>`,
  className: '',
  iconSize: [22, 22],
  iconAnchor: [11, 11],
})

export function StoresMap({ lat, lon, radiusKm, stores, onStoreClick }: StoresMapProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<L.Map | null>(null)
  const radiusCircleRef = useRef<L.Circle | null>(null)
  const storeLayerRef = useRef<L.LayerGroup | null>(null)
  const userMarkerRef = useRef<L.Marker | null>(null)

  // Initialise map once
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return

    const map = L.map(containerRef.current, {
      zoomControl: true,
      attributionControl: true,
    })

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
    }).addTo(map)

    mapRef.current = map
    storeLayerRef.current = L.layerGroup().addTo(map)

    return () => {
      map.remove()
      mapRef.current = null
    }
  }, [])

  // Update centre / radius circle when location or radius changes
  useEffect(() => {
    const map = mapRef.current
    if (!map) return

    // Zoom to fit the radius
    const radiusMetres = radiusKm * 1000
    const bounds = L.latLng(lat, lon).toBounds(radiusMetres * 2)
    map.fitBounds(bounds, { padding: [20, 20], animate: true })

    // User location marker
    if (userMarkerRef.current) userMarkerRef.current.remove()
    userMarkerRef.current = L.marker([lat, lon], { icon: userIcon, zIndexOffset: 1000 })
      .bindPopup('<b>Your location</b>')
      .addTo(map)

    // Radius circle
    if (radiusCircleRef.current) radiusCircleRef.current.remove()
    radiusCircleRef.current = L.circle([lat, lon], {
      radius: radiusMetres,
      color: '#4f46e5',
      weight: 2,
      fillColor: '#4f46e5',
      fillOpacity: 0.07,
    }).addTo(map)
  }, [lat, lon, radiusKm])

  // Redraw store markers when stores list changes
  useEffect(() => {
    const layer = storeLayerRef.current
    if (!layer) return
    layer.clearLayers()

    stores.forEach(store => {
      const color = CHAIN_COLORS[store.chain.toLowerCase()] ?? '#6b7280'
      const icon = pinIcon(color)
      const distLabel = store.distance_km < 1
        ? `${Math.round(store.distance_km * 1000)} m away`
        : `${store.distance_km.toFixed(1)} km away`
      const popup = `
        <div style="min-width:160px">
          <p style="font-weight:600;margin:0 0 2px">${store.name}</p>
          <p style="color:#6b7280;font-size:12px;margin:0 0 4px">${[store.suburb, store.state].filter(Boolean).join(', ')}</p>
          <span style="background:${color};color:white;font-size:11px;padding:2px 8px;border-radius:9999px">${distLabel}</span>
        </div>`
      const marker = L.marker([store.lat, store.lon], { icon })
        .bindPopup(popup)
        .addTo(layer)

      if (onStoreClick) {
        marker.on('click', () => onStoreClick(store.id))
      }
    })
  }, [stores, onStoreClick])

  return (
    <div
      ref={containerRef}
      className="w-full rounded-xl overflow-hidden border border-gray-200 shadow-sm"
      style={{ height: '320px' }}
      aria-label="Map showing nearby grocery stores"
    />
  )
}
