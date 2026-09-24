/**
 * Products tab: search + browse all products with images, prices, filters,
 * and save-to-watchlist. Mirrors the web Products page.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Image,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native'
import { router } from 'expo-router'
import * as Location from 'expo-location'
import apiClient from '@/api/client'
import { useStore } from '@/store/store'
import type { Product } from '@/types'

const CATEGORIES = ['All', 'Dairy', 'Bakery', 'Meat', 'Fruit & Veg', 'Snacks', 'Beverages', 'Pantry', 'Frozen', 'Personal Care']

interface ProductWithPrice extends Product {
  latest_price_cents?: number
  sale_pct_off?: number
  store_name?: string
  is_on_sale?: boolean
  image_url?: string | null
}

function PriceTag({ price, pctOff }: { price: number; pctOff?: number }) {
  return (
    <View style={styles.priceRow}>
      <Text style={styles.price}>${(price / 100).toFixed(2)}</Text>
      {pctOff && pctOff > 0 ? (
        <View style={styles.badge}>
          <Text style={styles.badgeText}>-{Math.round(pctOff)}%</Text>
        </View>
      ) : null}
    </View>
  )
}

export default function ProductsScreen() {
  const { query, setQuery, results, setResults, searchLoading, setSearchLoading } = useStore()
  const [localQuery, setLocalQuery] = useState(query)
  const [products, setProducts] = useState<ProductWithPrice[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedCategory, setSelectedCategory] = useState('All')
  const [onSaleOnly, setOnSaleOnly] = useState(false)
  const [addingId, setAddingId] = useState<string | null>(null)
  const [addedIds, setAddedIds] = useState<Set<string>>(new Set())
  const coordsRef = useRef<{ lat: number; lon: number } | null>(null)
  const inputRef = useRef<TextInput>(null)

  useEffect(() => {
    ;(async () => {
      const { status } = await Location.requestForegroundPermissionsAsync()
      if (status !== 'granted') return
      try {
        const loc = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced })
        coordsRef.current = { lat: loc.coords.latitude, lon: loc.coords.longitude }
      } catch {}
    })()
    loadProducts('')
  }, [])

  const loadProducts = useCallback(async (q: string) => {
    setLoading(true)
    setError(null)
    try {
      const params: Record<string, string | number> = { limit: 60 }
      if (q.trim()) params.q = q.trim()
      if (coordsRef.current) {
        params.lat = coordsRef.current.lat
        params.lon = coordsRef.current.lon
        params.radius_km = 25
      }
      const resp = await apiClient.get<ProductWithPrice[]>('/api/v1/products/search', { params })
      setProducts(resp.data)
    } catch {
      setError('Failed to load products. Check your connection.')
      setProducts([])
    } finally {
      setLoading(false)
    }
  }, [])

  function handleSearch() {
    setQuery(localQuery)
    loadProducts(localQuery)
  }

  async function handleSave(product: ProductWithPrice) {
    if (addedIds.has(product.id)) return
    setAddingId(product.id)
    try {
      await apiClient.post('/api/v1/watchlist', { product_id: product.id, alert_on_prediction: false })
      setAddedIds((prev) => new Set(prev).add(product.id))
    } catch (e: any) {
      const status = e?.response?.status
      if (status === 409) setAddedIds((prev) => new Set(prev).add(product.id))
      else if (status === 401) Alert.alert('Sign In Required', 'Sign in to save products.')
      else Alert.alert('Error', 'Could not save. Please try again.')
    } finally {
      setAddingId(null)
    }
  }

  const filteredProducts = products.filter((p) => {
    if (selectedCategory !== 'All') {
      const cat = (p.category ?? '').toLowerCase()
      if (!cat.includes(selectedCategory.toLowerCase())) return false
    }
    if (onSaleOnly && !p.is_on_sale) return false
    return true
  })

  function renderProduct({ item }: { item: ProductWithPrice }) {
    const isSaving = addingId === item.id
    const saved = addedIds.has(item.id)

    return (
      <Pressable
        style={styles.card}
        onPress={() => router.push(`/product/${item.id}`)}
        accessibilityRole="button"
      >
        {/* Image */}
        <View style={styles.imageContainer}>
          {item.image_url ? (
            <Image source={{ uri: item.image_url }} style={styles.image} resizeMode="contain" />
          ) : (
            <View style={[styles.image, styles.imagePlaceholder]}>
              <Text style={styles.imagePlaceholderText}>🛒</Text>
            </View>
          )}
          {item.is_on_sale && item.sale_pct_off ? (
            <View style={styles.salePill}>
              <Text style={styles.salePillText}>-{Math.round(item.sale_pct_off)}%</Text>
            </View>
          ) : null}
        </View>

        {/* Info */}
        <View style={styles.cardBody}>
          <Text style={styles.brand} numberOfLines={1}>{item.brand ?? ''}</Text>
          <Text style={styles.name} numberOfLines={2}>{item.name}</Text>
          {item.latest_price_cents ? (
            <PriceTag price={item.latest_price_cents} pctOff={item.sale_pct_off} />
          ) : null}
          {item.store_name ? (
            <Text style={styles.storeName} numberOfLines={1}>{item.store_name}</Text>
          ) : null}
        </View>

        {/* Save button */}
        <Pressable
          style={[styles.saveBtn, saved && styles.saveBtnDone]}
          onPress={(e) => { e.stopPropagation?.(); handleSave(item) }}
          disabled={saved || isSaving}
          accessibilityLabel={saved ? 'Saved' : 'Save to watchlist'}
          hitSlop={8}
        >
          {isSaving ? (
            <ActivityIndicator size="small" color="#164b40" />
          ) : (
            <Text style={[styles.saveBtnText, saved && styles.saveBtnTextDone]}>
              {saved ? '✓' : '+'}
            </Text>
          )}
        </Pressable>
      </Pressable>
    )
  }

  return (
    <View style={styles.container}>
      <View style={styles.intro}>
        <Text style={styles.introEyebrow}>SHOP SMARTER</Text>
        <Text style={styles.introTitle}>Compare your everyday groceries.</Text>
        <Pressable onPress={() => router.push('/(tabs)/deals')} accessibilityRole="link">
          <Text style={styles.introLink}>See nearby deals →</Text>
        </Pressable>
      </View>
      {/* Search bar */}
      <View style={styles.searchRow}>
        <TextInput
          ref={inputRef}
          style={styles.input}
          placeholder="Search products…"
          value={localQuery}
          onChangeText={setLocalQuery}
          onSubmitEditing={handleSearch}
          returnKeyType="search"
          autoCapitalize="none"
          clearButtonMode="while-editing"
        />
        <Pressable style={styles.searchBtn} onPress={handleSearch}>
          <Text style={styles.searchBtnText}>Search</Text>
        </Pressable>
      </View>

      {/* Filter row */}
      <View style={styles.filterRow}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filterScroll}>
          <Pressable
            style={[styles.filterChip, onSaleOnly && styles.filterChipActive]}
            onPress={() => setOnSaleOnly(!onSaleOnly)}
          >
            <Text style={[styles.filterChipText, onSaleOnly && styles.filterChipTextActive]}>On Sale</Text>
          </Pressable>
          {CATEGORIES.map((cat) => (
            <Pressable
              key={cat}
              style={[styles.filterChip, selectedCategory === cat && styles.filterChipActive]}
              onPress={() => setSelectedCategory(cat)}
            >
              <Text style={[styles.filterChipText, selectedCategory === cat && styles.filterChipTextActive]}>
                {cat}
              </Text>
            </Pressable>
          ))}
        </ScrollView>
      </View>

      {/* Results */}
      {loading ? (
        <ActivityIndicator size="large" color="#164b40" style={styles.spinner} />
      ) : error ? (
        <Text style={styles.errorText}>{error}</Text>
      ) : filteredProducts.length === 0 ? (
        <View style={styles.emptyState}>
          <Text style={styles.emptyText}>
            {products.length > 0 ? 'No products match your filters.' : 'Search for a product above.'}
          </Text>
        </View>
      ) : (
        <FlatList
          data={filteredProducts}
          keyExtractor={(item) => item.id}
          renderItem={renderProduct}
          contentContainerStyle={styles.list}
          numColumns={2}
          columnWrapperStyle={styles.row}
          showsVerticalScrollIndicator={false}
        />
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  intro: { backgroundColor: '#164b40', paddingHorizontal: 20, paddingTop: 20, paddingBottom: 17 },
  introEyebrow: { color: '#ddf67a', fontSize: 11, fontWeight: '800', letterSpacing: 2 },
  introTitle: { color: '#fff', fontSize: 25, lineHeight: 30, fontWeight: '700', maxWidth: 300, marginTop: 5 },
  introLink: { color: '#ddf67a', fontSize: 14, fontWeight: '700', marginTop: 10 },
  container: { flex: 1, backgroundColor: '#f5f7f2' },
  searchRow: { flexDirection: 'row', padding: 12, gap: 8, backgroundColor: '#fff', borderBottomWidth: 1, borderBottomColor: '#edf2e9' },
  input: {
    flex: 1,
    backgroundColor: '#f5f7f2',
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#dce5dd',
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 16,
  },
  searchBtn: { backgroundColor: '#164b40', borderRadius: 12, paddingHorizontal: 16, justifyContent: 'center' },
  searchBtnText: { color: '#fff', fontWeight: '600', fontSize: 14 },
  filterRow: { backgroundColor: '#fff', borderBottomWidth: 1, borderBottomColor: '#edf2e9' },
  filterScroll: { paddingHorizontal: 12, paddingVertical: 8, gap: 8 },
  filterChip: {
    paddingHorizontal: 12, paddingVertical: 6,
    borderRadius: 20, borderWidth: 1, borderColor: '#dce5dd',
    backgroundColor: '#fff', marginRight: 6,
  },
  filterChipActive: { backgroundColor: '#e9f3eb', borderColor: '#176f50' },
  filterChipText: { fontSize: 13, color: '#5d7469', fontWeight: '500' },
  filterChipTextActive: { color: '#164b40', fontWeight: '600' },
  spinner: { marginTop: 48 },
  errorText: { color: '#ef4444', textAlign: 'center', marginTop: 40, paddingHorizontal: 24, fontSize: 14 },
  emptyState: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  emptyText: { color: '#71877b', textAlign: 'center', fontSize: 15, paddingHorizontal: 32 },
  list: { padding: 10 },
  row: { justifyContent: 'space-between' },
  card: {
    width: '48%',
    backgroundColor: '#fff',
    borderRadius: 12,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: '#edf2e9',
    overflow: 'hidden',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.04,
    shadowRadius: 4,
    elevation: 1,
  },
  imageContainer: { position: 'relative', width: '100%', aspectRatio: 1, backgroundColor: '#f5f7f2' },
  image: { width: '100%', height: '100%' },
  imagePlaceholder: { alignItems: 'center', justifyContent: 'center', backgroundColor: '#edf2e9' },
  imagePlaceholderText: { fontSize: 32 },
  salePill: {
    position: 'absolute', top: 6, left: 6,
    backgroundColor: '#ef4444', borderRadius: 14,
    paddingHorizontal: 6, paddingVertical: 2,
  },
  salePillText: { color: '#fff', fontSize: 11, fontWeight: '700' },
  cardBody: { padding: 10, paddingBottom: 6 },
  brand: { fontSize: 11, color: '#176f50', fontWeight: '600', marginBottom: 1 },
  name: { fontSize: 14, fontWeight: '600', color: '#122923', lineHeight: 18 },
  priceRow: { flexDirection: 'row', alignItems: 'center', gap: 4, marginTop: 4 },
  price: { fontSize: 15, fontWeight: '700', color: '#176f50' },
  badge: { backgroundColor: '#fee2e2', borderRadius: 12, paddingHorizontal: 4, paddingVertical: 1 },
  badgeText: { fontSize: 10, fontWeight: '700', color: '#dc2626' },
  storeName: { fontSize: 12, color: '#71877b', marginTop: 2 },
  saveBtn: {
    marginHorizontal: 10,
    marginBottom: 10,
    backgroundColor: '#e9f3eb',
    borderRadius: 12,
    paddingVertical: 7,
    alignItems: 'center',
    justifyContent: 'center',
  },
  saveBtnDone: { backgroundColor: '#d1fae5' },
  saveBtnText: { fontSize: 16, fontWeight: '700', color: '#164b40' },
  saveBtnTextDone: { color: '#065f46' },
})
