/**
 * Upload-receipt screen.
 *
 * Lets the user pick or photograph a receipt, uploads it to the backend's
 * receipt-OCR endpoint (Claude vision), and shows the extracted line items
 * and how many were matched to products.
 */

import { useState } from 'react'
import {
  ActivityIndicator,
  Image,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native'
import * as ImagePicker from 'expo-image-picker'
import apiClient from '@/api/client'

interface UploadResult {
  status: string
  message: string
  created_reports: { product_name: string; confidence: number }[]
  unmatched_items: { name: string; price: number; reason: string }[]
}

export default function UploadReceiptScreen() {
  const [imageUri, setImageUri] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [result, setResult] = useState<UploadResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function pickImage(fromCamera: boolean) {
    setError(null)
    setResult(null)
    const picker = fromCamera
      ? ImagePicker.launchCameraAsync
      : ImagePicker.launchImageLibraryAsync

    if (fromCamera) {
      const perm = await ImagePicker.requestCameraPermissionsAsync()
      if (!perm.granted) {
        setError('Camera permission is required.')
        return
      }
    }

    const res = await picker({
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      quality: 0.7,
    })
    if (!res.canceled && res.assets.length > 0) {
      setImageUri(res.assets[0].uri)
    }
  }

  async function handleUpload() {
    if (!imageUri) return
    setUploading(true)
    setError(null)
    try {
      const form = new FormData()
      form.append('file', {
        uri: imageUri,
        name: 'receipt.jpg',
        type: 'image/jpeg',
      } as any)

      const resp = await apiClient.post<UploadResult>(
        '/api/v1/receipts/upload',
        form,
        { headers: { 'Content-Type': 'multipart/form-data' } }
      )
      setResult(resp.data)
    } catch (e: any) {
      const detail = e?.response?.data?.detail
      setError(detail ?? 'Upload failed. Try again.')
    } finally {
      setUploading(false)
    }
  }

  return (
    <ScrollView contentContainerStyle={styles.container}>
      {imageUri ? (
        <Image source={{ uri: imageUri }} style={styles.preview} />
      ) : (
        <View style={styles.placeholder}>
          <Text style={styles.placeholderText}>No receipt selected</Text>
        </View>
      )}

      <View style={styles.pickRow}>
        <Pressable style={styles.pickBtn} onPress={() => pickImage(true)}>
          <Text style={styles.pickText}>📷 Camera</Text>
        </Pressable>
        <Pressable style={styles.pickBtn} onPress={() => pickImage(false)}>
          <Text style={styles.pickText}>🖼️ Library</Text>
        </Pressable>
      </View>

      <Pressable
        style={[styles.upload, (!imageUri || uploading) && styles.disabled]}
        onPress={handleUpload}
        disabled={!imageUri || uploading}
      >
        {uploading ? (
          <ActivityIndicator color="#fff" />
        ) : (
          <Text style={styles.uploadText}>Upload & Parse</Text>
        )}
      </Pressable>

      {error ? <Text style={styles.error}>{error}</Text> : null}

      {result ? (
        <View style={styles.result}>
          <Text style={styles.resultMsg}>{result.message}</Text>
          {result.created_reports.map((r, i) => (
            <Text key={`c${i}`} style={styles.matched}>
              ✓ {r.product_name}
            </Text>
          ))}
          {result.unmatched_items.map((u, i) => (
            <Text key={`u${i}`} style={styles.unmatched}>
              ? {u.name} (${u.price.toFixed(2)}) — {u.reason}
            </Text>
          ))}
        </View>
      ) : null}
    </ScrollView>
  )
}

const styles = StyleSheet.create({
  container: { padding: 20, backgroundColor: '#f5f7f2', flexGrow: 1 },
  preview: { width: '100%', height: 280, borderRadius: 12, resizeMode: 'contain' },
  placeholder: {
    width: '100%',
    height: 280,
    borderRadius: 12,
    borderWidth: 2,
    borderStyle: 'dashed',
    borderColor: '#d1d5db',
    justifyContent: 'center',
    alignItems: 'center',
  },
  placeholderText: { color: '#71877b' },
  pickRow: { flexDirection: 'row', gap: 12, marginTop: 16 },
  pickBtn: {
    flex: 1,
    backgroundColor: '#fff',
    borderRadius: 12,
    borderWidth: 1,
    borderColor: '#dce5dd',
    padding: 14,
    alignItems: 'center',
  },
  pickText: { fontSize: 15, color: '#122923', fontWeight: '500' },
  upload: {
    backgroundColor: '#164b40',
    borderRadius: 12,
    padding: 16,
    alignItems: 'center',
    marginTop: 16,
  },
  disabled: { opacity: 0.6 },
  uploadText: { color: '#fff', fontWeight: '600', fontSize: 16 },
  error: { color: '#ef4444', marginTop: 16, textAlign: 'center' },
  result: { marginTop: 20, gap: 4 },
  resultMsg: { fontSize: 15, fontWeight: '600', color: '#122923', marginBottom: 8 },
  matched: { color: '#176f50', fontSize: 14 },
  unmatched: { color: '#b45309', fontSize: 14 },
})
