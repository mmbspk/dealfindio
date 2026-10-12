import { useCallback, useEffect, useRef } from 'react'
import * as Notifications from 'expo-notifications'
import { router, useRootNavigationState } from 'expo-router'
import AsyncStorage from '@react-native-async-storage/async-storage'
import apiClient from '@/api/client'
import { useStore } from '@/store/store'
import type { PushNotificationPayload } from '@/types'

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const COLD_START_NOTIF_KEY = '@dealfindio/cold_start_notif_id'

interface PendingAction {
  route: string
  notificationId: string | null
  // Non-null only when the action originated from a cold-start tap. The identifier
  // must not be written to AsyncStorage until navigation is dispatched; keeping it
  // here ensures it can be threaded to pendingRoute when nav is not yet ready.
  coldStartId: string | null
}

interface PendingRoute {
  route: string
  // Non-null when the route came from a cold-start tap. The flush effect writes
  // the identifier to AsyncStorage after router.push fires, so killing the app
  // before the navigator initialises leaves the identifier absent from storage
  // and the notification re-processable on the next cold start.
  coldStartId: string | null
}

function extractProductId(response: Notifications.NotificationResponse): string | null {
  const data = response.notification.request.content.data as PushNotificationPayload | undefined
  const id = data?.product_id
  return id && UUID_RE.test(id) ? id : null
}

function extractNotificationId(response: Notifications.NotificationResponse): string | null {
  const data = response.notification.request.content.data as PushNotificationPayload | undefined
  return data?.notification_id ?? null
}

/**
 * Fire-and-forget: mark a notification read and refresh the badge counter.
 * Must never be awaited before navigation — runs concurrently in the background.
 */
function markReadAndRefreshBadge(
  notificationId: string,
  setUnreadCount: (n: number) => void,
): void {
  void (async () => {
    try {
      await apiClient.put(`/api/v1/notifications/${notificationId}/read`)
      const { data } = await apiClient.get<{ count: number }>(
        '/api/v1/notifications/unread-count',
      )
      setUnreadCount(data.count ?? 0)
    } catch {
      // best-effort — auth or network failure must not block navigation
    }
  })()
}

/**
 * Registers foreground/background/cold-start notification handlers.
 * Call once in the root layout — must be mounted for the lifetime of the app.
 *
 * All three code paths (foreground tap, background tap, cold start) share the same
 * readiness and authentication gates. The cold-start identifier is only persisted to
 * AsyncStorage after router.push has actually been called, whether that happens
 * synchronously or after the navigator and/or authentication become ready.
 */
export function useNotificationHandler(): void {
  const setUnreadCount = useStore((s) => s.setUnreadCount)
  const isAuthenticated = useStore((s) => s.isAuthenticated)

  const processedIds = useRef<Set<string>>(new Set())
  const pendingRoute = useRef<PendingRoute | null>(null)
  const pendingAfterAuth = useRef<PendingAction | null>(null)

  const isAuthRef = useRef(isAuthenticated)
  const isNavReadyRef = useRef(false)

  const rootNavState = useRootNavigationState()
  const isNavReady = !!rootNavState?.key

  isAuthRef.current = isAuthenticated
  isNavReadyRef.current = isNavReady

  // Used only by the background-tap subscription handler — no coldStartId to thread.
  const queueOrNavigate = useCallback((route: string): void => {
    if (isNavReadyRef.current) {
      router.push(route)
    } else {
      pendingRoute.current = { route, coldStartId: null }
    }
  }, [])

  // Flush any deferred navigation once the navigator becomes ready.
  // If the pending route originated from a cold start, persist the identifier
  // only here — after router.push has actually been called.
  useEffect(() => {
    if (isNavReady && pendingRoute.current) {
      const { route, coldStartId } = pendingRoute.current
      pendingRoute.current = null
      router.push(route)
      if (coldStartId) {
        void AsyncStorage.setItem(COLD_START_NOTIF_KEY, coldStartId).catch(() => {})
      }
    }
  }, [isNavReady])

  // Resume a notification action that arrived while the user was unauthenticated.
  // Inlines the nav-readiness check so that a cold-start coldStartId is threaded
  // through to pendingRoute when nav is not yet ready, rather than being saved to
  // AsyncStorage before the push fires.
  useEffect(() => {
    if (!isAuthenticated) return
    const action = pendingAfterAuth.current
    if (!action) return
    pendingAfterAuth.current = null

    if (action.notificationId) {
      markReadAndRefreshBadge(action.notificationId, setUnreadCount)
    }

    if (isNavReadyRef.current) {
      router.push(action.route)
      if (action.coldStartId) {
        void AsyncStorage.setItem(COLD_START_NOTIF_KEY, action.coldStartId).catch(() => {})
      }
    } else {
      // Pass coldStartId into pendingRoute so the flush effect writes AsyncStorage
      // after the push — not before.
      pendingRoute.current = { route: action.route, coldStartId: action.coldStartId }
    }
  }, [isAuthenticated, setUnreadCount])

  // Register foreground display config and the background/foreground tap listener.
  useEffect(() => {
    Notifications.setNotificationHandler({
      handleNotification: () =>
        Promise.resolve({
          shouldShowAlert: true,
          shouldPlaySound: true,
          shouldSetBadge: false,
          shouldShowBanner: true,
          shouldShowList: true,
        }),
    })

    function handleResponse(response: Notifications.NotificationResponse): void {
      const identifier = response.notification.request.identifier
      if (processedIds.current.has(identifier)) return
      processedIds.current.add(identifier)

      const productId = extractProductId(response)
      const notificationId = extractNotificationId(response)

      if (!isAuthRef.current) {
        if (productId) {
          pendingAfterAuth.current = { route: `/product/${productId}`, notificationId, coldStartId: null }
        }
        return
      }

      if (notificationId) {
        markReadAndRefreshBadge(notificationId, setUnreadCount)
      }
      if (productId) {
        queueOrNavigate(`/product/${productId}`)
      }
    }

    const subscription = Notifications.addNotificationResponseReceivedListener(handleResponse)
    return () => { subscription.remove() }
  }, [queueOrNavigate, setUnreadCount])

  // Cold-start check: runs exactly once on mount.
  useEffect(() => {
    let cancelled = false

    async function handleColdStart(): Promise<void> {
      const response = await Notifications.getLastNotificationResponseAsync()
      if (cancelled || !response) return

      const identifier = response.notification.request.identifier

      const alreadyProcessed = await AsyncStorage.getItem(COLD_START_NOTIF_KEY)
      if (cancelled || alreadyProcessed === identifier) return

      if (processedIds.current.has(identifier)) return
      processedIds.current.add(identifier)

      const productId = extractProductId(response)
      const notificationId = extractNotificationId(response)

      if (!isAuthRef.current) {
        // Unauthenticated: defer to login. Do NOT write to AsyncStorage — the
        // identifier will be persisted by the auth-restoration path once navigation
        // is actually dispatched, so killing the app before sign-in leaves the
        // notification re-processable on the next cold start.
        if (productId) {
          pendingAfterAuth.current = {
            route: `/product/${productId}`,
            notificationId,
            coldStartId: identifier,
          }
        }
        return
      }

      // Authenticated path.
      if (notificationId) {
        markReadAndRefreshBadge(notificationId, setUnreadCount)
      }

      if (productId) {
        if (isNavReadyRef.current) {
          router.push(`/product/${productId}`)
          // Nav dispatched synchronously — safe to persist immediately.
          // (Falls through to the setItem below.)
        } else {
          // Nav not ready: bundle the route and identifier into pendingRoute so the
          // flush effect persists after router.push fires, not before. An app kill
          // before the navigator initialises leaves the identifier absent from storage
          // and the notification re-processable on the next cold start.
          pendingRoute.current = { route: `/product/${productId}`, coldStartId: identifier }
          return  // skip setItem
        }
      }

      // Reached when: nav was dispatched synchronously, OR there is no productId.
      try {
        await AsyncStorage.setItem(COLD_START_NOTIF_KEY, identifier)
      } catch {
        // Non-critical — worst case is a harmless duplicate on the next cold start
      }
    }

    void handleColdStart()
    return () => { cancelled = true }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
}
