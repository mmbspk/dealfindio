/**
 * Watchlist tab — multi-list: sidebar of named lists + items for the active list.
 */

import { useCallback, useEffect, useState } from 'react'
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Image,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
  Modal,
  TextInput,
  KeyboardAvoidingView,
  Platform,
} from 'react-native'
import { useRouter } from 'expo-router'
import apiClient from '@/api/client'
import type { WatchlistItem, WatchlistList } from '@/types'

export default function WatchlistScreen() {
  const router = useRouter()
  const [lists, setLists] = useState<WatchlistList[]>([])
  const [items, setItems] = useState<WatchlistItem[]>([])
  const [activeListId, setActiveListId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [itemsLoading, setItemsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [deletingItemId, setDeletingItemId] = useState<string | null>(null)
  const [deletingListId, setDeletingListId] = useState<string | null>(null)

  // New list modal
  const [showNewList, setShowNewList] = useState(false)
  const [newName, setNewName] = useState('')
  const [newEmoji, setNewEmoji] = useState('')
  const [creatingList, setCreatingList] = useState(false)

  const loadLists = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await apiClient.get<WatchlistList[]>('/api/v1/watchlist')
      const data = r.data ?? []
      setLists(data)
      if (data.length > 0) {
        const first = data[0]
        setActiveListId(first.id)
        await loadItems(first.id)
      }
    } catch {
      setError('Sign in to view your watchlist.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadLists()
  }, [loadLists])

  async function loadItems(listId: string) {
    setItemsLoading(true)
    try {
      const r = await apiClient.get<WatchlistItem[]>(`/api/v1/watchlist/${listId}/items`)
      setItems(r.data ?? [])
    } catch {
      setItems([])
    } finally {
      setItemsLoading(false)
    }
  }

  async function switchList(listId: string) {
    setActiveListId(listId)
    await loadItems(listId)
  }

  async function createList() {
    const name = newName.trim()
    if (!name) return
    setCreatingList(true)
    try {
      const r = await apiClient.post<WatchlistList>('/api/v1/watchlist', {
        name,
        emoji: newEmoji.trim() || null,
      })
      const created = r.data
      setLists(prev => [...prev, created])
      setNewName('')
      setNewEmoji('')
      setShowNewList(false)
      setActiveListId(created.id)
      setItems([])
    } catch {
      Alert.alert('Error', 'Could not create list. Please try again.')
    } finally {
      setCreatingList(false)
    }
  }

  function confirmDeleteList(listId: string, listName: string) {
    Alert.alert(
      'Delete list',
      `Delete "${listName}" and all its items?`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: () => void deleteList(listId),
        },
      ]
    )
  }

  async function deleteList(listId: string) {
    setDeletingListId(listId)
    try {
      await apiClient.delete(`/api/v1/watchlist/${listId}`)
      const remaining = lists.filter(l => l.id !== listId)
      setLists(remaining)
      if (activeListId === listId) {
        const next = remaining[0] ?? null
        setActiveListId(next?.id ?? null)
        if (next) await loadItems(next.id)
        else setItems([])
      }
    } catch {
      Alert.alert('Error', 'Could not delete list.')
    } finally {
      setDeletingListId(null)
    }
  }

  function confirmRemoveItem(item: WatchlistItem) {
    Alert.alert(
      'Remove item',
      `Remove "${item.product_name}" from this list?`,
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Remove',
          style: 'destructive',
          onPress: () => void removeItem(item.product_id),
        },
      ]
    )
  }

  async function removeItem(productId: string) {
    if (!activeListId) return
    setDeletingItemId(productId)
    try {
      await apiClient.delete(`/api/v1/watchlist/${activeListId}/items/${productId}`)
      setItems(prev => prev.filter(i => i.product_id !== productId))
      setLists(prev => prev.map(l =>
        l.id === activeListId ? { ...l, item_count: Math.max(0, l.item_count - 1) } : l
      ))
    } catch {
      Alert.alert('Error', 'Could not remove item. Please try again.')
    } finally {
      setDeletingItemId(null)
    }
  }

  function renderItem({ item }: { item: WatchlistItem }) {
    const isDeleting = deletingItemId === item.product_id
    return (
      <TouchableOpacity
        onPress={() => router.push(`/product/${item.product_id}` as never)}
        activeOpacity={0.7}
        style={styles.card}
      >
        {/* Thumbnail */}
        <View style={styles.thumb}>
          {item.product_image_url ? (
            <Image
              source={{ uri: item.product_image_url }}
              style={styles.thumbImg}
              resizeMode="contain"
            />
          ) : (
            <View style={styles.thumbPlaceholder} />
          )}
        </View>

        {/* Info */}
        <View style={styles.cardBody}>
          <Text style={styles.cardName} numberOfLines={2}>
            {item.product_name ?? item.product_id}
          </Text>
          {item.product_brand ? (
            <Text style={styles.cardBrand}>{item.product_brand}</Text>
          ) : null}
          <View style={styles.cardMeta}>
            {item.current_price_aud != null && (
              <Text style={[styles.price, item.is_on_sale && styles.priceSale]}>
                ${item.current_price_aud.toFixed(2)}
                {item.is_on_sale ? '  SALE' : ''}
              </Text>
            )}
            {item.current_chain ? (
              <Text style={styles.chain}>at <Text style={{ textTransform: 'capitalize', fontWeight: '500' }}>{item.current_chain}</Text></Text>
            ) : null}
            {item.target_price_cents != null && (
              <View style={styles.badge}>
                <Text style={styles.badgeText}>Target ${(item.target_price_cents / 100).toFixed(2)}</Text>
              </View>
            )}
            {item.target_pct_off != null && (
              <View style={styles.badge}>
                <Text style={styles.badgeText}>Target {item.target_pct_off}% off</Text>
              </View>
            )}
            {item.alert_on_prediction && (
              <View style={[styles.badge, styles.badgePurple]}>
                <Text style={[styles.badgeText, styles.badgePurpleText]}>ML alerts</Text>
              </View>
            )}
          </View>
        </View>

        {/* Delete */}
        <TouchableOpacity
          onPress={() => confirmRemoveItem(item)}
          disabled={isDeleting}
          style={styles.deleteBtn}
          accessibilityLabel="Remove from list"
        >
          {isDeleting
            ? <ActivityIndicator size="small" color="#ef4444" />
            : <Text style={styles.deleteIcon}>✕</Text>
          }
        </TouchableOpacity>
      </TouchableOpacity>
    )
  }

  if (loading) {
    return <ActivityIndicator size="large" color="#4f46e5" style={styles.spinner} />
  }

  if (error) {
    return (
      <View style={styles.centerBox}>
        <Text style={styles.errorText}>{error}</Text>
        <TouchableOpacity style={styles.signInBtn} onPress={() => router.push('/login' as never)}>
          <Text style={styles.signInBtnText}>Sign in</Text>
        </TouchableOpacity>
      </View>
    )
  }

  const activeList = lists.find(l => l.id === activeListId) ?? null

  return (
    <View style={styles.root}>
      {/* Lists sidebar (horizontal scroll on mobile) */}
      <View style={styles.sidebar}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.sidebarInner}>
          {lists.map(l => (
            <TouchableOpacity
              key={l.id}
              onPress={() => switchList(l.id)}
              onLongPress={() => confirmDeleteList(l.id, l.name)}
              style={[styles.tab, l.id === activeListId && styles.tabActive]}
            >
              {l.emoji ? <Text style={styles.tabEmoji}>{l.emoji}</Text> : null}
              <Text style={[styles.tabName, l.id === activeListId && styles.tabNameActive]} numberOfLines={1}>
                {l.name}
              </Text>
              <View style={[styles.tabCount, l.id === activeListId && styles.tabCountActive]}>
                <Text style={[styles.tabCountText, l.id === activeListId && styles.tabCountTextActive]}>
                  {l.item_count}
                </Text>
              </View>
            </TouchableOpacity>
          ))}
          <TouchableOpacity
            style={styles.newListBtn}
            onPress={() => setShowNewList(true)}
          >
            <Text style={styles.newListBtnText}>+ New list</Text>
          </TouchableOpacity>
        </ScrollView>
      </View>

      {/* Active list header */}
      {activeList && (
        <View style={styles.listHeader}>
          <Text style={styles.listTitle}>
            {activeList.emoji ? `${activeList.emoji}  ` : ''}{activeList.name}
          </Text>
          <Text style={styles.listCount}>
            {activeList.item_count} item{activeList.item_count !== 1 ? 's' : ''}
          </Text>
        </View>
      )}

      {/* Items */}
      {itemsLoading ? (
        <ActivityIndicator size="large" color="#4f46e5" style={styles.spinner} />
      ) : lists.length === 0 ? (
        <View style={styles.centerBox}>
          <Text style={styles.emptyTitle}>No lists yet</Text>
          <Text style={styles.emptySub}>Create a list and save deals to start tracking prices.</Text>
          <TouchableOpacity style={styles.signInBtn} onPress={() => setShowNewList(true)}>
            <Text style={styles.signInBtnText}>Create a list</Text>
          </TouchableOpacity>
        </View>
      ) : items.length === 0 ? (
        <View style={styles.centerBox}>
          <Text style={styles.emptyTitle}>This list is empty</Text>
          <Text style={styles.emptySub}>Browse deals and tap the bookmark icon to save here.</Text>
        </View>
      ) : (
        <FlatList
          data={items}
          keyExtractor={item => item.id}
          renderItem={renderItem}
          contentContainerStyle={styles.list}
          onRefresh={() => activeListId && loadItems(activeListId)}
          refreshing={itemsLoading}
        />
      )}

      {/* New list modal */}
      <Modal visible={showNewList} transparent animationType="slide" onRequestClose={() => setShowNewList(false)}>
        <KeyboardAvoidingView
          behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
          style={styles.modalBackdrop}
        >
          <TouchableOpacity style={styles.modalBackdropTouch} activeOpacity={1} onPress={() => setShowNewList(false)} />
          <View style={styles.modalSheet}>
            <Text style={styles.modalTitle}>New list</Text>
            <View style={styles.modalRow}>
              <TextInput
                value={newEmoji}
                onChangeText={setNewEmoji}
                placeholder="🛒"
                maxLength={2}
                style={styles.emojiInput}
              />
              <TextInput
                value={newName}
                onChangeText={setNewName}
                placeholder="List name…"
                autoFocus
                style={styles.nameInput}
                returnKeyType="done"
                onSubmitEditing={createList}
              />
            </View>
            <TouchableOpacity
              onPress={createList}
              disabled={creatingList || !newName.trim()}
              style={[styles.createBtn, (!newName.trim() || creatingList) && styles.createBtnDisabled]}
            >
              <Text style={styles.createBtnText}>{creatingList ? 'Creating…' : 'Create list'}</Text>
            </TouchableOpacity>
          </View>
        </KeyboardAvoidingView>
      </Modal>
    </View>
  )
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#f9fafb' },
  spinner: { flex: 1, backgroundColor: '#f9fafb' },
  centerBox: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 32 },
  errorText: { color: '#ef4444', fontSize: 16, textAlign: 'center', marginBottom: 16 },
  emptyTitle: { fontSize: 18, fontWeight: '600', color: '#374151', textAlign: 'center' },
  emptySub: { fontSize: 14, color: '#6b7280', textAlign: 'center', marginTop: 8 },
  signInBtn: { marginTop: 20, backgroundColor: '#4f46e5', paddingHorizontal: 28, paddingVertical: 13, borderRadius: 10 },
  signInBtnText: { color: '#fff', fontWeight: '600', fontSize: 15 },

  // Sidebar
  sidebar: { backgroundColor: '#fff', borderBottomWidth: 1, borderBottomColor: '#e5e7eb' },
  sidebarInner: { paddingHorizontal: 12, paddingVertical: 10, gap: 8, flexDirection: 'row', alignItems: 'center' },
  tab: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 20,
    backgroundColor: '#f3f4f6',
    maxWidth: 160,
  },
  tabActive: { backgroundColor: '#4f46e5' },
  tabEmoji: { fontSize: 14 },
  tabName: { fontSize: 13, fontWeight: '500', color: '#374151' },
  tabNameActive: { color: '#fff' },
  tabCount: { backgroundColor: '#e5e7eb', borderRadius: 10, paddingHorizontal: 6, paddingVertical: 1 },
  tabCountActive: { backgroundColor: '#6366f1' },
  tabCountText: { fontSize: 11, fontWeight: '600', color: '#6b7280' },
  tabCountTextActive: { color: '#fff' },
  newListBtn: {
    paddingHorizontal: 14,
    paddingVertical: 7,
    borderRadius: 20,
    borderWidth: 1.5,
    borderStyle: 'dashed',
    borderColor: '#c7d2fe',
  },
  newListBtnText: { fontSize: 13, color: '#6366f1', fontWeight: '500' },

  // List header
  listHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 16,
    paddingTop: 14,
    paddingBottom: 6,
  },
  listTitle: { fontSize: 18, fontWeight: '700', color: '#111827' },
  listCount: { fontSize: 13, color: '#9ca3af' },

  // Items
  list: { padding: 12, paddingBottom: 32 },
  card: {
    backgroundColor: '#fff',
    borderRadius: 12,
    padding: 12,
    marginBottom: 8,
    borderWidth: 1,
    borderColor: '#e5e7eb',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
  },
  thumb: {
    width: 52,
    height: 52,
    borderRadius: 8,
    backgroundColor: '#f3f4f6',
    overflow: 'hidden',
    flexShrink: 0,
    borderWidth: 1,
    borderColor: '#f0f0f0',
  },
  thumbImg: { width: '100%', height: '100%' },
  thumbPlaceholder: { flex: 1, backgroundColor: '#e5e7eb' },
  cardBody: { flex: 1 },
  cardName: { fontSize: 14, fontWeight: '600', color: '#111827', lineHeight: 19 },
  cardBrand: { fontSize: 11, color: '#9ca3af', marginTop: 1 },
  cardMeta: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 5, alignItems: 'center' },
  price: { fontSize: 14, fontWeight: '700', color: '#10b981' },
  priceSale: { color: '#ef4444' },
  chain: { fontSize: 11, color: '#6b7280' },
  badge: {
    backgroundColor: '#eef2ff',
    borderRadius: 12,
    paddingHorizontal: 8,
    paddingVertical: 2,
  },
  badgeText: { fontSize: 11, color: '#4f46e5', fontWeight: '500' },
  badgePurple: { backgroundColor: '#faf5ff' },
  badgePurpleText: { color: '#7c3aed' },
  deleteBtn: { padding: 8, marginLeft: 4, flexShrink: 0 },
  deleteIcon: { fontSize: 14, color: '#d1d5db', fontWeight: '700' },

  // Modal
  modalBackdrop: { flex: 1, justifyContent: 'flex-end' },
  modalBackdropTouch: { flex: 1 },
  modalSheet: {
    backgroundColor: '#fff',
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    padding: 24,
    paddingBottom: 40,
    gap: 16,
  },
  modalTitle: { fontSize: 18, fontWeight: '700', color: '#111827' },
  modalRow: { flexDirection: 'row', gap: 10, alignItems: 'center' },
  emojiInput: {
    width: 52,
    height: 44,
    borderWidth: 1,
    borderColor: '#e5e7eb',
    borderRadius: 10,
    textAlign: 'center',
    fontSize: 22,
    backgroundColor: '#f9fafb',
  },
  nameInput: {
    flex: 1,
    height: 44,
    borderWidth: 1,
    borderColor: '#e5e7eb',
    borderRadius: 10,
    paddingHorizontal: 14,
    fontSize: 15,
    backgroundColor: '#f9fafb',
    color: '#111827',
  },
  createBtn: {
    backgroundColor: '#4f46e5',
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: 'center',
  },
  createBtnDisabled: { opacity: 0.45 },
  createBtnText: { color: '#fff', fontSize: 16, fontWeight: '600' },
})
