/**
 * Barcode scanner screen.
 *
 * Uses expo-camera to scan a product barcode (EAN/UPC), looks the product up
 * by GTIN, then routes to the report-price form with the resolved product.
 */

import { useState } from 'react'
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native'
import { CameraView, useCameraPermissions } from 'expo-camera'
import { router } from 'expo-router'
import apiClient from '@/api/client'
import type { Product } from '@/types'

export default function ScanScreen() {
  const [permission, requestPermission] = useCameraPermissions()
  const [scanned, setScanned] = useState(false)
  const [lookingUp, setLookingUp] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  async function handleScanned({ data }: { data: string }) {
    if (scanned) return
    setScanned(true)
    setLookingUp(true)
    setMessage(`Looking up barcode ${data}...`)
    try {
      const resp = await apiClient.get<Product[]>('/api/v1/products/search', {
        params: { gtin: data, limit: 1 },
      })
      if (resp.data.length > 0) {
        const product = resp.data[0]
        router.replace({
          pathname: '/report-price',
          params: { productId: product.id, productName: product.name, gtin: data },
        })
      } else {
        setMessage(`No product found for barcode ${data}. Try manual search.`)
      }
    } catch {
      setMessage('Lookup failed. Check your connection.')
    } finally {
      setLookingUp(false)
    }
  }

  if (!permission) {
    return <ActivityIndicator size="large" color="#164b40" style={styles.center} />
  }

  if (!permission.granted) {
    return (
      <View style={styles.center}>
        <Text style={styles.info}>
          Camera access is needed to scan barcodes.
        </Text>
        <Pressable style={styles.btn} onPress={requestPermission}>
          <Text style={styles.btnText}>Grant Permission</Text>
        </Pressable>
      </View>
    )
  }

  return (
    <View style={styles.container}>
      <CameraView
        style={StyleSheet.absoluteFill}
        barcodeScannerSettings={{
          barcodeTypes: ['ean13', 'ean8', 'upc_a', 'upc_e'],
        }}
        onBarcodeScanned={scanned ? undefined : handleScanned}
      />
      <View style={styles.overlay}>
        <View style={styles.reticle} />
        {lookingUp ? (
          <ActivityIndicator size="large" color="#fff" />
        ) : (
          <Text style={styles.hint}>{message ?? 'Point at a barcode'}</Text>
        )}
        {scanned && !lookingUp ? (
          <Pressable
            style={styles.btn}
            onPress={() => {
              setScanned(false)
              setMessage(null)
            }}
          >
            <Text style={styles.btnText}>Scan Again</Text>
          </Pressable>
        ) : null}
      </View>
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#000' },
  center: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    padding: 24,
    backgroundColor: '#f5f7f2',
  },
  overlay: {
    ...StyleSheet.absoluteFillObject,
    justifyContent: 'center',
    alignItems: 'center',
    gap: 20,
  },
  reticle: {
    width: 240,
    height: 140,
    borderWidth: 3,
    borderColor: '#fff',
    borderRadius: 12,
    backgroundColor: 'transparent',
  },
  hint: { color: '#fff', fontSize: 16, fontWeight: '500' },
  info: { fontSize: 16, color: '#122923', textAlign: 'center', marginBottom: 16 },
  btn: {
    backgroundColor: '#164b40',
    paddingHorizontal: 20,
    paddingVertical: 12,
    borderRadius: 12,
  },
  btnText: { color: '#fff', fontWeight: '600', fontSize: 16 },
})
