import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { Product } from '../types'
import { formatPackageSize } from '../lib/format'
import { ChainBadgeList } from './ChainBadge'

interface ProductCardProps {
  product: Product
}

export const ProductCard: React.FC<ProductCardProps> = ({ product }) => {
  const [imgError, setImgError] = useState(false)
  const packSize = formatPackageSize(product.package_size)
  const displayImage = product.external_image_url || product.image_url
  const isOffImage = !product.external_image_url && product.image_source === 'open_food_facts'

  return (
    <Link to={`/product/${product.id}`}>
      <div className="bg-white rounded-lg shadow-md hover:shadow-xl transition cursor-pointer h-full p-6 hover:scale-105 transform duration-200">
        <div className="aspect-square bg-gradient-to-br from-gray-100 to-gray-200 rounded-lg mb-4 flex items-center justify-center overflow-hidden relative">
          {displayImage && !imgError ? (
            <img
              src={displayImage}
              alt={product.name}
              className="w-full h-full object-contain"
              onError={() => setImgError(true)}
              loading="lazy"
            />
          ) : (
            <svg className="w-16 h-16 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
            </svg>
          )}
          {isOffImage && (
            <span className="absolute bottom-1 right-1 bg-green-600/80 text-white text-[9px] font-bold px-1 py-0.5 rounded leading-none pointer-events-none">
              OFF
            </span>
          )}
        </div>

        <h3 className="font-semibold text-lg text-gray-900 truncate" title={product.name}>
          {product.name}
        </h3>

        {product.brand && (
          <p className="text-sm text-gray-600 mt-1 truncate">{product.brand}</p>
        )}

        {packSize && (
          <p className="text-xs font-semibold text-gray-700 bg-gray-100 border border-gray-200 px-2 py-0.5 rounded mt-1 inline-block">{packSize}</p>
        )}

        <ChainBadgeList chains={product.available_chains} size="xs" />

        {product.unit_price != null && (
          <p className="text-xs text-gray-400 mt-0.5">
            ${product.unit_price.toFixed(2)} / {product.unit_price_label ?? 'unit'}
          </p>
        )}

        {product.category && (
          <span className="inline-block mt-3 px-3 py-1 bg-emerald-100 text-emerald-700 text-xs font-medium rounded-full">
            {product.category}
          </span>
        )}

        <div className="mt-4 flex items-center justify-between">
          <span className="text-xs text-gray-500">View details</span>
          <svg className="w-4 h-4 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
        </div>
      </div>
    </Link>
  )
}
