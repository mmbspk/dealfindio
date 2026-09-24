import React, { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { apiClient } from '../api/client'
import type { WatchlistList, WatchlistItem } from '../types'

export const Watchlist: React.FC = () => {
  const [lists, setLists] = useState<WatchlistList[]>([])
  const [items, setItems] = useState<WatchlistItem[]>([])
  const [activeListId, setActiveListId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [itemsLoading, setItemsLoading] = useState(false)
  const [error, setError] = useState('')

  // New list form
  const [showNewList, setShowNewList] = useState(false)
  const [newListName, setNewListName] = useState('')
  const [newListEmoji, setNewListEmoji] = useState('')
  const [creatingList, setCreatingList] = useState(false)

  // Delete states
  const [deletingItemId, setDeletingItemId] = useState<string | null>(null)
  const [deletingListId, setDeletingListId] = useState<string | null>(null)

  useEffect(() => {
    loadLists()
  }, [])

  const loadLists = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await apiClient.get<WatchlistList[]>('/api/v1/watchlist')
      const data = r.data ?? []
      setLists(data)
      if (data.length > 0) {
        const firstId = data[0].id
        setActiveListId(firstId)
        await loadItems(firstId)
      }
    } catch (err: unknown) {
      const status = (err as { response?: { status?: number } })?.response?.status
      if (status === 401) setError('Sign in to view your watchlist.')
      else setError('Failed to load watchlist.')
    } finally {
      setLoading(false)
    }
  }, [])

  const loadItems = async (listId: string) => {
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

  async function createList(e: React.FormEvent) {
    e.preventDefault()
    const name = newListName.trim()
    if (!name) return
    setCreatingList(true)
    try {
      const r = await apiClient.post<WatchlistList>('/api/v1/watchlist', {
        name,
        emoji: newListEmoji.trim() || null,
      })
      const newList = r.data
      setLists(prev => [...prev, newList])
      setNewListName('')
      setNewListEmoji('')
      setShowNewList(false)
      setActiveListId(newList.id)
      setItems([])
    } catch {
      // ignore
    } finally {
      setCreatingList(false)
    }
  }

  async function deleteList(listId: string) {
    if (!window.confirm('Delete this list and all its items?')) return
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
      // ignore
    } finally {
      setDeletingListId(null)
    }
  }

  async function removeItem(listId: string, productId: string) {
    setDeletingItemId(productId)
    try {
      await apiClient.delete(`/api/v1/watchlist/${listId}/items/${productId}`)
      setItems(prev => prev.filter(i => i.product_id !== productId))
      setLists(prev => prev.map(l =>
        l.id === listId ? { ...l, item_count: Math.max(0, l.item_count - 1) } : l
      ))
    } catch {
      // ignore
    } finally {
      setDeletingItemId(null)
    }
  }

  const activeList = lists.find(l => l.id === activeListId) ?? null

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="w-10 h-10 border-4 border-emerald-200 border-t-emerald-600 rounded-full animate-spin" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="bg-white rounded-xl shadow p-10 text-center max-w-sm">
          <p className="text-gray-600 mb-4">{error}</p>
          {error.includes('Sign in') && (
            <Link to="/login?next=/watchlist" className="px-5 py-2.5 bg-amber-500 text-gray-900 rounded-lg font-semibold hover:bg-amber-600 transition text-sm">
              Sign in
            </Link>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-[1280px] mx-auto px-6 sm:px-8 lg:px-10 py-10">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-3xl font-bold text-gray-900">My Lists</h1>
          <button
            onClick={() => setShowNewList(v => !v)}
            className="flex items-center gap-1.5 px-4 py-2 bg-amber-500 text-gray-900 rounded-lg font-semibold hover:bg-amber-600 transition text-sm"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            New list
          </button>
        </div>

        {/* New list form */}
        {showNewList && (
          <div className="bg-white border border-gray-200 rounded-xl p-5 mb-6 shadow-sm">
            <form onSubmit={createList} className="flex gap-3 items-end flex-wrap">
              <div>
                <label className="block text-xs font-semibold text-gray-500 mb-1">Emoji (optional)</label>
                <input
                  type="text"
                  value={newListEmoji}
                  onChange={e => setNewListEmoji(e.target.value)}
                  placeholder="🛒"
                  maxLength={2}
                  className="w-14 border border-gray-200 rounded-lg px-2.5 py-2 text-center text-lg focus:outline-none focus:ring-2 focus:ring-emerald-300"
                />
              </div>
              <div className="flex-1 min-w-[180px]">
                <label className="block text-xs font-semibold text-gray-500 mb-1">List name</label>
                <input
                  type="text"
                  value={newListName}
                  onChange={e => setNewListName(e.target.value)}
                  placeholder="e.g. Weekly shop, Baby items…"
                  autoFocus
                  className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-300"
                />
              </div>
              <button
                type="submit"
                disabled={creatingList || !newListName.trim()}
                className="px-4 py-2 bg-amber-500 text-gray-900 rounded-lg text-sm font-semibold hover:bg-amber-600 transition disabled:opacity-40"
              >
                {creatingList ? 'Creating…' : 'Create'}
              </button>
              <button
                type="button"
                onClick={() => { setShowNewList(false); setNewListName(''); setNewListEmoji('') }}
                className="px-4 py-2 text-gray-500 hover:text-gray-700 text-sm transition"
              >
                Cancel
              </button>
            </form>
          </div>
        )}

        {lists.length === 0 ? (
          /* Empty state: no lists at all */
          <div className="bg-white rounded-xl shadow-sm p-14 text-center border border-gray-100">
            <svg className="w-14 h-14 text-gray-300 mx-auto mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
            </svg>
            <p className="text-lg font-semibold text-gray-700 mb-1">No lists yet</p>
            <p className="text-sm text-gray-500">Create a list then save deals to start tracking prices.</p>
          </div>
        ) : (
          <div className="flex gap-6 items-start">
            {/* Sidebar: list tabs */}
            <div className="w-56 flex-shrink-0 space-y-1">
              {lists.map(l => (
                <div key={l.id} className="group relative">
                  <button
                    onClick={() => switchList(l.id)}
                    className={`w-full text-left flex items-center gap-2.5 px-3 py-2.5 rounded-xl text-sm font-medium transition ${
                      activeListId === l.id
                        ? 'bg-emerald-600 text-white shadow-sm'
                        : 'text-gray-700 hover:bg-gray-100'
                    }`}
                  >
                    {l.emoji && <span className="text-base leading-none">{l.emoji}</span>}
                    <span className="flex-1 truncate">{l.name}</span>
                    <span className={`text-xs rounded-full px-1.5 py-0.5 font-semibold ${
                      activeListId === l.id ? 'bg-emerald-500 text-white' : 'bg-gray-200 text-gray-600'
                    }`}>
                      {l.item_count}
                    </span>
                  </button>
                  <button
                    onClick={() => deleteList(l.id)}
                    disabled={deletingListId === l.id}
                    className="absolute right-1.5 top-1.5 opacity-0 group-hover:opacity-100 p-1 text-gray-400 hover:text-red-500 transition rounded"
                    title="Delete list"
                  >
                    <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                    </svg>
                  </button>
                </div>
              ))}
            </div>

            {/* Main: items for active list */}
            <div className="flex-1 min-w-0">
              {activeList && (
                <div className="flex items-center gap-2 mb-4">
                  {activeList.emoji && <span className="text-2xl">{activeList.emoji}</span>}
                  <h2 className="text-xl font-bold text-gray-900">{activeList.name}</h2>
                  <span className="text-sm text-gray-400">{activeList.item_count} item{activeList.item_count !== 1 ? 's' : ''}</span>
                </div>
              )}

              {itemsLoading ? (
                <div className="flex justify-center py-12">
                  <div className="w-8 h-8 border-4 border-emerald-200 border-t-emerald-600 rounded-full animate-spin" />
                </div>
              ) : items.length === 0 ? (
                <div className="bg-white rounded-xl border border-dashed border-gray-200 p-10 text-center">
                  <p className="text-gray-500 text-sm">This list is empty.</p>
                  <p className="text-xs text-gray-400 mt-1">Browse deals and hit the bookmark icon to save here.</p>
                </div>
              ) : (
                <div className="space-y-3">
                  {items.map(item => (
                    <div
                      key={item.id}
                      className="bg-white border border-gray-200 rounded-xl p-4 flex gap-3 items-start hover:border-emerald-200 hover:shadow-sm transition"
                    >
                      {/* Product image */}
                      <Link to={`/product/${item.product_id}`} className="flex-shrink-0">
                        <div className="w-14 h-14 bg-gray-50 rounded-lg border border-gray-100 overflow-hidden flex items-center justify-center">
                          {item.product_image_url ? (
                            <img src={item.product_image_url} alt={item.product_name ?? ''} className="w-full h-full object-contain" loading="lazy" />
                          ) : (
                            <svg className="w-6 h-6 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
                            </svg>
                          )}
                        </div>
                      </Link>

                      {/* Info */}
                      <div className="flex-1 min-w-0">
                        <Link to={`/product/${item.product_id}`} className="font-semibold text-gray-900 hover:text-emerald-600 transition text-sm leading-tight block truncate">
                          {item.product_name ?? item.product_id}
                        </Link>
                        {item.product_brand && (
                          <p className="text-xs text-gray-400 mt-0.5">{item.product_brand}</p>
                        )}
                        <div className="mt-1.5 flex flex-wrap items-center gap-2">
                          {item.current_price_aud != null && (
                            <span className={`text-sm font-bold ${item.is_on_sale ? 'text-red-500' : 'text-emerald-600'}`}>
                              ${item.current_price_aud.toFixed(2)}
                              {item.is_on_sale && <span className="ml-1 text-xs font-semibold">ON SALE</span>}
                            </span>
                          )}
                          {item.current_chain && (
                            <span className="text-xs text-gray-400">
                              at <span className="capitalize font-medium text-gray-500">{item.current_chain}</span>
                            </span>
                          )}
                          {item.target_price_cents != null && (
                            <span className="text-xs px-2 py-0.5 bg-emerald-50 text-emerald-700 rounded-full font-medium">
                              Target ${(item.target_price_cents / 100).toFixed(2)}
                            </span>
                          )}
                          {item.target_pct_off != null && (
                            <span className="text-xs px-2 py-0.5 bg-emerald-50 text-emerald-700 rounded-full font-medium">
                              Target {item.target_pct_off}% off
                            </span>
                          )}
                          {item.alert_on_prediction && (
                            <span className="text-xs px-2 py-0.5 bg-purple-50 text-purple-700 rounded-full font-medium">
                              ML alerts
                            </span>
                          )}
                        </div>
                      </div>

                      {/* Remove button */}
                      <button
                        onClick={() => removeItem(activeListId!, item.product_id)}
                        disabled={deletingItemId === item.product_id}
                        aria-label="Remove from list"
                        className="flex-shrink-0 p-1.5 text-gray-300 hover:text-red-500 transition rounded-lg hover:bg-red-50 disabled:opacity-40"
                      >
                        {deletingItemId === item.product_id ? (
                          <div className="w-4 h-4 border-2 border-red-300 border-t-red-500 rounded-full animate-spin" />
                        ) : (
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                          </svg>
                        )}
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
