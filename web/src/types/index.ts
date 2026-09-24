export interface Product {
  id: string
  gtin: string | null
  name: string
  brand: string | null
  category: string | null
  package_size?: string | null
  unit_price?: number | null
  unit_price_label?: string | null
  image_url?: string | null
  in_stock?: boolean
  external_source?: string | null
  lowest_price_aud?: number | null
  was_price_aud?: number | null
  discount_pct?: number | null
  is_on_sale?: boolean
}

export interface Deal {
  id: string
  product_id: string
  product_name: string
  brand: string | null
  image_url?: string | null
  category?: string | null
  store_name: string
  chain: string
  suburb: string | null
  state: string | null
  price_aud: number
  was_price_aud: number | null
  discount_pct: number | null
  distance_km: number | null
}

export interface PricePoint {
  date: string
  price: number
  chain: string
}

export interface PriceHistory {
  data: PricePoint[]
}

export interface SearchResult {
  products: Product[]
  total: number
}

export interface WatchlistList {
  id: string
  user_id: string
  name: string
  emoji: string | null
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
