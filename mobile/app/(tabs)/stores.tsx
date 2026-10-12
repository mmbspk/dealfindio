/**
 * Stores tab: find grocery stores near you on a map with a list below.
 *
 * States:
 *  1. prompt  — no location yet; user can tap "Find Stores Near Me" or enter a suburb/postcode
 *  2. loading — fetching location or store data
 *  3. results — MapView on top half, FlatList of store cards below
 */

import { useRef, useState } from 'react'
import {
  ActivityIndicator,
  FlatList,
  Keyboard,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from 'react-native'
import * as Location from 'expo-location'
import MapView, { Circle, Marker, PROVIDER_DEFAULT } from 'react-native-maps'
import apiClient from '@/api/client'

// ── Types ──────────────────────────────────────────────────────────────────────

interface StoreResult {
  id: string
  chain: string
  name: string
  address: string
  suburb: string
  state: string
  postcode: string
  lat: number
  lon: number
  distance_km: number
}

interface NearbyResponse {
  count: number
  stores: StoreResult[]
}

interface LatLon {
  latitude: number
  longitude: number
}

// ── Constants ──────────────────────────────────────────────────────────────────

const CHAINS = ['All', 'Coles', 'Woolworths', 'ALDI', 'IGA', 'Drakes'] as const
type ChainFilter = (typeof CHAINS)[number]

const RADIUS_OPTIONS = [2, 5, 10, 20] as const
type RadiusKm = (typeof RADIUS_OPTIONS)[number]

/** Normalised chain key → display label. */
const CHAIN_LABELS: Record<string, string> = {
  coles: 'Coles',
  woolworths: 'Woolworths',
  aldi: 'ALDI',
  iga: 'IGA',
  drakes: 'Drakes',
}

/** Chain → map pin colour. */
const PIN_COLORS: Record<string, string> = {
  coles: '#dc2626',       // red-600
  woolworths: '#16a34a',  // green-600
  aldi: '#2563eb',        // blue-600
  iga: '#f97316',         // orange-500
  drakes: '#ca8a04',      // yellow-600
}

/** Chain → card left-border colour (same hues, used in list). */
const BORDER_COLORS = PIN_COLORS

/** Chain filter label → API query param value. */
function chainParam(filter: ChainFilter): string | undefined {
  if (filter === 'All') return undefined
  return filter.toLowerCase()
}

// ── Component ──────────────────────────────────────────────────────────────────

export default function StoresScreen() {
  const mapRef = useRef<MapView>(null)

  const [screenState, setScreenState] = useState<'prompt' | 'loading' | 'results'>('prompt')
  const [userLocation, setUserLocation] = useState<LatLon | null>(null)
  const [stores, setStores] = useState<StoreResult[]>([])
  const [radiusKm, setRadiusKm] = useState<RadiusKm>(10)
  const [chainFilter, setChainFilter] = useState<ChainFilter>('All')
  const [error, setError] = useState<string | null>(null)
  const [manualQuery, setManualQuery] = useState('')
  const [geocoding, setGeocoding] = useState(false)
  const [locationDenied, setLocationDenied] = useState(false)

  // ── Fetching ────────────────────────────────────────────────────────────────

  async function fetchStores(location: LatLon, radius: RadiusKm, chain: ChainFilter) {
    setError(null)
    try {
      const params: Record<string, string | number> = {
        lat: location.latitude,
        lon: location.longitude,
        radius_km: radius,
        limit: 100,
      }
      const cp = chainParam(chain)
      if (cp) params.chain = cp

      const resp = await apiClient.get<NearbyResponse>('/api/v1/stores/nearby', { params })
      setStores(resp.data.stores ?? [])
    } catch {
      setError('Could not load stores. Check your connection and try again.')
      setStores([])
    } finally {
      setScreenState('results')
    }
  }

  // ── Location via device GPS ─────────────────────────────────────────────────

  async function handleFindNearMe() {
    setScreenState('loading')
    setError(null)

    const { status } = await Location.requestForegroundPermissionsAsync()
    if (status !== 'granted') {
      setLocationDenied(true)
      setScreenState('prompt')
      return
    }

    try {
      const loc = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced })
      const latlon: LatLon = {
        latitude: loc.coords.latitude,
        longitude: loc.coords.longitude,
      }
      setUserLocation(latlon)
      await fetchStores(latlon, radiusKm, chainFilter)
    } catch {
      setError('Could not determine your location. Try entering a suburb manually.')
      setScreenState('prompt')
    }
  }

  // ── Location via Nominatim geocoding ────────────────────────────────────────

  async function handleManualSearch() {
    const q = manualQuery.trim()
    if (!q) return
    Keyboard.dismiss()
    setGeocoding(true)
    setError(null)

    try {
      const url =
        `https://nominatim.openstreetmap.org/search` +
        `?q=${encodeURIComponent(q + ', Australia')}` +
        `&format=json&limit=1&countrycodes=au`

      const resp = await fetch(url, {
        headers: { 'User-Agent': 'dealfindio-mobile/1.0' },
      })
      const data = (await resp.json()) as { lat: string; lon: string }[]
      if (!data.length) {
        setError('Location not found. Try a different suburb or postcode.')
        return
      }

      const latlon: LatLon = {
        latitude: parseFloat(data[0].lat),
        longitude: parseFloat(data[0].lon),
      }
      setUserLocation(latlon)
      setScreenState('loading')
      await fetchStores(latlon, radiusKm, chainFilter)
    } catch {
      setError('Geocoding failed. Check your connection and try again.')
    } finally {
      setGeocoding(false)
    }
  }

  // ── Filter controls (re-fetch on change) ────────────────────────────────────

  async function handleRadiusChange(r: RadiusKm) {
    setRadiusKm(r)
    if (userLocation) {
      setScreenState('loading')
      await fetchStores(userLocation, r, chainFilter)
    }
  }

  async function handleChainChange(c: ChainFilter) {
    setChainFilter(c)
    if (userLocation) {
      setScreenState('loading')
      await fetchStores(userLocation, radiusKm, c)
    }
  }

  // ── Map camera ──────────────────────────────────────────────────────────────

  function focusStore(store: StoreResult) {
    mapRef.current?.animateToRegion(
      {
        latitude: store.lat,
        longitude: store.lon,
        latitudeDelta: 0.01,
        longitudeDelta: 0.01,
      },
      400
    )
  }

  // ── Render helpers ──────────────────────────────────────────────────────────

  function renderStoreCard({ item }: { item: StoreResult }) {
    const chain = item.chain.toLowerCase()
    const borderColor = BORDER_COLORS[chain] ?? '#164b40'
    const label = CHAIN_LABELS[chain] ?? item.chain

    return (
      <TouchableOpacity
        style={[styles.card, { borderLeftColor: borderColor }]}
        onPress={() => focusStore(item)}
        activeOpacity={0.75}
      >
        <View style={styles.cardBody}>
          <Text style={styles.cardName}>{item.name}</Text>
          <Text style={styles.cardSub}>
            {[item.suburb, item.state].filter(Boolean).join(', ')}
          </Text>
          {item.address ? (
            <Text style={styles.cardAddress} numberOfLines={1}>
              {item.address}
            </Text>
          ) : null}
        </View>
        <View style={styles.cardRight}>
          <View style={[styles.chainPill, { backgroundColor: borderColor }]}>
            <Text style={styles.chainPillText}>{label}</Text>
          </View>
          <Text style={styles.distBadge}>{item.distance_km.toFixed(1)} km</Text>
        </View>
      </TouchableOpacity>
    )
  }

  // ── Map region derived from location + radius ────────────────────────────────

  const mapDelta = radiusKm * 0.022
  const mapRegion = userLocation
    ? {
        latitude: userLocation.latitude,
        longitude: userLocation.longitude,
        latitudeDelta: mapDelta,
        longitudeDelta: mapDelta,
      }
    : undefined

  // ── Screens ─────────────────────────────────────────────────────────────────

  if (screenState === 'loading') {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color="#164b40" />
        <Text style={styles.loadingText}>Finding stores…</Text>
      </View>
    )
  }

  if (screenState === 'prompt') {
    return (
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView
          contentContainerStyle={styles.promptContainer}
          keyboardShouldPersistTaps="handled"
        >
          <Text style={styles.promptEmoji}>📍</Text>
          <Text style={styles.promptTitle}>Find Stores Near Me</Text>
          <Text style={styles.promptSub}>
            Discover nearby Coles, Woolworths, ALDI, IGA and Drakes stores.
          </Text>

          {locationDenied ? (
            <Text style={styles.permissionNote}>
              Location access was denied.{'\n'}Enable it in Settings or enter your suburb below.
            </Text>
          ) : null}

          {error ? <Text style={styles.errorText}>{error}</Text> : null}

          <TouchableOpacity
            style={styles.primaryBtn}
            onPress={handleFindNearMe}
            activeOpacity={0.85}
          >
            <Text style={styles.primaryBtnText}>Use My Location</Text>
          </TouchableOpacity>

          <Text style={styles.orLabel}>or enter a suburb / postcode</Text>

          <View style={styles.manualRow}>
            <TextInput
              style={styles.manualInput}
              placeholder="e.g. Bondi Junction or 2022"
              placeholderTextColor="#71877b"
              value={manualQuery}
              onChangeText={setManualQuery}
              onSubmitEditing={handleManualSearch}
              returnKeyType="search"
              autoCapitalize="words"
            />
            <Pressable
              style={[styles.manualBtn, geocoding && styles.manualBtnDisabled]}
              onPress={handleManualSearch}
              disabled={geocoding}
            >
              {geocoding ? (
                <ActivityIndicator size="small" color="#fff" />
              ) : (
                <Text style={styles.manualBtnText}>Search</Text>
              )}
            </Pressable>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    )
  }

  // ── Results: map + list ──────────────────────────────────────────────────────

  return (
    <View style={styles.flex}>
      {/* Map */}
      {mapRegion ? (
        <MapView
          ref={mapRef}
          style={styles.map}
          provider={PROVIDER_DEFAULT}
          initialRegion={mapRegion}
          showsUserLocation={false}
          showsMyLocationButton={false}
        >
          {/* Radius circle */}
          <Circle
            center={{ latitude: userLocation!.latitude, longitude: userLocation!.longitude }}
            radius={radiusKm * 1000}
            strokeColor="#164b40"
            strokeWidth={1.5}
            fillColor="rgba(79, 70, 229, 0.08)"
          />

          {/* User location marker */}
          <Marker
            coordinate={{ latitude: userLocation!.latitude, longitude: userLocation!.longitude }}
            title="You are here"
            pinColor="#164b40"
            zIndex={999}
          />

          {/* Store markers */}
          {stores.map((store) => (
            <Marker
              key={store.id}
              coordinate={{ latitude: store.lat, longitude: store.lon }}
              title={store.name}
              description={[store.suburb, store.state].filter(Boolean).join(', ')}
              pinColor={PIN_COLORS[store.chain.toLowerCase()] ?? '#5d7469'}
            />
          ))}
        </MapView>
      ) : (
        <View style={styles.mapPlaceholder}>
          <Text style={styles.mapPlaceholderText}>Map unavailable</Text>
        </View>
      )}

      {/* Controls bar */}
      <View style={styles.controlsBar}>
        {/* Radius presets */}
        <View style={styles.radiusRow}>
          <Text style={styles.controlLabel}>Radius:</Text>
          {RADIUS_OPTIONS.map((r) => (
            <TouchableOpacity
              key={r}
              style={[styles.radiusBtn, radiusKm === r && styles.radiusBtnActive]}
              onPress={() => handleRadiusChange(r)}
              activeOpacity={0.75}
            >
              <Text style={[styles.radiusBtnText, radiusKm === r && styles.radiusBtnTextActive]}>
                {r} km
              </Text>
            </TouchableOpacity>
          ))}
        </View>

        {/* Chain filter pills */}
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={styles.chainRow}
          keyboardShouldPersistTaps="handled"
        >
          {CHAINS.map((c) => (
            <TouchableOpacity
              key={c}
              style={[styles.chainPillFilter, chainFilter === c && styles.chainPillFilterActive]}
              onPress={() => handleChainChange(c)}
              activeOpacity={0.75}
            >
              <Text
                style={[
                  styles.chainPillFilterText,
                  chainFilter === c && styles.chainPillFilterTextActive,
                ]}
              >
                {c}
              </Text>
            </TouchableOpacity>
          ))}
        </ScrollView>
      </View>

      {/* Store list */}
      {error ? (
        <View style={styles.errorBanner}>
          <Text style={styles.errorBannerText}>{error}</Text>
          <TouchableOpacity
            onPress={() => userLocation && fetchStores(userLocation, radiusKm, chainFilter)}
          >
            <Text style={styles.retryText}>Retry</Text>
          </TouchableOpacity>
        </View>
      ) : null}

      {stores.length === 0 && !error ? (
        <View style={styles.emptyBox}>
          <Text style={styles.emptyText}>No stores found within {radiusKm} km.</Text>
          <Text style={styles.emptySub}>Try a larger radius or a different filter.</Text>
        </View>
      ) : (
        <FlatList
          data={stores}
          keyExtractor={(item) => item.id}
          renderItem={renderStoreCard}
          contentContainerStyle={styles.list}
          keyboardShouldPersistTaps="handled"
        />
      )}
    </View>
  )
}

// ── Styles ─────────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: '#f5f7f2' },

  // ── Shared loading / prompt ────────────────────────────────────────────────
  center: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#f5f7f2',
    gap: 12,
  },
  loadingText: { color: '#5d7469', fontSize: 15 },

  // ── Prompt screen ──────────────────────────────────────────────────────────
  promptContainer: {
    flexGrow: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 32,
    gap: 12,
  },
  promptEmoji: { fontSize: 52 },
  promptTitle: { fontSize: 22, fontWeight: '700', color: '#122923', textAlign: 'center' },
  promptSub: { fontSize: 14, color: '#5d7469', textAlign: 'center', marginBottom: 8 },
  permissionNote: {
    fontSize: 13,
    color: '#b45309',
    textAlign: 'center',
    backgroundColor: '#fef3c7',
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 12,
    overflow: 'hidden',
  },
  errorText: { color: '#ef4444', fontSize: 14, textAlign: 'center' },
  primaryBtn: {
    backgroundColor: '#164b40',
    borderRadius: 14,
    paddingHorizontal: 32,
    paddingVertical: 14,
    width: '100%',
    alignItems: 'center',
  },
  primaryBtnText: { color: '#fff', fontSize: 16, fontWeight: '700' },
  orLabel: { color: '#71877b', fontSize: 13, marginTop: 4 },
  manualRow: { flexDirection: 'row', width: '100%', gap: 8 },
  manualInput: {
    flex: 1,
    backgroundColor: '#fff',
    borderWidth: 1,
    borderColor: '#d1d5db',
    borderRadius: 12,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 15,
    color: '#122923',
  },
  manualBtn: {
    backgroundColor: '#164b40',
    borderRadius: 12,
    paddingHorizontal: 16,
    alignItems: 'center',
    justifyContent: 'center',
    minWidth: 72,
  },
  manualBtnDisabled: { opacity: 0.6 },
  manualBtnText: { color: '#fff', fontWeight: '600', fontSize: 14 },

  // ── Map ────────────────────────────────────────────────────────────────────
  map: {
    width: '100%',
    height: 280,
  },
  mapPlaceholder: {
    width: '100%',
    height: 280,
    backgroundColor: '#dce5dd',
    alignItems: 'center',
    justifyContent: 'center',
  },
  mapPlaceholderText: { color: '#71877b' },

  // ── Controls bar ──────────────────────────────────────────────────────────
  controlsBar: {
    backgroundColor: '#fff',
    borderBottomWidth: 1,
    borderBottomColor: '#dce5dd',
    paddingVertical: 8,
    paddingHorizontal: 12,
    gap: 6,
  },
  controlLabel: { fontSize: 12, color: '#5d7469', fontWeight: '500' },
  radiusRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  radiusBtn: {
    paddingHorizontal: 12,
    paddingVertical: 5,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: '#d1d5db',
    backgroundColor: '#fff',
  },
  radiusBtnActive: { backgroundColor: '#164b40', borderColor: '#164b40' },
  radiusBtnText: { fontSize: 12, color: '#29463a', fontWeight: '500' },
  radiusBtnTextActive: { color: '#fff' },

  chainRow: { flexDirection: 'row', gap: 6, paddingRight: 4 },
  chainPillFilter: {
    paddingHorizontal: 14,
    paddingVertical: 5,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: '#d1d5db',
    backgroundColor: '#fff',
  },
  chainPillFilterActive: { backgroundColor: '#164b40', borderColor: '#164b40' },
  chainPillFilterText: { fontSize: 12, color: '#29463a', fontWeight: '500' },
  chainPillFilterTextActive: { color: '#fff' },

  // ── Error banner ──────────────────────────────────────────────────────────
  errorBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    backgroundColor: '#fef2f2',
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: '#fecaca',
  },
  errorBannerText: { color: '#ef4444', fontSize: 13, flex: 1 },
  retryText: { color: '#164b40', fontWeight: '600', fontSize: 13, marginLeft: 12 },

  // ── Empty state ───────────────────────────────────────────────────────────
  emptyBox: {
    alignItems: 'center',
    paddingTop: 40,
    paddingHorizontal: 24,
  },
  emptyText: { fontSize: 16, fontWeight: '600', color: '#29463a', textAlign: 'center' },
  emptySub: { fontSize: 13, color: '#71877b', textAlign: 'center', marginTop: 6 },

  // ── Store list ────────────────────────────────────────────────────────────
  list: { padding: 12, gap: 8 },
  card: {
    backgroundColor: '#fff',
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#dce5dd',
    borderLeftWidth: 4,
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 12,
    paddingHorizontal: 14,
    marginBottom: 8,
    // iOS shadow
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.06,
    shadowRadius: 2,
    // Android elevation
    elevation: 1,
  },
  cardBody: { flex: 1 },
  cardName: { fontSize: 15, fontWeight: '600', color: '#122923' },
  cardSub: { fontSize: 13, color: '#5d7469', marginTop: 2 },
  cardAddress: { fontSize: 12, color: '#71877b', marginTop: 1 },
  cardRight: { alignItems: 'flex-end', gap: 6, marginLeft: 10 },
  chainPill: {
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 999,
    overflow: 'hidden',
  },
  chainPillText: { color: '#fff', fontSize: 11, fontWeight: '600' },
  distBadge: {
    fontSize: 13,
    fontWeight: '600',
    color: '#164b40',
  },
})
