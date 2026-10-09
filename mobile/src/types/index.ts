/**
 * Shared TypeScript types for the dealfindio mobile app.
 * Mirrors the web app's types and the backend API response shapes.
 */

export interface Product {
  id: string
  gtin: string | null
  name: string
  brand: string | null
  category: string | null
}

export interface Store {
  id: string
  chain: string
  name: string
  suburb: string | null
  state: string | null
  postcode: string | null
  latitude: number
  longitude: number
  distance_km?: number
}

export interface PricePoint {
  date: string
  price: number
  chain: string
}

export interface PriceReport {
  id: string
  product_id: string
  product_name: string
  store_id: string
  store_name: string
  price_aud: number
  was_price_aud: number | null
  confidence: number
  is_verified: boolean
  reporter_id: string
  created_at: string
}

export type ChannelOverride = 'push' | 'email' | 'both' | 'none' | null

export interface WatchlistList {
  id: string
  user_id: string
  name: string
  emoji: string | null
  channel_override: ChannelOverride
  item_count: number
  created_at: string
}

export interface WatchlistItem {
  id: string
  user_id: string
  list_id: string | null
  product_id: string
  product_name: string | null
  product_image_url: string | null
  product_brand: string | null
  product_category: string | null
  current_price_aud: number | null
  current_chain: string | null
  is_on_sale: boolean
  target_price_cents: number | null
  target_pct_off: number | null
  alert_on_prediction: boolean
  preferred_chains: string[] | null
  is_active: boolean
  created_at: string
}

export interface ProductWatchlistStatus {
  list_id: string
  list_name: string
  list_emoji: string | null
  in_list: boolean
  item_id: string | null
}

export interface Notification {
  id: string
  price_report_id: string | null
  trigger_type: string
  message_title: string
  message_body: string
  sent_at: string
  opened_at: string | null
  product_name?: string
  store_name?: string
}

export interface UserProfile {
  id: string
  email: string
  reputation_score: number
  tier: 'bronze' | 'silver' | 'gold'
  contribution_count: number
}

/** Payload for submitting a community price report. */
export interface PriceReportSubmission {
  product_gtin?: string
  product_name?: string
  store_id: string
  price_cents: number
  was_price_cents?: number
  latitude: number
  longitude: number
}
