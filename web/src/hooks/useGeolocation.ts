import { useState, useCallback, useEffect } from 'react'

export type GeolocationStatus = 'idle' | 'requesting' | 'granted' | 'denied' | 'unavailable' | 'error'

export interface GeolocationPosition {
  lat: number
  lon: number
  accuracy: number
  label?: string
}

export interface UseGeolocationReturn {
  position: GeolocationPosition | null
  status: GeolocationStatus
  error: string | null
  requestLocation: () => void
  clearLocation: () => void
}

const STORAGE_KEY = 'dealfindio_location'

async function reverseGeocode(lat: number, lon: number): Promise<string | null> {
  try {
    const url = `https://nominatim.openstreetmap.org/reverse?lat=${lat}&lon=${lon}&format=json&accept-language=en`
    const r = await fetch(url, { headers: { 'Accept-Language': 'en' } })
    if (!r.ok) return null
    const data = await r.json()
    const addr = data.address
    if (!addr) return null
    // Prefer: suburb/town + state abbreviation (e.g. "Norwood SA")
    const locality = addr.suburb || addr.town || addr.village || addr.city_district || addr.city || addr.county
    const state = addr.state
    if (locality && state) return `${locality}, ${stateAbbr(state)}`
    if (locality) return locality
    if (data.display_name) return data.display_name.split(',').slice(0, 2).join(',').trim()
    return null
  } catch {
    return null
  }
}

function stateAbbr(state: string): string {
  const map: Record<string, string> = {
    'New South Wales': 'NSW', 'Victoria': 'VIC', 'Queensland': 'QLD',
    'South Australia': 'SA', 'Western Australia': 'WA', 'Tasmania': 'TAS',
    'Australian Capital Territory': 'ACT', 'Northern Territory': 'NT',
  }
  return map[state] ?? state
}

function loadStoredPosition(): GeolocationPosition | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    if (typeof parsed.lat === 'number' && typeof parsed.lon === 'number') {
      return parsed as GeolocationPosition
    }
  } catch {}
  return null
}

export function useGeolocation(): UseGeolocationReturn {
  const [position, setPosition] = useState<GeolocationPosition | null>(() => loadStoredPosition())
  const [status, setStatus] = useState<GeolocationStatus>(() => loadStoredPosition() ? 'granted' : 'idle')
  const [error, setError] = useState<string | null>(null)

  // Keep localStorage in sync whenever position changes
  useEffect(() => {
    if (position) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(position))
    } else {
      localStorage.removeItem(STORAGE_KEY)
    }
  }, [position])

  // Enrich a stored position that has no label yet (e.g. loaded from an old localStorage entry)
  useEffect(() => {
    if (position && !position.label) {
      reverseGeocode(position.lat, position.lon).then(label => {
        if (label) setPosition(prev => prev ? { ...prev, label } : prev)
      })
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Listen for cross-component writes (e.g. Stores page manual geocode)
  useEffect(() => {
    const handler = (e: StorageEvent) => {
      if (e.key !== STORAGE_KEY) return
      if (e.newValue) {
        try {
          const parsed = JSON.parse(e.newValue)
          if (typeof parsed.lat === 'number' && typeof parsed.lon === 'number') {
            setPosition(parsed)
            setStatus('granted')
          }
        } catch {}
      } else {
        setPosition(null)
        setStatus('idle')
      }
    }
    window.addEventListener('storage', handler)
    return () => window.removeEventListener('storage', handler)
  }, [])

  const requestLocation = useCallback(() => {
    if (!navigator.geolocation) {
      setStatus('unavailable')
      setError('Geolocation is not supported by your browser.')
      return
    }

    setStatus('requesting')
    setError(null)

    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const lat = pos.coords.latitude
        const lon = pos.coords.longitude
        const accuracy = pos.coords.accuracy
        // Set position immediately with no label, then enrich with reverse geocode
        const base: GeolocationPosition = { lat, lon, accuracy }
        setPosition(base)
        setStatus('granted')
        setError(null)
        // Reverse geocode in background and update label
        reverseGeocode(lat, lon).then(label => {
          if (label) {
            setPosition({ lat, lon, accuracy, label })
          }
        })
      },
      (err) => {
        if (err.code === err.PERMISSION_DENIED) {
          setStatus('denied')
          setError('Location permission denied. Enable it in your browser settings.')
        } else if (err.code === err.POSITION_UNAVAILABLE) {
          setStatus('error')
          setError('Location unavailable. Check your device settings.')
        } else if (err.code === err.TIMEOUT) {
          setStatus('error')
          setError('Location request timed out. Please try again.')
        } else {
          setStatus('error')
          setError('Failed to get your location.')
        }
      },
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 }
    )
  }, [])

  const clearLocation = useCallback(() => {
    setPosition(null)
    setStatus('idle')
    setError(null)
  }, [])

  return { position, status, error, requestLocation, clearLocation }
}
