/**
 * Deals tab: nearby deals based on the user's location.
 */

import { useEffect, useState } from 'react'
import {
  ActivityIndicator,
  FlatList,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from 'react-native'
import * as Location from 'expo-location'
import apiClient from '@/api/client'

interface Deal {
  id: string
  product_id: string
  product_name: string
  brand: string | null
  category: string | null
  store_id: string
  store_name: string
  chain: string
  suburb: string | null
  state: string | null
  price_aud: number
  was_price_aud: number | null
  discount_pct: number | null
  is_verified: boolean
  distance_km: number | null
}

export default function DealsScreen() {
  const [deals, setDeals] = useState<Deal[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    void loadDeals()
  }, [])

  async function loadDeals() {
    setLoading(true)
    setError(null)
    try {
      const { status } = await Location.requestForegroundPermissionsAsync()
      if (status !== 'granted') {
        // Fall back to browsing all deals without geo filter
        const resp = await apiClient.get<Deal[]>('/api/v1/deals/feed', {
          params: { limit: 50 },
        })
        setDeals(resp.data)
        return
      }

      const loc = await Location.getCurrentPositionAsync({})
      const resp = await apiClient.get<Deal[]>('/api/v1/deals/feed', {
        params: {
          lat: loc.coords.latitude,
          lon: loc.coords.longitude,
          radius_km: 10,
          limit: 50,
        },
      })
      setDeals(resp.data)
    } catch {
      setError('Could not load deals. Pull to retry.')
    } finally {
      setLoading(false)
    }
  }

  function renderDeal({ item }: { item: Deal }) {
    const pctOff = item.discount_pct
      ? Math.round(item.discount_pct)
      : item.was_price_aud && item.was_price_aud > 0
      ? Math.round(((item.was_price_aud - item.price_aud) / item.was_price_aud) * 100)
      : null

    const storeLine = [item.store_name, item.suburb, item.state].filter(Boolean).join(', ')
    const distLine = item.distance_km != null ? ` · ${item.distance_km.toFixed(1)} km away` : ''

    return (
      <View style={styles.card}>
        <Text style={styles.name}>{item.product_name}</Text>
        {item.brand ? <Text style={styles.brand}>{item.brand}</Text> : null}
        <Text style={styles.store}>{storeLine}{distLine}</Text>
        <View style={styles.priceRow}>
          <Text style={styles.price}>${item.price_aud.toFixed(2)}</Text>
          {item.was_price_aud ? (
            <Text style={styles.wasPrice}>${item.was_price_aud.toFixed(2)}</Text>
          ) : null}
          {pctOff ? <Text style={styles.badge}>{pctOff}% off</Text> : null}
          {item.is_verified ? <Text style={styles.verifiedBadge}>Verified</Text> : null}
        </View>
      </View>
    )
  }

  if (loading) {
    return <ActivityIndicator size="large" color="#4f46e5" style={styles.spinner} />
  }

  return (
    <View style={styles.container}>
      {error ? (
        <View style={styles.center}>
          <Text style={styles.error}>{error}</Text>
          <TouchableOpacity style={styles.retryBtn} onPress={loadDeals}>
            <Text style={styles.retryText}>Retry</Text>
          </TouchableOpacity>
        </View>
      ) : deals.length === 0 ? (
        <Text style={styles.empty}>No nearby deals right now. Check back soon!</Text>
      ) : (
        <FlatList
          data={deals}
          keyExtractor={(item) => item.id}
          renderItem={renderDeal}
          contentContainerStyle={styles.list}
          onRefresh={loadDeals}
          refreshing={loading}
        />
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f9fafb' },
  spinner: { flex: 1, backgroundColor: '#f9fafb' },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24 },
  error: { color: '#ef4444', textAlign: 'center', marginBottom: 16 },
  retryBtn: {
    backgroundColor: '#4f46e5',
    paddingHorizontal: 20,
    paddingVertical: 10,
    borderRadius: 8,
  },
  retryText: { color: '#fff', fontWeight: '600' },
  empty: { color: '#6b7280', textAlign: 'center', marginTop: 40, paddingHorizontal: 24 },
  list: { padding: 12 },
  card: {
    backgroundColor: '#fff',
    borderRadius: 8,
    padding: 14,
    marginBottom: 8,
    borderWidth: 1,
    borderColor: '#e5e7eb',
  },
  name: { fontSize: 16, fontWeight: '600', color: '#111827' },
  brand: { fontSize: 12, color: '#9ca3af', marginTop: 1 },
  store: { fontSize: 13, color: '#6b7280', marginTop: 2 },
  priceRow: { flexDirection: 'row', alignItems: 'center', marginTop: 8, gap: 8, flexWrap: 'wrap' },
  price: { fontSize: 20, fontWeight: '700', color: '#10b981' },
  wasPrice: {
    fontSize: 14,
    color: '#9ca3af',
    textDecorationLine: 'line-through',
  },
  badge: {
    backgroundColor: '#fef3c7',
    color: '#b45309',
    fontSize: 12,
    fontWeight: '600',
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 4,
    overflow: 'hidden',
  },
  verifiedBadge: {
    backgroundColor: '#d1fae5',
    color: '#065f46',
    fontSize: 11,
    fontWeight: '600',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 4,
    overflow: 'hidden',
  },
})
