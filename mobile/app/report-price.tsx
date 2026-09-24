/**
 * Report-price form.
 *
 * Reached from the barcode scanner (or manual search) with a resolved product.
 * Captures the user's current location for geofence validation and submits a
 * community price report to the backend.
 */

import { useEffect, useState } from 'react'
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native'
import { router, useLocalSearchParams } from 'expo-router'
import * as Location from 'expo-location'
import apiClient from '@/api/client'
import type { PriceReportSubmission } from '@/types'

export default function ReportPriceScreen() {
  const params = useLocalSearchParams<{
    productId?: string
    productName?: string
    gtin?: string
    storeId?: string
  }>()

  const [price, setPrice] = useState('')
  const [wasPrice, setWasPrice] = useState('')
  const [coords, setCoords] = useState<{ lat: number; lon: number } | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    void captureLocation()
  }, [])

  async function captureLocation() {
    const { status } = await Location.requestForegroundPermissionsAsync()
    if (status !== 'granted') {
      setMessage('Location is required to report a price near a store.')
      return
    }
    const loc = await Location.getCurrentPositionAsync({})
    setCoords({ lat: loc.coords.latitude, lon: loc.coords.longitude })
  }

  async function handleSubmit() {
    const priceValue = parseFloat(price)
    if (isNaN(priceValue) || priceValue <= 0) {
      setMessage('Enter a valid price.')
      return
    }
    if (!coords) {
      setMessage('Waiting for location…')
      return
    }
    if (!params.storeId) {
      setMessage('No store selected. Pick a store first.')
      return
    }

    setSubmitting(true)
    setMessage(null)
    try {
      const payload: PriceReportSubmission = {
        product_gtin: params.gtin,
        product_name: params.productName,
        store_id: params.storeId,
        price_cents: Math.round(priceValue * 100),
        was_price_cents: wasPrice ? Math.round(parseFloat(wasPrice) * 100) : undefined,
        latitude: coords.lat,
        longitude: coords.lon,
      }
      await apiClient.post('/api/v1/price-reports/', payload)
      setMessage('✓ Price reported. Thanks for contributing!')
      setTimeout(() => router.back(), 1200)
    } catch (e: any) {
      const detail = e?.response?.data?.detail
      setMessage(detail ?? 'Failed to submit. Try again.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <View style={styles.container}>
      <Text style={styles.product}>{params.productName ?? 'Product'}</Text>

      <Text style={styles.label}>Current price (AUD)</Text>
      <TextInput
        style={styles.input}
        placeholder="3.50"
        keyboardType="decimal-pad"
        value={price}
        onChangeText={setPrice}
      />

      <Text style={styles.label}>Was price (optional)</Text>
      <TextInput
        style={styles.input}
        placeholder="5.00"
        keyboardType="decimal-pad"
        value={wasPrice}
        onChangeText={setWasPrice}
      />

      <Text style={styles.geo}>
        {coords
          ? '📍 Location captured'
          : '📍 Getting your location…'}
      </Text>

      <Pressable
        style={[styles.submit, submitting && styles.submitDisabled]}
        onPress={handleSubmit}
        disabled={submitting}
      >
        {submitting ? (
          <ActivityIndicator color="#fff" />
        ) : (
          <Text style={styles.submitText}>Submit Report</Text>
        )}
      </Pressable>

      {message ? <Text style={styles.message}>{message}</Text> : null}
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f5f7f2', padding: 20 },
  product: { fontSize: 20, fontWeight: '700', color: '#122923', marginBottom: 16 },
  label: { fontSize: 14, color: '#5d7469', marginTop: 12, marginBottom: 4 },
  input: {
    backgroundColor: '#fff',
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#dce5dd',
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 16,
  },
  geo: { marginTop: 16, color: '#5d7469', fontSize: 14 },
  submit: {
    backgroundColor: '#164b40',
    borderRadius: 12,
    padding: 16,
    alignItems: 'center',
    marginTop: 24,
  },
  submitDisabled: { opacity: 0.6 },
  submitText: { color: '#fff', fontWeight: '600', fontSize: 16 },
  message: { marginTop: 16, textAlign: 'center', color: '#122923' },
})
