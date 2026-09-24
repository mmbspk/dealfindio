import React, { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiClient } from '../api/client'

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

const CHANNEL_COLOURS: Record<string, string> = {
  push: 'bg-sky-100 text-sky-700',
  email: 'bg-violet-100 text-violet-700',
  in_app: 'bg-gray-100 text-gray-600',
}

function triggerLabel(type: string) {
  return TRIGGER_LABELS[type] ?? type
}

function channelLabel(ch: string) {
  return ch === 'in_app' ? 'in-app' : ch
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

function groupByDate(notifications: Notification[]): { label: string; items: Notification[] }[] {
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const yesterday = new Date(today.getTime() - 86400000)

  const groups: Record<string, Notification[]> = { Today: [], Yesterday: [], Earlier: [] }
  for (const n of notifications) {
    const d = new Date(n.sent_at)
    const day = new Date(d.getFullYear(), d.getMonth(), d.getDate())
    if (day.getTime() === today.getTime()) groups['Today'].push(n)
    else if (day.getTime() === yesterday.getTime()) groups['Yesterday'].push(n)
    else groups['Earlier'].push(n)
  }
  return Object.entries(groups)
    .filter(([, items]) => items.length > 0)
    .map(([label, items]) => ({ label, items }))
}

const PAGE_SIZE = 30

export const Notifications: React.FC = () => {
  const [notifications, setNotifications] = useState<Notification[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [markingAllRead, setMarkingAllRead] = useState(false)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [offset, setOffset] = useState(0)
  const [hasMore, setHasMore] = useState(false)

  const load = useCallback(async (off = 0, append = false) => {
    setLoading(true)
    setError('')
    try {
      const resp = await apiClient.get(`/api/v1/notifications?limit=${PAGE_SIZE}&offset=${off}`)
      const items: Notification[] = resp.data ?? []
      setNotifications((prev) => (append ? [...prev, ...items] : items))
      setHasMore(items.length === PAGE_SIZE)
      setOffset(off)
    } catch (err: any) {
      setError(err?.response?.status === 401 ? 'Sign in to view your notifications.' : 'Failed to load notifications.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load(0) }, [load])

  async function markAllRead() {
    setMarkingAllRead(true)
    try {
      await apiClient.put('/api/v1/notifications/read-all')
      const now = new Date().toISOString()
      setNotifications((prev) => prev.map((n) => ({ ...n, opened_at: n.opened_at ?? now })))
    } catch {
      // ignore
    } finally {
      setMarkingAllRead(false)
    }
  }

  async function deleteNotif(id: string) {
    setDeletingId(id)
    try {
      await apiClient.delete(`/api/v1/notifications/${id}`)
      setNotifications((prev) => prev.filter((n) => n.id !== id))
    } catch {
      // ignore
    } finally {
      setDeletingId(null)
    }
  }

  async function markRead(id: string) {
    try {
      await apiClient.put(`/api/v1/notifications/${id}/read`)
      setNotifications((prev) =>
        prev.map((n) => (n.id === id ? { ...n, opened_at: new Date().toISOString() } : n))
      )
    } catch {
      // ignore
    }
  }

  const unreadCount = notifications.filter((n) => !n.opened_at).length
  const groups = groupByDate(notifications)

  return (
    <div className="min-h-screen bg-gray-50 py-12">
      <div className="max-w-[1280px] mx-auto px-6 sm:px-8 lg:px-10">
      <div className="max-w-3xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div className="flex items-center gap-3">
            <h1 className="text-4xl font-bold text-gray-900">Notifications</h1>
            {unreadCount > 0 && (
              <span className="px-2.5 py-0.5 bg-emerald-600 text-white text-sm font-bold rounded-full">
                {unreadCount}
              </span>
            )}
          </div>
          <div className="flex items-center gap-3">
            {unreadCount > 0 && (
              <button
                onClick={markAllRead}
                disabled={markingAllRead}
                className="text-sm text-emerald-600 hover:text-emerald-800 font-medium disabled:opacity-40 cursor-pointer"
              >
                {markingAllRead ? 'Marking…' : 'Mark all read'}
              </button>
            )}
            <Link
              to="/notifications/preferences"
              className="p-2 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors cursor-pointer"
              aria-label="Notification preferences"
            >
              {/* gear icon */}
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75}
                  d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
            </Link>
          </div>
        </div>

        {/* Content */}
        {loading && notifications.length === 0 ? (
          <div className="flex justify-center py-16">
            <svg className="w-10 h-10 text-emerald-600 animate-spin" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
          </div>
        ) : error ? (
          <div className="bg-white rounded-lg shadow-md p-8 text-center">
            <p className="text-gray-600">{error}</p>
          </div>
        ) : notifications.length === 0 ? (
          <div className="bg-white rounded-lg shadow-md p-12 text-center">
            <svg className="w-16 h-16 text-gray-300 mx-auto mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
            </svg>
            <p className="text-gray-600 mb-2 font-medium">No notifications yet</p>
            <p className="text-sm text-gray-400">Add products to your watchlist to get price-drop alerts.</p>
          </div>
        ) : (
          <div className="space-y-6">
            {groups.map(({ label, items }) => (
              <div key={label}>
                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2 px-1">{label}</p>
                <div className="space-y-2">
                  {items.map((n) => {
                    const isUnread = !n.opened_at
                    return (
                      <div
                        key={n.id}
                        onClick={() => isUnread && markRead(n.id)}
                        className={`bg-white rounded-xl border p-4 flex items-start gap-4 transition-colors cursor-pointer group ${
                          isUnread ? 'border-emerald-200 bg-emerald-50/20' : 'border-gray-100'
                        }`}
                      >
                        {/* unread dot */}
                        <div className="flex-shrink-0 mt-1">
                          {isUnread
                            ? <div className="w-2.5 h-2.5 rounded-full bg-emerald-500" />
                            : <div className="w-2.5 h-2.5 rounded-full bg-transparent" />}
                        </div>

                        <div className="flex-1 min-w-0">
                          <div className="flex flex-wrap items-center gap-1.5 mb-1">
                            <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-700">
                              {triggerLabel(n.trigger_type)}
                            </span>
                            <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${CHANNEL_COLOURS[n.channel] ?? 'bg-gray-100 text-gray-600'}`}>
                              {channelLabel(n.channel)}
                            </span>
                            <span className="text-xs text-gray-400 ml-auto">{timeAgo(n.sent_at)}</span>
                          </div>
                          <p className={`font-semibold text-gray-900 ${isUnread ? '' : 'text-gray-700'}`}>
                            {n.message_title}
                          </p>
                          <p className="text-sm text-gray-500 mt-0.5">{n.message_body}</p>
                          {(n.product_name || n.store_name) && (
                            <p className="text-xs text-gray-400 mt-1">
                              {[n.product_name, n.store_name].filter(Boolean).join(' · ')}
                            </p>
                          )}
                        </div>

                        {/* delete button */}
                        <button
                          onClick={(e) => { e.stopPropagation(); deleteNotif(n.id) }}
                          disabled={deletingId === n.id}
                          aria-label="Delete notification"
                          className="flex-shrink-0 text-gray-300 hover:text-red-400 transition-colors opacity-0 group-hover:opacity-100 disabled:opacity-30 cursor-pointer"
                        >
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                          </svg>
                        </button>
                      </div>
                    )
                  })}
                </div>
              </div>
            ))}

            {hasMore && (
              <div className="text-center pt-2">
                <button
                  onClick={() => load(offset + PAGE_SIZE, true)}
                  disabled={loading}
                  className="text-sm text-emerald-600 hover:text-emerald-800 font-medium disabled:opacity-40 cursor-pointer"
                >
                  {loading ? 'Loading…' : 'Load more'}
                </button>
              </div>
            )}
          </div>
        )}
      </div>
      </div>
    </div>
  )
}
