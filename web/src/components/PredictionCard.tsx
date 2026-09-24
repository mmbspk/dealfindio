import React from 'react'

export interface Prediction {
  id: string
  chain: string | null
  predicted_price_aud: number
  prediction_week: string
  ci_low_aud: number | null
  ci_high_aud: number | null
  is_likely_sale: boolean
  model_version: string | null
}

interface PredictionCardProps {
  prediction: Prediction
}

function weekLabel(isoDate: string): string {
  const d = new Date(isoDate)
  return d.toLocaleDateString('en-AU', { day: 'numeric', month: 'short' })
}

export const PredictionCard: React.FC<PredictionCardProps> = ({ prediction }) => {
  const { chain, predicted_price_aud, prediction_week, is_likely_sale, ci_low_aud, ci_high_aud } = prediction

  return (
    <div
      className={`border rounded-lg p-4 flex items-start justify-between gap-4 transition ${
        is_likely_sale
          ? 'border-emerald-300 bg-emerald-50/40'
          : 'border-gray-200 bg-white'
      }`}
    >
      <div className="flex-1 min-w-0">
        <p className="font-semibold text-gray-900 text-sm">{chain ?? 'All chains'}</p>
        <p className="text-xs text-gray-500 mt-0.5">Week of {weekLabel(prediction_week)}</p>
        {ci_low_aud != null && ci_high_aud != null && (
          <p className="text-xs text-gray-400 mt-1">
            Range: ${ci_low_aud.toFixed(2)} – ${ci_high_aud.toFixed(2)}
          </p>
        )}
      </div>

      <div className="text-right flex-shrink-0">
        <p className={`text-xl font-bold ${is_likely_sale ? 'text-emerald-600' : 'text-gray-800'}`}>
          ${predicted_price_aud.toFixed(2)}
        </p>
        {is_likely_sale && (
          <span className="inline-block mt-1 px-2 py-0.5 text-xs font-semibold bg-emerald-100 text-emerald-700 rounded-full">
            Expected Sale
          </span>
        )}
      </div>
    </div>
  )
}
