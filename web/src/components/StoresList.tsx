import React from 'react'

export interface StoreItem {
  id: string
  chain: string
  name: string
  address?: string
  suburb?: string
  state?: string
  postcode?: string
  lat?: number
  lon?: number
  price_aud: number
  is_on_sale: boolean
  discount_pct?: number | null
  distance_km?: number | null
}

interface StoresListProps {
  stores?: StoreItem[]
}

export const StoresList: React.FC<StoresListProps> = ({ stores = [] }) => {
  if (stores.length === 0) {
    return (
      <div className="text-center py-8 text-gray-500">
        <p>No stores available</p>
      </div>
    )
  }

  return (
    <div className="bg-white rounded-lg shadow-md overflow-hidden">
      <div className="p-6">
        <h3 className="text-lg font-semibold text-gray-900 mb-4">Available Stores</h3>

        <div className="space-y-3">
          {stores.map((store) => {
            const locationLine = [store.suburb, store.state].filter(Boolean).join(', ')
            return (
              <div
                key={store.id}
                className="flex items-center justify-between p-4 border border-gray-200 rounded-lg hover:border-emerald-300 transition"
              >
                <div className="flex-1 min-w-0">
                  <p className="font-semibold text-gray-900 truncate">{store.name}</p>
                  <p className="text-sm text-gray-500">{store.chain}</p>
                  {locationLine && (
                    <p className="text-sm text-gray-500">{locationLine}</p>
                  )}
                  {store.distance_km != null && (
                    <p className="text-xs text-emerald-500 mt-0.5">
                      {store.distance_km.toFixed(1)} km away
                    </p>
                  )}
                </div>

                <div className="text-right ml-4 flex-shrink-0">
                  <p className="text-xl font-bold text-gray-900">${store.price_aud.toFixed(2)}</p>
                  {store.is_on_sale ? (
                    <span className="inline-block mt-1 px-2 py-0.5 bg-emerald-100 text-emerald-700 text-xs font-semibold rounded-full">
                      {store.discount_pct ? `${Math.round(store.discount_pct)}% off` : 'On Sale'}
                    </span>
                  ) : (
                    <span className="inline-block mt-1 px-2 py-0.5 bg-gray-100 text-gray-500 text-xs rounded-full">
                      Regular price
                    </span>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
