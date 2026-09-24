import React, { useEffect, useRef, useState } from 'react'
import { apiClient } from '../api/client'
import type { ProductWatchlistStatus, WatchlistList } from '../types'

interface Props {
  productId: string
  size?: 'sm' | 'md'
  className?: string
}

export const SaveToListButton: React.FC<Props> = ({ productId, size = 'md', className = '' }) => {
  const [open, setOpen] = useState(false)
  const [lists, setLists] = useState<ProductWatchlistStatus[]>([])
  const [loading, setLoading] = useState(false)
  const [newListName, setNewListName] = useState('')
  const [creating, setCreating] = useState(false)
  const [showNewInput, setShowNewInput] = useState(false)
  const popoverRef = useRef<HTMLDivElement>(null)
  const newInputRef = useRef<HTMLInputElement>(null)

  const savedCount = lists.filter(l => l.in_list).length
  const isSaved = savedCount > 0

  // Close on outside click
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  // Focus new-list input when it appears
  useEffect(() => {
    if (showNewInput) newInputRef.current?.focus()
  }, [showNewInput])

  async function fetchStatus() {
    setLoading(true)
    try {
      const r = await apiClient.get<ProductWatchlistStatus[]>(
        `/api/v1/watchlist/product/${productId}/status`
      )
      setLists(r.data || [])
    } catch {
      // not authed — silently close
      setOpen(false)
    } finally {
      setLoading(false)
    }
  }

  function handleOpen(e: React.MouseEvent) {
    e.preventDefault()
    e.stopPropagation()
    if (!open) {
      setOpen(true)
      fetchStatus()
    } else {
      setOpen(false)
    }
  }

  async function toggleList(status: ProductWatchlistStatus) {
    const optimistic = lists.map(l =>
      l.list_id === status.list_id ? { ...l, in_list: !l.in_list } : l
    )
    setLists(optimistic)
    try {
      if (status.in_list) {
        await apiClient.delete(
          `/api/v1/watchlist/${status.list_id}/items/${productId}`
        )
      } else {
        await apiClient.post(`/api/v1/watchlist/${status.list_id}/items`, {
          product_id: productId,
        })
      }
    } catch {
      // revert on error
      fetchStatus()
    }
  }

  async function createList() {
    const name = newListName.trim()
    if (!name) return
    setCreating(true)
    try {
      const r = await apiClient.post<WatchlistList>('/api/v1/watchlist', {
        name,
        emoji: null,
      })
      // Add to list and immediately add the product to it
      await apiClient.post(`/api/v1/watchlist/${r.data.id}/items`, {
        product_id: productId,
      })
      setLists(prev => [
        ...prev,
        {
          list_id: r.data.id,
          list_name: r.data.name,
          list_emoji: r.data.emoji,
          in_list: true,
          item_id: null,
        },
      ])
      setNewListName('')
      setShowNewInput(false)
    } catch {
      // ignore
    } finally {
      setCreating(false)
    }
  }

  const iconSize = size === 'sm' ? 'w-4 h-4' : 'w-5 h-5'
  const btnSize = size === 'sm' ? 'p-1.5' : 'p-2'

  return (
    <div className={`relative ${className}`} ref={popoverRef}>
      <button
        type="button"
        onClick={handleOpen}
        className={`${btnSize} rounded-full transition-colors ${
          isSaved
            ? 'text-emerald-600 bg-emerald-50 hover:bg-emerald-100'
            : 'text-gray-400 bg-white hover:text-emerald-600 hover:bg-emerald-50'
        } border border-gray-200 shadow-sm`}
        title={isSaved ? `Saved to ${savedCount} list${savedCount > 1 ? 's' : ''}` : 'Save to list'}
        aria-label="Save to list"
      >
        <svg className={iconSize} fill={isSaved ? 'currentColor' : 'none'} stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
        </svg>
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-2 w-64 bg-white rounded-xl shadow-xl border border-gray-100 z-50 overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-100">
            <p className="text-sm font-semibold text-gray-900">Save to list</p>
          </div>

          {loading ? (
            <div className="px-4 py-6 flex justify-center">
              <div className="w-5 h-5 border-2 border-emerald-300 border-t-emerald-600 rounded-full animate-spin" />
            </div>
          ) : (
            <>
              <div className="max-h-52 overflow-y-auto">
                {lists.length === 0 ? (
                  <p className="px-4 py-3 text-sm text-gray-500">No lists yet. Create one below.</p>
                ) : (
                  lists.map(status => (
                    <button
                      key={status.list_id}
                      type="button"
                      onClick={() => toggleList(status)}
                      className="w-full flex items-center gap-3 px-4 py-2.5 hover:bg-gray-50 transition-colors text-left"
                    >
                      <div className={`w-5 h-5 rounded flex items-center justify-center flex-shrink-0 border-2 transition-colors ${
                        status.in_list
                          ? 'bg-emerald-600 border-emerald-600'
                          : 'border-gray-300'
                      }`}>
                        {status.in_list && (
                          <svg className="w-3 h-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                          </svg>
                        )}
                      </div>
                      <span className="text-sm text-gray-700 flex-1 truncate">
                        {status.list_emoji && <span className="mr-1">{status.list_emoji}</span>}
                        {status.list_name}
                      </span>
                    </button>
                  ))
                )}
              </div>

              <div className="border-t border-gray-100">
                {showNewInput ? (
                  <div className="px-3 py-2 flex gap-2 items-center">
                    <input
                      ref={newInputRef}
                      type="text"
                      value={newListName}
                      onChange={e => setNewListName(e.target.value)}
                      onKeyDown={e => {
                        if (e.key === 'Enter') createList()
                        if (e.key === 'Escape') { setShowNewInput(false); setNewListName('') }
                      }}
                      placeholder="List name…"
                      className="flex-1 text-sm border border-gray-200 rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-2 focus:ring-emerald-300"
                    />
                    <button
                      type="button"
                      onClick={createList}
                      disabled={creating || !newListName.trim()}
                      className="px-2.5 py-1.5 bg-emerald-600 text-white text-sm rounded-lg disabled:opacity-40 hover:bg-emerald-700 transition"
                    >
                      {creating ? '…' : 'Add'}
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => setShowNewInput(true)}
                    className="w-full flex items-center gap-2 px-4 py-3 text-sm text-emerald-600 hover:bg-emerald-50 transition-colors"
                  >
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                    </svg>
                    New list
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
