import { useEffect, useState } from 'react'
import {
  ActivityIndicator,
  Alert,
  ScrollView,
  StyleSheet,
  Text,
  View,
  Pressable,
  Image,
} from 'react-native'
import { useLocalSearchParams, Stack } from 'expo-router'
import { apiClient } from '@/api/client'
import { useStore } from '@/store/store'

interface ProductDetail {
  id: string
  name: string
  brand: string | null
  category: string | null
  image_url: string | null
  gtin: string | null
}

interface StorePrice {
  store_id: string
  store_name: string
  chain: string
  suburb: string | null
  state: string | null
  price_aud: number
  was_price_aud: number | null
  discount_pct: number | null
}

export default function ProductDetailScreen() {
  const { id } = useLocalSearchParams<{ id: string }>()
  const { isAuthenticated } = useStore()

  const [product, setProduct] = useState<ProductDetail | null>(null)
  const [stores, setStores] = useState<StorePrice[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [addingWatchlist, setAddingWatchlist] = useState(false)
  const [inWatchlist, setInWatchlist] = useState(false)

  useEffect(() => {
    if (!id) return
    load()
  }, [id])

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const [productRes, storesRes] = await Promise.all([
        apiClient.get<ProductDetail>(`/api/v1/products/${id}`),
        apiClient.get<{ items: StorePrice[] } | StorePrice[]>(`/api/v1/products/${id}/stores`),
      ])
      setProduct(productRes.data)
      const storeList = Array.isArray(storesRes.data)
        ? storesRes.data
        : (storesRes.data as { items: StorePrice[] }).items ?? []
      setStores(storeList)
    } catch {
      setError('Could not load product. Check your connection.')
    } finally {
      setLoading(false)
    }
  }

  async function handleAddToWatchlist() {
    if (!isAuthenticated) {
      Alert.alert('Sign In Required', 'Sign in to add items to your watchlist.')
      return
    }
    setAddingWatchlist(true)
    try {
      await apiClient.post('/api/v1/watchlist', {
        product_id: id,
        alert_on_prediction: false,
      })
      setInWatchlist(true)
      Alert.alert('Added', `${product?.name ?? 'Product'} added to your watchlist.`)
    } catch (e: unknown) {
      const status = (e as { response?: { status?: number } })?.response?.status
      if (status === 409) {
        setInWatchlist(true)
        Alert.alert('Already in Watchlist', 'This product is already on your watchlist.')
      } else {
        Alert.alert('Error', 'Could not add to watchlist. Please try again.')
      }
    } finally {
      setAddingWatchlist(false)
    }
  }

  if (loading) {
    return (
      <View style={styles.center}>
        <Stack.Screen options={{ title: 'Product' }} />
        <ActivityIndicator size="large" color="#164b40" />
      </View>
    )
  }

  if (error || !product) {
    return (
      <View style={styles.center}>
        <Stack.Screen options={{ title: 'Product' }} />
        <Text style={styles.errorText}>{error ?? 'Product not found.'}</Text>
        <Pressable style={styles.retryBtn} onPress={load}>
          <Text style={styles.retryBtnText}>Retry</Text>
        </Pressable>
      </View>
    )
  }

  const lowestPrice = stores.length > 0
    ? Math.min(...stores.map(s => s.price_aud))
    : null

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Stack.Screen options={{ title: product.name }} />

      {/* Product image */}
      {product.image_url ? (
        <Image
          source={{ uri: product.image_url }}
          style={styles.image}
          resizeMode="contain"
        />
      ) : (
        <View style={[styles.image, styles.imagePlaceholder]}>
          <Text style={styles.imagePlaceholderText}>No image</Text>
        </View>
      )}

      {/* Product header */}
      <View style={styles.header}>
        <Text style={styles.name}>{product.name}</Text>
        {product.brand && <Text style={styles.brand}>{product.brand}</Text>}
        {product.category && <Text style={styles.category}>{product.category}</Text>}
        {lowestPrice != null && (
          <Text style={styles.lowestPrice}>
            From ${lowestPrice.toFixed(2)}
          </Text>
        )}
      </View>

      {/* Add to Watchlist */}
      <Pressable
        style={[styles.watchlistBtn, inWatchlist && styles.watchlistBtnAdded]}
        onPress={handleAddToWatchlist}
        disabled={addingWatchlist || inWatchlist}
      >
        {addingWatchlist ? (
          <ActivityIndicator size="small" color="#fff" />
        ) : (
          <Text style={styles.watchlistBtnText}>
            {inWatchlist ? '✓ In Watchlist' : '+ Add to Watchlist'}
          </Text>
        )}
      </Pressable>

      {/* Store prices */}
      {stores.length > 0 && (
        <View style={styles.storesSection}>
          <Text style={styles.sectionTitle}>Store Prices</Text>
          {stores.map((s) => (
            <View key={s.store_id} style={[styles.storeRow, s.price_aud === lowestPrice && styles.bestStoreRow]}>
              <View style={styles.storeInfo}>
                <Text style={styles.storeName}>{s.store_name}</Text>
                {s.price_aud === lowestPrice && <Text style={styles.bestStoreLabel}>BEST PRICE</Text>}
                <Text style={styles.storeLocation}>
                  {[s.chain, s.suburb, s.state].filter(Boolean).join(' · ')}
                </Text>
              </View>
              <View style={styles.storePriceWrap}>
                <Text style={styles.storePrice}>${s.price_aud.toFixed(2)}</Text>
                {s.was_price_aud != null && (
                  <Text style={styles.wasPrice}>${s.was_price_aud.toFixed(2)}</Text>
                )}
                {s.discount_pct != null && s.discount_pct > 0 && (
                  <View style={styles.discountBadge}>
                    <Text style={styles.discountText}>{Math.round(s.discount_pct)}% off</Text>
                  </View>
                )}
              </View>
            </View>
          ))}
        </View>
      )}
    </ScrollView>
  )
}

const styles = StyleSheet.create({
  bestStoreRow: { borderColor: '#8cbf9f', backgroundColor: '#f0f8ec' },
  bestStoreLabel: { color: '#176f50', fontSize: 11, fontWeight: '800', marginTop: 4, letterSpacing: 1 },
  container: { flex: 1, backgroundColor: '#f5f7f2' },
  content: { paddingBottom: 40 },
  center: { flex: 1, justifyContent: 'center', alignItems: 'center', padding: 24 },
  errorText: { color: '#ef4444', textAlign: 'center', marginBottom: 16 },
  retryBtn: { backgroundColor: '#164b40', borderRadius: 12, paddingHorizontal: 20, paddingVertical: 10 },
  retryBtnText: { color: '#fff', fontWeight: '600' },

  image: {
    width: '100%',
    height: 220,
    backgroundColor: '#fff',
  },
  imagePlaceholder: {
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: '#edf2e9',
  },
  imagePlaceholderText: { color: '#71877b', fontSize: 14 },

  header: {
    backgroundColor: '#fff',
    padding: 20,
    borderBottomWidth: 1,
    borderBottomColor: '#dce5dd',
  },
  name: { fontSize: 20, fontWeight: '700', color: '#122923' },
  brand: { fontSize: 14, color: '#164b40', marginTop: 4 },
  category: { fontSize: 13, color: '#71877b', marginTop: 2 },
  lowestPrice: { fontSize: 22, fontWeight: '700', color: '#176f50', marginTop: 10 },

  watchlistBtn: {
    margin: 16,
    backgroundColor: '#164b40',
    borderRadius: 14,
    paddingVertical: 14,
    alignItems: 'center',
  },
  watchlistBtnAdded: { backgroundColor: '#176f50' },
  watchlistBtnText: { color: '#fff', fontSize: 16, fontWeight: '700' },

  storesSection: { marginHorizontal: 16 },
  sectionTitle: { fontSize: 16, fontWeight: '700', color: '#122923', marginBottom: 10 },
  storeRow: {
    backgroundColor: '#fff',
    borderRadius: 12,
    padding: 14,
    marginBottom: 8,
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    borderWidth: 1,
    borderColor: '#dce5dd',
  },
  storeInfo: { flex: 1, marginRight: 12 },
  storeName: { fontSize: 15, fontWeight: '600', color: '#122923' },
  storeLocation: { fontSize: 12, color: '#5d7469', marginTop: 2 },
  storePriceWrap: { alignItems: 'flex-end' },
  storePrice: { fontSize: 18, fontWeight: '700', color: '#122923' },
  wasPrice: { fontSize: 13, color: '#71877b', textDecorationLine: 'line-through', marginTop: 1 },
  discountBadge: {
    backgroundColor: '#fef3c7',
    borderRadius: 4,
    paddingHorizontal: 6,
    paddingVertical: 2,
    marginTop: 3,
  },
  discountText: { color: '#d97706', fontSize: 11, fontWeight: '700' },
})
