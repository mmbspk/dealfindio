import React from 'react'
import { Link } from 'react-router-dom'
import type { Deal } from '../types'
import { SaveToListButton } from './SaveToListButton'

type ViewMode = 'compact' | 'comfortable' | 'list'

interface Props {
  deal: Deal
  viewMode?: ViewMode
}

const PlaceholderIcon = ({ className }: { className: string }) => (
  <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
  </svg>
)

export const DealCard: React.FC<Props> = ({ deal, viewMode = 'list' }) => {
  const pctOff = deal.discount_pct
    ? Math.round(deal.discount_pct)
    : deal.was_price_aud && deal.was_price_aud > 0
    ? Math.round(((deal.was_price_aud - deal.price_aud) / deal.was_price_aud) * 100)
    : null
  const chainLabel = deal.chain
    ? deal.chain.charAt(0).toUpperCase() + deal.chain.slice(1)
    : deal.store_name

  // ── Compact: small square tile ──────────────────────────────────────────
  if (viewMode === 'compact') {
    return (
      <div className="relative group">
        <Link
          to={`/product/${deal.product_id}`}
          className="block bg-white border border-gray-100 rounded-2xl p-3 hover:shadow-md hover:border-[#a8cdb1] transition flex flex-col"
        >
          <div className="aspect-square bg-gray-50 rounded-lg mb-2 overflow-hidden flex items-center justify-center relative">
            {deal.image_url ? (
              <img src={deal.image_url} alt={deal.product_name} className="w-full h-full object-contain group-hover:scale-105 transition-transform" loading="lazy" />
            ) : (
              <PlaceholderIcon className="w-8 h-8 text-gray-200" />
            )}
            {pctOff && pctOff > 0 && (
              <span className="absolute top-1.5 left-1.5 bg-red-500 text-white text-xs font-bold px-1.5 py-0.5 rounded">
                -{pctOff}%
              </span>
            )}
          </div>
          <p className="text-xs font-semibold text-gray-900 leading-snug line-clamp-2 group-hover:text-[#176f50] transition">
            {deal.product_name}
          </p>
          {deal.brand && <p className="text-xs text-gray-400 mt-0.5 truncate">{deal.brand}</p>}
          <p className="text-xs text-gray-400 mt-0.5">{chainLabel}</p>
          <div className="mt-1.5 flex items-baseline gap-1 flex-wrap">
            <span className="text-sm font-bold text-[#176f50]">${deal.price_aud.toFixed(2)}</span>
            {deal.was_price_aud && (
              <span className="text-xs text-gray-400 line-through">${deal.was_price_aud.toFixed(2)}</span>
            )}
          </div>
        </Link>
        <div className="absolute top-2 right-2 z-10">
          <SaveToListButton productId={deal.product_id} size="sm" />
        </div>
      </div>
    )
  }

  // ── Comfortable: medium tile (2-col grid on desktop) ────────────────────
  if (viewMode === 'comfortable') {
    return (
      <div className="relative group">
        <Link
          to={`/product/${deal.product_id}`}
          className="block bg-white border border-gray-100 rounded-2xl p-4 hover:shadow-md hover:border-[#a8cdb1] transition flex flex-col h-full"
        >
          <div className="aspect-square bg-gray-50 rounded-lg mb-3 overflow-hidden flex items-center justify-center relative">
            {deal.image_url ? (
              <img src={deal.image_url} alt={deal.product_name} className="w-full h-full object-contain group-hover:scale-105 transition-transform" loading="lazy" />
            ) : (
              <PlaceholderIcon className="w-12 h-12 text-gray-200" />
            )}
            {pctOff && pctOff > 0 && (
              <span className="absolute top-2 left-2 bg-red-500 text-white text-xs font-bold px-2 py-0.5 rounded">
                -{pctOff}%
              </span>
            )}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold text-gray-900 leading-snug line-clamp-2 group-hover:text-[#176f50] transition mb-1">
              {deal.product_name}
            </p>
            {deal.brand && <p className="text-xs text-gray-400 mb-1">{deal.brand}</p>}
            <p className="text-xs text-gray-500 truncate mb-2">{chainLabel}</p>
            <div className="flex items-baseline gap-2 flex-wrap">
              <span className="text-base font-bold text-[#176f50]">${deal.price_aud.toFixed(2)}</span>
              {deal.was_price_aud && (
                <span className="text-xs text-gray-400 line-through">${deal.was_price_aud.toFixed(2)}</span>
              )}
            </div>
          </div>
        </Link>
        <div className="absolute top-3 right-3 z-10">
          <SaveToListButton productId={deal.product_id} size="sm" />
        </div>
      </div>
    )
  }

  // ── List: horizontal row (default) ──────────────────────────────────────
  return (
    <Link
      to={`/product/${deal.product_id}`}
      className="flex gap-4 items-center bg-white border border-gray-200 rounded-2xl p-4 hover:border-[#a8cdb1] hover:shadow-md transition group"
    >
      <div className="w-16 h-16 flex-shrink-0 bg-gray-50 rounded-lg overflow-hidden flex items-center justify-center border border-gray-100">
        {deal.image_url ? (
          <img src={deal.image_url} alt={deal.product_name} className="w-full h-full object-contain" loading="lazy" />
        ) : (
          <PlaceholderIcon className="w-7 h-7 text-gray-200" />
        )}
      </div>

      <div className="flex-1 min-w-0">
        <p className="font-semibold text-gray-900 leading-tight text-sm group-hover:text-[#176f50] transition line-clamp-2 mb-0.5">
          {deal.product_name}
        </p>
        {deal.brand && <p className="text-xs text-gray-400">{deal.brand}</p>}
        <p className="text-xs text-gray-400">{chainLabel}</p>
        <div className="flex items-baseline gap-2 mt-1.5">
          <span className="text-base font-bold text-[#176f50]">${deal.price_aud.toFixed(2)}</span>
          {deal.was_price_aud && (
            <span className="text-xs text-gray-400 line-through">${deal.was_price_aud.toFixed(2)}</span>
          )}
        </div>
      </div>

      <div className="flex-shrink-0 flex items-center gap-2">
        {pctOff && pctOff > 0 && (
          <span className="px-2 py-0.5 bg-red-100 text-red-600 text-xs font-bold rounded-full whitespace-nowrap">
            -{pctOff}%
          </span>
        )}
        <div onClick={e => { e.preventDefault(); e.stopPropagation() }}>
          <SaveToListButton productId={deal.product_id} size="sm" />
        </div>
      </div>
    </Link>
  )
}
