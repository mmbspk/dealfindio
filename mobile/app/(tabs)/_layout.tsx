/**
 * Tab bar layout: Deals, Products, Stores, Watchlist, Profile.
 * Bell icon with unread badge lives in the shared header right slot.
 */

import { useEffect, useState } from 'react'
import { Pressable, StyleSheet, Text, View } from 'react-native'
import { router, Tabs } from 'expo-router'
import apiClient from '@/api/client'
import { useStore } from '@/store/store'

function TabIcon({ emoji, color }: { emoji: string; color: string }) {
  return <Text style={{ fontSize: 22, color }}>{emoji}</Text>
}

function BellIcon({ color, unread }: { color: string; unread: number }) {
  return (
    <View style={styles.bellWrap}>
      <Text style={{ fontSize: 20, color }}>🔔</Text>
      {unread > 0 && (
        <View style={styles.badge}>
          <Text style={styles.badgeText}>{unread > 99 ? '99' : unread}</Text>
        </View>
      )}
    </View>
  )
}

export default function TabsLayout() {
  const { isAuthenticated } = useStore()
  const [unreadCount, setUnreadCount] = useState(0)

  useEffect(() => {
    if (!isAuthenticated) { setUnreadCount(0); return }
    let cancelled = false
    apiClient.get('/api/v1/notifications/unread-count')
      .then((r) => { if (!cancelled) setUnreadCount(r.data.count ?? 0) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [isAuthenticated])

  const headerRight = () => (
    <Pressable
      onPress={() => router.push('/notifications')}
      style={styles.bellBtn}
      hitSlop={8}
      accessibilityLabel={unreadCount > 0 ? `${unreadCount} unread notifications` : 'Notifications'}
    >
      <BellIcon color="#fff" unread={unreadCount} />
    </Pressable>
  )

  return (
    <Tabs
      screenOptions={{
        tabBarActiveTintColor: '#4f46e5',
        tabBarInactiveTintColor: '#9ca3af',
        headerStyle: { backgroundColor: '#4f46e5' },
        headerTintColor: '#fff',
        headerTitleStyle: { fontWeight: '600' },
        headerRight,
      }}
    >
      <Tabs.Screen
        name="deals"
        options={{
          title: 'Deals',
          tabBarIcon: ({ color }) => <TabIcon emoji="🔥" color={color} />,
        }}
      />
      <Tabs.Screen
        name="search"
        options={{
          title: 'Products',
          tabBarIcon: ({ color }) => <TabIcon emoji="🛒" color={color} />,
        }}
      />
      <Tabs.Screen
        name="stores"
        options={{
          title: 'Stores',
          tabBarIcon: ({ color }) => <TabIcon emoji="📍" color={color} />,
        }}
      />
      <Tabs.Screen
        name="watchlist"
        options={{
          title: 'Watchlist',
          tabBarIcon: ({ color }) => <TabIcon emoji="⭐" color={color} />,
        }}
      />
      <Tabs.Screen
        name="profile"
        options={{
          title: 'Profile',
          tabBarIcon: ({ color }) => <TabIcon emoji="👤" color={color} />,
        }}
      />
    </Tabs>
  )
}

const styles = StyleSheet.create({
  bellBtn: { marginRight: 12, padding: 4 },
  bellWrap: { position: 'relative' },
  badge: {
    position: 'absolute', top: -4, right: -6,
    backgroundColor: '#ef4444',
    borderRadius: 8, minWidth: 16, height: 16,
    alignItems: 'center', justifyContent: 'center',
    paddingHorizontal: 3,
  },
  badgeText: { color: '#fff', fontSize: 10, fontWeight: '700' },
})
