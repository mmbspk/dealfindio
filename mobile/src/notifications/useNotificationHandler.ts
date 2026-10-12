import { useEffect, useRef } from 'react'
import * as Notifications from 'expo-notifications'
import { router, useRootNavigationState } from 'expo-router'
import AsyncStorage from '@react-native-async-storage/async-storage'
import apiClient from '@/api/client'
import { useStore } from '@/store/store'
import type { PushNotificationPayload } from '@/types'

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const COLD_START_NOTIF_KEY = '@dealfindio/cold_start_notif_id'

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
 */
export function useNotificationHandler(): void {
  const setUnreadCount = useStore((s) => s.setUnreadCount)
  const isAuthenticated = useStore((s) => s.isAuthenticated)

  const processedIds = useRef<Set<string>>(new Set())
  const pendingRoute = useRef<string | null>(null)

  // Refs mirror reactive values for use inside async callbacks — avoids stale closures.
  const isAuthRef = useRef(isAuthenticated)
  const isNavReadyRef = useRef(false)

  const rootNavState = useRootNavigationState()
  const isNavReady = !!rootNavState?.key

  // Keep refs current with every render (assignment in render phase is the React-recommended
  // pattern for "get latest value inside a callback without re-registering the callback").
  isAuthRef.current = isAuthenticated
  isNavReadyRef.current = isNavReady

  // Once the navigator is ready, execute any cold-start navigation that was deferred
  // because the navigator had not yet initialised.
  useEffect(() => {
    if (isNavReady && pendingRoute.current) {
      const route = pendingRoute.current
      pendingRoute.current = null
      router.push(route)
    }
  }, [isNavReady])

  // Register the foreground display config and the background/foreground tap listener.
  // setUnreadCount is a stable Zustand action so this effect runs only on mount.
  useEffect(() => {
    Notifications.setNotificationHandler({
      handleNotification: async () => ({
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

      // Launch mark-as-read concurrently — NEVER awaited before navigation.
      // Auth check avoids a guaranteed-401 fire when the user is not signed in.
      if (notificationId && isAuthRef.current) {
        markReadAndRefreshBadge(notificationId, setUnreadCount)
      }

      // Navigate immediately — does not wait for any network operation.
      if (productId) {
        router.push(`/product/${productId}`)
      }
    }

    const subscription = Notifications.addNotificationResponseReceivedListener(
      (response) => { handleResponse(response) },
    )

    return () => {
      subscription.remove()
    }
  }, [setUnreadCount])

  // Cold-start check: runs exactly once on mount.
  // Uses refs (not state) for auth and nav-readiness to avoid stale closure issues
  // inside the async callback.
  useEffect(() => {
    async function handleColdStart(): Promise<void> {
      const response = await Notifications.getLastNotificationResponseAsync()
      if (!response) return

      const identifier = response.notification.request.identifier

      // Guard against replaying a notification from a previous app session.
      // If AsyncStorage is unavailable, skip to avoid the risk of stale replay.
      try {
        const alreadyProcessed = await AsyncStorage.getItem(COLD_START_NOTIF_KEY)
        if (alreadyProcessed === identifier) return
        await AsyncStorage.setItem(COLD_START_NOTIF_KEY, identifier)
      } catch {
        return
      }

      if (processedIds.current.has(identifier)) return
      processedIds.current.add(identifier)

      const productId = extractProductId(response)
      const notificationId = extractNotificationId(response)

      if (notificationId && isAuthRef.current) {
        markReadAndRefreshBadge(notificationId, setUnreadCount)
      }

      if (productId) {
        if (isNavReadyRef.current) {
          router.push(`/product/${productId}`)
        } else {
          // Navigator not yet ready — defer until the isNavReady effect fires.
          pendingRoute.current = `/product/${productId}`
        }
      }
    }

    void handleColdStart()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
}
