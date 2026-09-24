/**
 * Root layout for the Expo Router app.
 * Wraps the whole app in a Stack; the (tabs) group renders the tab bar.
 * Registers an Expo push token after sign-in and on cold start.
 */

import { useEffect } from 'react'
import { Stack } from 'expo-router'
import { StatusBar } from 'expo-status-bar'
import { SafeAreaProvider } from 'react-native-safe-area-context'
import * as Notifications from 'expo-notifications'
import * as Device from 'expo-device'
import { apiClient } from '@/api/client'
import { useStore } from '@/store/store'

async function registerPushToken() {
  if (!Device.isDevice) return
  const { status: existing } = await Notifications.getPermissionsAsync()
  let finalStatus = existing
  if (existing !== 'granted') {
    const { status } = await Notifications.requestPermissionsAsync()
    finalStatus = status
  }
  if (finalStatus !== 'granted') return
  try {
    const token = (await Notifications.getExpoPushTokenAsync()).data
    await apiClient.post('/api/v1/notifications/fcm-token', { fcm_token: token })
  } catch {
    // Non-critical — token registration failures shouldn't crash the app
  }
}

export default function RootLayout() {
  const { isAuthenticated } = useStore()

  useEffect(() => {
    if (isAuthenticated) {
      void registerPushToken()
    }
  }, [isAuthenticated])

  return (
    <SafeAreaProvider>
      <StatusBar style="light" />
      <Stack
        screenOptions={{
          headerStyle: { backgroundColor: '#4f46e5' },
          headerTintColor: '#fff',
          headerTitleStyle: { fontWeight: '600' },
        }}
      >
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        <Stack.Screen name="notifications" options={{ title: 'Notifications' }} />
        <Stack.Screen name="notification-preferences" options={{ title: 'Notification Preferences' }} />
        <Stack.Screen name="scan" options={{ title: 'Scan Barcode' }} />
        <Stack.Screen name="report-price" options={{ title: 'Report Price' }} />
        <Stack.Screen name="upload-receipt" options={{ title: 'Upload Receipt' }} />
        <Stack.Screen name="product/[id]" options={{ title: 'Product' }} />
      </Stack>
    </SafeAreaProvider>
  )
}
