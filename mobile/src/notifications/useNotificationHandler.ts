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
 * All three code paths (foreground tap, background tap, cold start) route through
 * the same readiness and authentication gates before navigating.
 */
export function useNotificationHandler(): void {
  const setUnreadCount = useStore((s) => s.setUnreadCount)
  const isAuthenticated = useStore((s) => s.isAuthenticated)

  const processedIds = useRef<Set<string>>(new Set())
  const pendingRoute = useRef<string | null>(null)
  // A tap that arrived while the user was not authenticated — held until login.
  const pendingAfterAuth = useRef<PendingAction | null>(null)

  // Refs mirror reactive values for use inside async callbacks and stable closures.
  const isAuthRef = useRef(isAuthenticated)
  const isNavReadyRef = useRef(false)

  const rootNavState = useRootNavigationState()
  const isNavReady = !!rootNavState?.key

  // Update refs every render so async code always reads the latest values.
  isAuthRef.current = isAuthenticated
  isNavReadyRef.current = isNavReady

  // Shared navigation helper used by all three code paths.
  // useCallback with empty deps — only reads refs and calls stable router import.
  const queueOrNavigate = useCallback((route: string): void => {
    if (isNavReadyRef.current) {
      router.push(route)
    } else {
      pendingRoute.current = route
    }
  }, [])

  // Once the navigator is ready, flush any navigation that was deferred because the
  // navigator had not yet initialised (cold start or early background tap).
  useEffect(() => {
    if (isNavReady && pendingRoute.current) {
      const route = pendingRoute.current
      pendingRoute.current = null
      router.push(route)
    }
  }, [isNavReady])

  // Resume a notification action that arrived while the user was unauthenticated.
  // On false→true auth transition: navigate and reconcile the mark-as-read state.
  useEffect(() => {
    if (!isAuthenticated) return
    const action = pendingAfterAuth.current
    if (!action) return
    pendingAfterAuth.current = null
    if (action.notificationId) {
      markReadAndRefreshBadge(action.notificationId, setUnreadCount)
    }
    queueOrNavigate(action.route)
  }, [isAuthenticated, queueOrNavigate, setUnreadCount])

  // Register foreground display config and the background/foreground tap listener.
  // Both queueOrNavigate and setUnreadCount are stable, so this runs only on mount.
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
        // Unauthenticated: preserve destination and notification ID so navigation
        // and mark-as-read can both fire once the user signs in.
        if (productId) {
          pendingAfterAuth.current = { route: `/product/${productId}`, notificationId }
        }
        return
      }

      // Authenticated path: fire mark-as-read concurrently — NEVER awaited before nav.
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
        // Unauthenticated cold start: defer until login, same as background tap.
        if (productId) {
          pendingAfterAuth.current = { route: `/product/${productId}`, notificationId }
        }
      } else {
        if (notificationId) {
          markReadAndRefreshBadge(notificationId, setUnreadCount)
        }
        if (productId) {
          queueOrNavigate(`/product/${productId}`)
        }
      }

      // Persist AFTER queuing navigation. A crash before this write causes a safe
      // re-process on the next cold start rather than permanently losing the notification.
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
