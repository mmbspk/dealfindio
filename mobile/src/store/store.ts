/**
 * Global app state (Zustand).
 *
 * Holds search results, the user's watchlist/notifications, and auth/user
 * profile. Screens subscribe to slices of this store.
 */

import { create } from 'zustand'
import type {
  Notification,
  Product,
  UserProfile,
  WatchlistItem,
  WatchlistList,
} from '@/types'

interface AppState {
  // Auth / user
  user: UserProfile | null
  isAuthenticated: boolean
  setUser: (user: UserProfile | null) => void

  // Search
  query: string
  results: Product[]
  searchLoading: boolean
  setQuery: (q: string) => void
  setResults: (r: Product[]) => void
  setSearchLoading: (loading: boolean) => void

  // Watchlist
  watchlistLists: WatchlistList[]
  watchlistItems: WatchlistItem[]  // items for the active list
  activeListId: string | null
  setWatchlistLists: (lists: WatchlistList[]) => void
  setWatchlistItems: (items: WatchlistItem[]) => void
  setActiveListId: (id: string | null) => void
  // legacy compat — kept so Product screen SaveButton compiles
  watchlist: WatchlistItem[]
  setWatchlist: (items: WatchlistItem[]) => void

  // Notifications
  notifications: Notification[]
  unreadCount: number
  setNotifications: (items: Notification[]) => void
  setUnreadCount: (count: number) => void
}

export const useStore = create<AppState>((set) => ({
  user: null,
  isAuthenticated: false,
  setUser: (user) => set({ user, isAuthenticated: user !== null }),

  query: '',
  results: [],
  searchLoading: false,
  setQuery: (query) => set({ query }),
  setResults: (results) => set({ results }),
  setSearchLoading: (searchLoading) => set({ searchLoading }),

  watchlistLists: [],
  watchlistItems: [],
  activeListId: null,
  setWatchlistLists: (watchlistLists) => set({ watchlistLists }),
  setWatchlistItems: (watchlistItems) => set({ watchlistItems }),
  setActiveListId: (activeListId) => set({ activeListId }),
  watchlist: [],
  setWatchlist: (watchlist) => set({ watchlist }),

  notifications: [],
  unreadCount: 0,
  setNotifications: (notifications) =>
    set({
      notifications,
      unreadCount: notifications.filter((n) => n.opened_at === null).length,
    }),
  setUnreadCount: (unreadCount) => set({ unreadCount }),
}))
