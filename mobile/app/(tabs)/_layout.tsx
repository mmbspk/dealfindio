import { useEffect } from 'react'
import { Pressable, StyleSheet, Text, View } from 'react-native'
import { router, Tabs } from 'expo-router'
import apiClient from '@/api/client'
import { useStore } from '@/store/store'

const forest = '#164b40'
const lime = '#ddf67a'

function TabIcon({ symbol, color }: { symbol: string; color: string }) {
  return <Text style={{ fontSize: 25, lineHeight: 28, color, fontWeight: '500' }}>{symbol}</Text>
}

export default function TabsLayout() {
  const { isAuthenticated, unreadCount, setUnreadCount } = useStore()

  useEffect(() => {
    if (!isAuthenticated) { setUnreadCount(0); return }
    let cancelled = false
    apiClient.get('/api/v1/notifications/unread-count')
      .then(({ data }) => { if (!cancelled) setUnreadCount(data.count ?? 0) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [isAuthenticated, setUnreadCount])

  const headerRight = () => (
    <Pressable
      onPress={() => router.push('/notifications')}
      style={styles.bellButton}
      hitSlop={8}
      accessibilityLabel={unreadCount > 0 ? `${unreadCount} unread notifications` : 'Notifications'}
    >
      <Text style={styles.bell}>🔔</Text>
      {unreadCount > 0 && (
        <View style={styles.badge}><Text style={styles.badgeText}>{unreadCount > 99 ? '99+' : unreadCount}</Text></View>
      )}
    </Pressable>
  )

  return (
    <Tabs screenOptions={{
      tabBarActiveTintColor: forest,
      tabBarInactiveTintColor: '#71877b',
      tabBarStyle: { backgroundColor: '#ffffff', borderTopColor: '#dce5dd', height: 66, paddingTop: 5 },
      tabBarLabelStyle: { fontSize: 11, fontWeight: '700', marginBottom: 4 },
      headerStyle: { backgroundColor: forest },
      headerTintColor: '#ffffff',
      headerTitleStyle: { fontWeight: '700', fontSize: 19 },
      headerRight,
    }}>
      <Tabs.Screen name="search" options={{ title: 'Compare', tabBarIcon: ({ color }) => <TabIcon symbol="⌕" color={color} /> }} />
      <Tabs.Screen name="deals" options={{ title: 'Deals', tabBarIcon: ({ color }) => <TabIcon symbol="◇" color={color} /> }} />
      <Tabs.Screen name="stores" options={{ title: 'Stores', tabBarIcon: ({ color }) => <TabIcon symbol="⌖" color={color} /> }} />
      <Tabs.Screen name="watchlist" options={{ title: 'Saved', tabBarIcon: ({ color }) => <TabIcon symbol="♡" color={color} /> }} />
      <Tabs.Screen name="profile" options={{ title: 'You', tabBarIcon: ({ color }) => <TabIcon symbol="◯" color={color} /> }} />
    </Tabs>
  )
}

const styles = StyleSheet.create({
  bellButton: { marginRight: 15, width: 38, height: 38, backgroundColor: '#ffffff20', borderRadius: 19, alignItems: 'center', justifyContent: 'center' },
  bell: { color: lime, fontSize: 26, lineHeight: 29 },
  badge: { position: 'absolute', top: -3, right: -4, minWidth: 17, height: 17, borderRadius: 9, backgroundColor: lime, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 3 },
  badgeText: { color: forest, fontSize: 10, fontWeight: '800' },
})
