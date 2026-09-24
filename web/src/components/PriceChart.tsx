import React, { useMemo } from 'react'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts'
import { PricePoint } from '../types'

interface PriceChartProps {
  data: PricePoint[]
}

export const PriceChart: React.FC<PriceChartProps> = ({ data }) => {
  const chartData = useMemo(() => {
    if (!data || data.length === 0) return []

    // Sort by date
    const sorted = [...data].sort((a, b) => new Date(a.date).getTime() - new Date(b.date).getTime())

    // Transform data: group by date, with each chain as a separate field
    const groupedByDate = sorted.reduce(
      (acc, point) => {
        const dateStr = new Date(point.date).toLocaleDateString('en-AU', {
          month: 'short',
          day: 'numeric',
        })
        const existing = acc.find((item) => item.date === dateStr)

        if (existing) {
          existing[point.chain] = point.price
        } else {
          acc.push({
            date: dateStr,
            [point.chain]: point.price,
          })
        }

        return acc
      },
      [] as Record<string, any>[]
    )

    return groupedByDate
  }, [data])

  const chains = useMemo(() => {
    return [...new Set(data.map((p) => p.chain))].sort()
  }, [data])

  const colors = ['#4f46e5', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899']
  const chainColors: Record<string, string> = {}
  chains.forEach((chain, idx) => {
    chainColors[chain] = colors[idx % colors.length]
  })

  const stats = useMemo(() => {
    return chains.map((chain) => {
      const chainPrices = data.filter((p) => p.chain === chain).map((p) => p.price)
      if (chainPrices.length === 0) {
        return { chain, avg: 'N/A', min: 'N/A', max: 'N/A' }
      }

      const avg = (chainPrices.reduce((a, b) => a + b, 0) / chainPrices.length).toFixed(2)
      const min = Math.min(...chainPrices).toFixed(2)
      const max = Math.max(...chainPrices).toFixed(2)

      return { chain, avg, min, max }
    })
  }, [data, chains])

  if (chartData.length === 0) {
    return (
      <div className="text-center py-12">
        <p className="text-gray-600">No price history available</p>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <div className="bg-gray-50 rounded-lg p-4 overflow-x-auto">
          <ResponsiveContainer width="100%" height={400}>
            <LineChart
              data={chartData}
              margin={{ top: 5, right: 30, left: 0, bottom: 60 }}
              aria-label="Price history chart"
            >
              <defs>
                {chains.map((chain) => (
                  <linearGradient key={`grad-${chain}`} id={`grad-${chain}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={chainColors[chain]} stopOpacity={0.3} />
                    <stop offset="95%" stopColor={chainColors[chain]} stopOpacity={0} />
                  </linearGradient>
                ))}
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
              <XAxis
                dataKey="date"
                angle={-45}
                textAnchor="end"
                height={80}
                interval={Math.floor(chartData.length / 6)}
                tick={{ fontSize: 12 }}
              />
              <YAxis
                label={{ value: 'Price ($)', angle: -90, position: 'insideLeft' }}
                tick={{ fontSize: 12 }}
              />
              <Tooltip
                formatter={(value) => {
                  if (typeof value === 'number') {
                    return [`$${value.toFixed(2)}`, 'Price']
                  }
                  return value
                }}
                contentStyle={{
                  backgroundColor: '#fff',
                  border: `1px solid #e5e7eb`,
                  borderRadius: '0.5rem',
                }}
                labelStyle={{ color: '#111827' }}
              />
              <Legend wrapperStyle={{ paddingTop: '20px' }} />

              {chains.map((chain) => (
                <Line
                  key={chain}
                  type="monotone"
                  dataKey={chain}
                  stroke={chainColors[chain]}
                  dot={false}
                  connectNulls
                  strokeWidth={2}
                  name={chain}
                  isAnimationActive={true}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div>
        <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wide mb-3">Price Statistics</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {stats.map((stat) => (
            <div
              key={stat.chain}
              className="bg-gray-50 rounded-xl border border-gray-100 p-4"
            >
              <p className="text-xs font-semibold text-gray-500 capitalize mb-3">{stat.chain}</p>
              <div className="space-y-1.5">
                <div className="flex justify-between items-center">
                  <span className="text-xs text-gray-400">Average</span>
                  <span className="text-base font-bold text-gray-900">
                    {stat.avg === 'N/A' ? stat.avg : `$${stat.avg}`}
                  </span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-xs text-gray-400">Lowest</span>
                  <span className="text-sm font-semibold text-emerald-600">
                    {stat.min === 'N/A' ? stat.min : `$${stat.min}`}
                  </span>
                </div>
                <div className="flex justify-between items-center">
                  <span className="text-xs text-gray-400">Highest</span>
                  <span className="text-sm font-semibold text-red-500">
                    {stat.max === 'N/A' ? stat.max : `$${stat.max}`}
                  </span>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
