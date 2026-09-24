/**
 * Notifications screen: in-app notification inbox with swipe-to-delete
 * and mark-all-read. Accessible via the bell icon in the header.
 */

import { useCallback, useEffect, useState } from 'react'
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native'
import { router } from 'expo-router'
import apiClient from '@/api/client'

interface Notification {
  id: string
  trigger_type: string
  message_title: string
  message_body: string
  channel: string
  sent_at: string
  opened_at: string | null
  product_name?: string
  store_name?: string
}

const TRIGGER_LABELS: Record<string, string> = {
  price_match: 'Price Match',
  pct_off_match: 'Discount',
  flash_sale: 'Flash Sale',
  prediction_drop: 'Predicted Drop',
  weekly_digest: 'Weekly Digest',
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

export default function NotificationsScreen() {
  const [notifications, setNotifications] = useState<Notification[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [markingAll, setMarkingAll] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const resp = await apiClient.get('/api/v1/notifications?limit=50')
      setNotifications(resp.data ?? [])
    } catch (e: any) {
      if (e?.response?.status === 401) {
        setError('Sign in to view notifications.')
      } else {
        setError('Failed to load notifications.')
      }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  async function markAllRead() {
    setMarkingAll(true)
    try {
      await apiClient.put('/api/v1/notifications/read-all')
      const now = new Date().toISOString()
      setNotifications((prev) => prev.map((n) => ({ ...n, opened_at: n.opened_at ?? now })))
    } catch {
      Alert.alert('Error', 'Failed to mark all as read.')
    } finally {
      setMarkingAll(false)
    }
  }

  async function markRead(id: string) {
    try {
      await apiClient.put(`/api/v1/notifications/${id}/read`)
      setNotifications((prev) => prev.map((n) =>
        n.id === id ? { ...n, opened_at: new Date().toISOString() } : n
      ))
    } catch {}
  }

  async function deleteNotif(id: string) {
    try {
      await apiClient.delete(`/api/v1/notifications/${id}`)
      setNotifications((prev) => prev.filter((n) => n.id !== id))
    } catch {
      Alert.alert('Error', 'Could not delete notification.')
    }
  }

  function confirmDelete(id: string) {
    Alert.alert('Delete', 'Remove this notification?', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Delete', style: 'destructive', onPress: () => deleteNotif(id) },
    ])
  }

  const unreadCount = notifications.filter((n) => !n.opened_at).length

  function renderItem({ item }: { item: Notification }) {
    const isUnread = !item.opened_at
    return (
      <Pressable
        onPress={() => { if (isUnread) markRead(item.id) }}
        onLongPress={() => confirmDelete(item.id)}
        style={[styles.card, isUnread && styles.cardUnread]}
        accessibilityRole="button"
        accessibilityLabel={`${item.message_title}. ${isUnread ? 'Unread.' : ''} Long press to delete.`}
      >
        <View style={styles.cardLeft}>
          <View style={[styles.dot, isUnread ? styles.dotActive : styles.dotRead]} />
        </View>
        <View style={styles.cardBody}>
          <View style={styles.headerRow}>
            <View style={styles.triggerBadge}>
              <Text style={styles.triggerText}>{TRIGGER_LABELS[item.trigger_type] ?? item.trigger_type}</Text>
            </View>
            <Text style={styles.timeText}>{timeAgo(item.sent_at)}</Text>
          </View>
          <Text style={[styles.title, !isUnread && styles.titleRead]} numberOfLines={2}>
            {item.message_title}
          </Text>
          <Text style={styles.body} numberOfLines={2}>{item.message_body}</Text>
          {(item.product_name || item.store_name) && (
            <Text style={styles.meta} numberOfLines={1}>
              {[item.product_name, item.store_name].filter(Boolean).join(' · ')}
            </Text>
          )}
        </View>
        <Pressable
          onPress={() => confirmDelete(item.id)}
          style={styles.deleteBtn}
          hitSlop={8}
          accessibilityLabel="Delete notification"
        >
          <Text style={styles.deleteIcon}>✕</Text>
        </Pressable>
      </Pressable>
    )
  }

  return (
    <View style={styles.container}>
      {/* Toolbar */}
      <View style={styles.toolbar}>
        <View style={styles.toolbarLeft}>
          {unreadCount > 0 && (
            <View style={styles.unreadBadge}>
              <Text style={styles.unreadBadgeText}>{unreadCount}</Text>
            </View>
          )}
        </View>
        <View style={styles.toolbarRight}>
          {unreadCount > 0 && (
            <Pressable onPress={markAllRead} disabled={markingAll} style={styles.actionBtn}>
              <Text style={styles.actionBtnText}>{markingAll ? 'Marking…' : 'Mark all read'}</Text>
            </Pressable>
          )}
          <Pressable onPress={() => router.push('/notification-preferences')} style={styles.prefsBtn}>
            <Text style={styles.prefsBtnText}>⚙</Text>
          </Pressable>
        </View>
      </View>

      {loading ? (
        <ActivityIndicator size="large" color="#164b40" style={styles.spinner} />
      ) : error ? (
        <View style={styles.emptyState}>
          <Text style={styles.emptyText}>{error}</Text>
        </View>
      ) : notifications.length === 0 ? (
        <View style={styles.emptyState}>
          <Text style={styles.emptyIcon}>🔔</Text>
          <Text style={styles.emptyTitle}>No notifications yet</Text>
          <Text style={styles.emptyText}>Add products to your watchlist to get price-drop alerts.</Text>
        </View>
      ) : (
        <FlatList
          data={notifications}
          keyExtractor={(item) => item.id}
          renderItem={renderItem}
          contentContainerStyle={styles.list}
          showsVerticalScrollIndicator={false}
          onRefresh={load}
          refreshing={loading}
        />
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f5f7f2' },
  toolbar: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingVertical: 10,
    backgroundColor: '#fff', borderBottomWidth: 1, borderBottomColor: '#edf2e9',
  },
  toolbarLeft: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  toolbarRight: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  unreadBadge: { backgroundColor: '#164b40', borderRadius: 14, paddingHorizontal: 8, paddingVertical: 2 },
  unreadBadgeText: { color: '#fff', fontSize: 12, fontWeight: '700' },
  actionBtn: { paddingHorizontal: 10, paddingVertical: 6 },
  actionBtnText: { color: '#164b40', fontSize: 13, fontWeight: '600' },
  prefsBtn: { padding: 6 },
  prefsBtnText: { fontSize: 18, color: '#5d7469' },
  spinner: { marginTop: 48 },
  emptyState: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 32 },
  emptyIcon: { fontSize: 48, marginBottom: 12 },
  emptyTitle: { fontSize: 17, fontWeight: '700', color: '#122923', marginBottom: 6, textAlign: 'center' },
  emptyText: { fontSize: 14, color: '#71877b', textAlign: 'center', lineHeight: 20 },
  list: { padding: 12 },
  card: {
    flexDirection: 'row',
    backgroundColor: '#fff',
    borderRadius: 12,
    marginBottom: 8,
    borderWidth: 1,
    borderColor: '#edf2e9',
    overflow: 'hidden',
    alignItems: 'flex-start',
  },
  cardUnread: { borderColor: '#afd2b2', backgroundColor: '#fafbff' },
  cardLeft: { paddingTop: 16, paddingLeft: 12, paddingRight: 6 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  dotActive: { backgroundColor: '#164b40' },
  dotRead: { backgroundColor: 'transparent' },
  cardBody: { flex: 1, padding: 12, paddingLeft: 4 },
  headerRow: { flexDirection: 'row', alignItems: 'center', gap: 6, marginBottom: 4 },
  triggerBadge: { backgroundColor: '#d1fae5', borderRadius: 14, paddingHorizontal: 7, paddingVertical: 2 },
  triggerText: { fontSize: 11, fontWeight: '600', color: '#065f46' },
  timeText: { fontSize: 11, color: '#71877b', marginLeft: 'auto' },
  title: { fontSize: 14, fontWeight: '700', color: '#122923', lineHeight: 20 },
  titleRead: { color: '#29463a', fontWeight: '600' },
  body: { fontSize: 13, color: '#5d7469', marginTop: 2, lineHeight: 18 },
  meta: { fontSize: 11, color: '#71877b', marginTop: 4 },
  deleteBtn: { padding: 12, paddingTop: 14 },
  deleteIcon: { fontSize: 14, color: '#d1d5db' },
})
