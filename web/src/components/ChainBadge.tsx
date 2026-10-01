export const CHAIN_META: Record<string, { label: string; colorClass: string }> = {
  coles:      { label: 'Coles',      colorClass: 'bg-red-50 text-red-700 border-red-200' },
  woolworths: { label: 'Woolworths', colorClass: 'bg-green-50 text-green-700 border-green-200' },
  aldi:       { label: 'ALDI',       colorClass: 'bg-blue-50 text-blue-700 border-blue-200' },
  iga:        { label: 'IGA',        colorClass: 'bg-orange-50 text-orange-700 border-orange-200' },
  drakes:     { label: 'Drakes',     colorClass: 'bg-yellow-50 text-yellow-800 border-yellow-200' },
}

interface ChainBadgeProps {
  chain: string
  size?: 'xs' | 'sm'
}

export function ChainBadge({ chain, size = 'sm' }: ChainBadgeProps) {
  const meta = CHAIN_META[chain.toLowerCase()]
  const colorClass = meta?.colorClass ?? 'bg-gray-50 text-gray-700 border-gray-200'
  const label = meta?.label ?? chain
  const sizeClass = size === 'xs' ? 'text-[9px] px-1.5 py-0.5' : 'text-xs px-2 py-0.5'
  return (
    <span className={`inline-block font-semibold rounded border leading-none ${sizeClass} ${colorClass}`}>
      {label}
    </span>
  )
}

interface ChainBadgeListProps {
  chains?: string[] | null
  size?: 'xs' | 'sm'
}

export function ChainBadgeList({ chains, size = 'xs' }: ChainBadgeListProps) {
  if (!chains?.length) return null
  return (
    <div className="flex flex-wrap gap-1 mt-1">
      {chains.map(c => <ChainBadge key={c} chain={c} size={size} />)}
    </div>
  )
}
