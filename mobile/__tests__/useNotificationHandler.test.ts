/**
 * Story #93 — useNotificationHandler unit tests.
 *
 * Covers 19 scenarios:
 *  1.  setNotificationHandler is called on mount
 *  2.  Foreground handler config: shouldShowAlert=true, shouldSetBadge=false
 *  3.  Background tap with valid product_id navigates to product screen
 *  4.  Background tap marks notification as read via API
 *  5.  Background tap refreshes unread badge count
 *  6.  Tap with missing product_id does not navigate
 *  7.  Tap with non-UUID product_id does not navigate
 *  8.  Tap with null notification_id skips mark-read call
 *  9.  Duplicate tap (same identifier) is processed only once
 * 10.  Cold-start notification is handled on mount
 * 11.  Subscription is cleaned up on unmount
 * 12.  Navigation fires immediately, concurrent with mark-as-read (non-blocking)
 * 13.  Cold-start stale notification (already processed) is not replayed
 * 14.  Cold-start navigation is deferred when navigator not ready, fires when ready
 * 15.  Unauthenticated tap: action stored pending auth, no immediate navigation or API call
 * 16.  Auth restored: pending unauthed-tap action triggers navigation and mark-as-read
 * 17.  Background tap: navigation deferred when navigator not ready, fires when ready
 * 18.  Cold-start: identifier saved to AsyncStorage AFTER navigation is queued, not before
 * 19.  Cold-start async is cancelled on unmount: no navigation or storage writes
 */

import { renderHook, waitFor } from '@testing-library/react-native'
import * as Notifications from 'expo-notifications'
import { router, useRootNavigationState } from 'expo-router'
import AsyncStorage from '@react-native-async-storage/async-storage'

// ── Mocks ─────────────────────────────────────────────────────────────────────

jest.mock('expo-notifications', () => ({
  setNotificationHandler: jest.fn(),
  addNotificationResponseReceivedListener: jest.fn(),
  getLastNotificationResponseAsync: jest.fn(),
}))

jest.mock('expo-router', () => ({
  router: { push: jest.fn() },
  useRootNavigationState: jest.fn(),
}))

jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
}))

jest.mock('../src/api/client', () => ({
  __esModule: true,
  default: {
    put: jest.fn(),
    get: jest.fn(),
  },
}))

jest.mock('../src/store/store', () => ({
  useStore: jest.fn(),
}))

// Imports must follow mock declarations (jest.mock is hoisted; imports are not)
import apiClient from '../src/api/client'
import { useStore } from '../src/store/store'
import { useNotificationHandler } from '../src/notifications/useNotificationHandler'

// ── Helpers ───────────────────────────────────────────────────────────────────

const VALID_PRODUCT_UUID = '11111111-1111-1111-1111-111111111111'
const VALID_NOTIF_UUID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'

const NAV_READY = { key: 'test-nav-key', routes: [], index: 0, type: 'stack', stale: false }

const mockSetUnreadCount = jest.fn()

function setupStoreMock(isAuthenticated = true) {
  ;(useStore as unknown as jest.Mock).mockImplementation(
    (selector: (s: { setUnreadCount: jest.Mock; isAuthenticated: boolean }) => unknown) =>
      selector({ setUnreadCount: mockSetUnreadCount, isAuthenticated }),
  )
}

function makeResponse(overrides: {
  identifier?: string
  productId?: string | null  // null = omit product_id from data
  notificationId?: string | null
} = {}): Notifications.NotificationResponse {
  const {
    identifier = 'response-id-1',
    productId = VALID_PRODUCT_UUID,
    notificationId = VALID_NOTIF_UUID,
  } = overrides

  const data: Record<string, unknown> = {
    store_id: 'store-1',
    price_cents: '450',
    trigger_type: 'price_match',
  }
  if (productId !== null) data.product_id = productId
  if (notificationId !== null) data.notification_id = notificationId

  return {
    notification: {
      request: { identifier, content: { data } },
    },
    actionIdentifier: 'expo.modules.notifications.actions.DEFAULT',
  } as unknown as Notifications.NotificationResponse
}

// ── Setup ─────────────────────────────────────────────────────────────────────

let capturedListener: ((r: Notifications.NotificationResponse) => void) | null = null
const mockRemove = jest.fn()

beforeEach(() => {
  jest.clearAllMocks()
  capturedListener = null

  setupStoreMock(true)
  ;(useRootNavigationState as jest.Mock).mockReturnValue(NAV_READY)
  ;(Notifications.addNotificationResponseReceivedListener as jest.Mock).mockImplementation((cb) => {
    capturedListener = cb
    return { remove: mockRemove }
  })
  ;(Notifications.getLastNotificationResponseAsync as jest.Mock).mockResolvedValue(null)
  ;(AsyncStorage.getItem as jest.Mock).mockResolvedValue(null)
  ;(AsyncStorage.setItem as jest.Mock).mockResolvedValue(undefined)
  ;(apiClient.put as jest.Mock).mockResolvedValue({ data: {} })
  ;(apiClient.get as jest.Mock).mockResolvedValue({ data: { count: 3 } })
})

// ── Tests ─────────────────────────────────────────────────────────────────────

it('1 — registers setNotificationHandler on mount', () => {
  renderHook(() => useNotificationHandler())
  expect(Notifications.setNotificationHandler).toHaveBeenCalledTimes(1)
})

it('2 — foreground handler shows alert, plays sound, does not set badge', async () => {
  renderHook(() => useNotificationHandler())
  const [config] = (Notifications.setNotificationHandler as jest.Mock).mock.calls[0]
  const result = await (config as { handleNotification: () => Promise<Notifications.NotificationBehavior> }).handleNotification()
  expect(result.shouldShowAlert).toBe(true)
  expect(result.shouldPlaySound).toBe(true)
  expect(result.shouldSetBadge).toBe(false)
})

it('3 — background tap with valid product_id navigates to product screen', async () => {
  renderHook(() => useNotificationHandler())
  capturedListener!(makeResponse())
  await waitFor(() => {
    expect(router.push).toHaveBeenCalledWith(`/product/${VALID_PRODUCT_UUID}`)
  })
})

it('4 — background tap marks notification as read via API', async () => {
  renderHook(() => useNotificationHandler())
  capturedListener!(makeResponse())
  await waitFor(() => {
    expect(apiClient.put).toHaveBeenCalledWith(`/api/v1/notifications/${VALID_NOTIF_UUID}/read`)
  })
})

it('5 — background tap refreshes unread badge count from API', async () => {
  renderHook(() => useNotificationHandler())
  capturedListener!(makeResponse())
  await waitFor(() => {
    expect(apiClient.get).toHaveBeenCalledWith('/api/v1/notifications/unread-count')
    expect(mockSetUnreadCount).toHaveBeenCalledWith(3)
  })
})

it('6 — tap with missing product_id does not navigate', async () => {
  renderHook(() => useNotificationHandler())
  capturedListener!(makeResponse({ productId: null }))
  await new Promise<void>((r) => setTimeout(r, 0))
  expect(router.push).not.toHaveBeenCalled()
})

it('7 — tap with non-UUID product_id does not navigate', async () => {
  renderHook(() => useNotificationHandler())
  capturedListener!(makeResponse({ productId: 'not-a-uuid' }))
  await new Promise<void>((r) => setTimeout(r, 0))
  expect(router.push).not.toHaveBeenCalled()
})

it('8 — tap with null notification_id skips mark-read API call', async () => {
  renderHook(() => useNotificationHandler())
  capturedListener!(makeResponse({ notificationId: null }))
  await new Promise<void>((r) => setTimeout(r, 0))
  expect(apiClient.put).not.toHaveBeenCalled()
})

it('9 — duplicate tap with same identifier is processed only once', async () => {
  renderHook(() => useNotificationHandler())
  const response = makeResponse()
  capturedListener!(response)
  capturedListener!(response)
  await waitFor(() => {
    expect(router.push).toHaveBeenCalledTimes(1)
  })
})

it('10 — cold-start notification is handled on mount', async () => {
  const coldResponse = makeResponse({ identifier: 'cold-start-unique-id' })
  ;(Notifications.getLastNotificationResponseAsync as jest.Mock).mockResolvedValue(coldResponse)
  renderHook(() => useNotificationHandler())
  await waitFor(() => {
    expect(router.push).toHaveBeenCalledWith(`/product/${VALID_PRODUCT_UUID}`)
  })
})

it('11 — notification listener subscription is removed on unmount', () => {
  const { unmount } = renderHook(() => useNotificationHandler())
  unmount()
  expect(mockRemove).toHaveBeenCalledTimes(1)
})

it('12 — navigation fires immediately even when mark-as-read request is pending', async () => {
  // put never resolves — navigation must not wait for it
  ;(apiClient.put as jest.Mock).mockReturnValue(new Promise(() => {}))

  renderHook(() => useNotificationHandler())
  capturedListener!(makeResponse())

  await waitFor(() => {
    expect(router.push).toHaveBeenCalledWith(`/product/${VALID_PRODUCT_UUID}`)
  })
  // Confirm navigation happened while put is still pending
  expect(apiClient.put).toHaveBeenCalledTimes(1)
})

it('13 — cold-start: stale notification already processed in a previous session is not replayed', async () => {
  const identifier = 'previously-seen-id'
  ;(Notifications.getLastNotificationResponseAsync as jest.Mock).mockResolvedValue(
    makeResponse({ identifier }),
  )
  ;(AsyncStorage.getItem as jest.Mock).mockResolvedValue(identifier)  // already stored

  renderHook(() => useNotificationHandler())

  await new Promise<void>((r) => setTimeout(r, 50))
  expect(router.push).not.toHaveBeenCalled()
  expect(AsyncStorage.setItem).not.toHaveBeenCalled()
})

it('14 — cold-start: navigation deferred when navigator not ready, fires when it becomes ready', async () => {
  ;(useRootNavigationState as jest.Mock)
    .mockReturnValueOnce(undefined)    // not ready on first render
    .mockReturnValue(NAV_READY)        // ready on subsequent renders

  const coldResponse = makeResponse({ identifier: 'cold-deferred-id' })
  ;(Notifications.getLastNotificationResponseAsync as jest.Mock).mockResolvedValue(coldResponse)

  const { rerender } = renderHook(() => useNotificationHandler())

  // Wait for the cold-start async to write to AsyncStorage (proves processing completed)
  await waitFor(() => {
    expect(AsyncStorage.setItem).toHaveBeenCalledWith(
      '@dealfindio/cold_start_notif_id',
      'cold-deferred-id',
    )
  })

  // Navigation must NOT have fired yet (navigator was not ready when cold-start ran)
  expect(router.push).not.toHaveBeenCalled()

  // Simulate navigator becoming ready
  rerender(undefined)

  await waitFor(() => {
    expect(router.push).toHaveBeenCalledWith(`/product/${VALID_PRODUCT_UUID}`)
  })
})

it('15 — unauthenticated tap: action stored pending auth; no navigation or API calls before auth restored', async () => {
  setupStoreMock(false)  // isAuthenticated = false

  renderHook(() => useNotificationHandler())
  capturedListener!(makeResponse())

  await new Promise<void>((r) => setTimeout(r, 0))
  expect(router.push).not.toHaveBeenCalled()
  expect(apiClient.put).not.toHaveBeenCalled()
})

it('16 — auth restored: pending unauthed-tap action triggers navigation and mark-as-read', async () => {
  setupStoreMock(false)  // start unauthenticated

  const { rerender } = renderHook(() => useNotificationHandler())
  capturedListener!(makeResponse())

  // Unauthenticated: no immediate navigation or API calls
  await new Promise<void>((r) => setTimeout(r, 0))
  expect(router.push).not.toHaveBeenCalled()
  expect(apiClient.put).not.toHaveBeenCalled()

  // User signs in
  setupStoreMock(true)
  rerender(undefined)

  await waitFor(() => {
    expect(router.push).toHaveBeenCalledWith(`/product/${VALID_PRODUCT_UUID}`)
    expect(apiClient.put).toHaveBeenCalledWith(
      `/api/v1/notifications/${VALID_NOTIF_UUID}/read`,
    )
  })
})

it('17 — background tap: navigation deferred when navigator not ready, fires when navigator becomes ready', async () => {
  ;(useRootNavigationState as jest.Mock)
    .mockReturnValueOnce(undefined)  // not ready on first render
    .mockReturnValue(NAV_READY)      // ready on subsequent renders

  const { rerender } = renderHook(() => useNotificationHandler())
  capturedListener!(makeResponse())

  // Navigator was not ready at tap time — push must not have been called
  await new Promise<void>((r) => setTimeout(r, 0))
  expect(router.push).not.toHaveBeenCalled()

  // Simulate navigator becoming ready
  rerender(undefined)

  await waitFor(() => {
    expect(router.push).toHaveBeenCalledWith(`/product/${VALID_PRODUCT_UUID}`)
  })
})

it('18 — cold-start: identifier saved to AsyncStorage AFTER navigation is queued, not before', async () => {
  const sequence: string[] = []
  ;(router.push as jest.Mock).mockImplementation(() => { sequence.push('push') })
  ;(AsyncStorage.setItem as jest.Mock).mockImplementation(() => {
    sequence.push('setItem')
    return Promise.resolve()
  })

  const coldResponse = makeResponse({ identifier: 'ordering-test-id' })
  ;(Notifications.getLastNotificationResponseAsync as jest.Mock).mockResolvedValue(coldResponse)

  renderHook(() => useNotificationHandler())

  await waitFor(() => {
    expect(sequence).toContain('setItem')
  })

  const pushIdx = sequence.indexOf('push')
  const setItemIdx = sequence.indexOf('setItem')
  expect(pushIdx).toBeGreaterThanOrEqual(0)
  expect(setItemIdx).toBeGreaterThan(pushIdx)
})

it('19 — cold-start async is cancelled on unmount: no navigation or storage writes after unmount', async () => {
  let resolveColdStart!: (r: Notifications.NotificationResponse | null) => void
  ;(Notifications.getLastNotificationResponseAsync as jest.Mock).mockReturnValue(
    new Promise((resolve) => { resolveColdStart = resolve }),
  )

  const { unmount } = renderHook(() => useNotificationHandler())
  unmount()  // triggers cleanup → cancellation flag set to true

  // Response arrives after unmount — must be completely discarded
  resolveColdStart(makeResponse({ identifier: 'post-unmount-id' }))

  await new Promise<void>((r) => setTimeout(r, 50))
  expect(router.push).not.toHaveBeenCalled()
  expect(AsyncStorage.getItem).not.toHaveBeenCalled()
  expect(AsyncStorage.setItem).not.toHaveBeenCalled()
})
