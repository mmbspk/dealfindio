import { useEffect, useRef } from 'react'
import * as Notifications from 'expo-notifications'
import { router } from 'expo-router'
import apiClient from '@/api/client'
import { useStore } from '@/store/store'
import type { PushNotificationPayload } from '@/types'

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/**
 * Registers foreground/background/cold-start notification handlers.
 * Call once in the root layout — must be mounted for the lifetime of the app.
 */
export function useNotificationHandler(): void {
  const setUnreadCount = useStore((s) => s.setUnreadCount)
  const processedIds = useRef<Set<string>>(new Set())

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

    async function handleResponse(response: Notifications.NotificationResponse): Promise<void> {
      const identifier = response.notification.request.identifier
      if (processedIds.current.has(identifier)) return
      processedIds.current.add(identifier)

      const data = response.notification.request.content.data as PushNotificationPayload | undefined
      const productId = data?.product_id
      const notificationId = data?.notification_id

      if (notificationId) {
        try {
          await apiClient.put(`/api/v1/notifications/${notificationId}/read`)
          const { data: countData } = await apiClient.get<{ count: number }>(
            '/api/v1/notifications/unread-count',
          )
          setUnreadCount(countData.count ?? 0)
        } catch {
          // best-effort — mark-read failures must not block navigation
        }
      }

      if (productId && UUID_RE.test(productId)) {
        router.push(`/product/${productId}`)
      }
    }

    const subscription = Notifications.addNotificationResponseReceivedListener(
      (response) => { void handleResponse(response) },
    )

    void Notifications.getLastNotificationResponseAsync().then((response) => {
      if (response) void handleResponse(response)
    })

    return () => {
      subscription.remove()
    }
  }, [setUnreadCount])
}
