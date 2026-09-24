import React, { useEffect, useRef, useState, useCallback, forwardRef, useImperativeHandle } from 'react'
import apiClient from '../api/client'

const ALL_CHAINS = ['coles', 'woolworths', 'aldi', 'iga', 'drakes', 'dan_murphys'] as const

type CrawlerTab = 'catalog' | 'enrichment' | 'coverage'
type DedupSubTab = 'health' | 'review' | 'run'
type TopTab = 'crawlers' | 'dedup' | 'configs'

// ─── Types ────────────────────────────────────────────────────────────────────

interface FlyerRun {
  id: string; chain: string; status: string
  products_found: number; products_new: number
  run_started_at: string; run_finished_at: string | null
  errors_json: { errors?: string[] } | null
}
interface FlyerProgress { pct: number; stage: string; message: string }
interface FlyerTask { taskId: string; progress: FlyerProgress | null; error: string | null }
interface Stats {
  total_products: number; total_price_reports: number
  products_by_chain: Record<string, number>
  last_scrape_by_chain: Record<string, string | null>
}
interface EnrichResult { chain: string | null; candidates: number; enriched: number; skipped: number; status: string }
interface EnrichTask { taskId: string; state: string; result: EnrichResult | null; error: string | null }
interface CatalogResult { chain: string; mode: string; status: string; categories_found: number; categories_crawled: number; categories_skipped: number; products_found: number; products_new: number; products_updated: number }
interface CatalogTask { taskId: string; mode: string; state: string; result: CatalogResult | null; error: string | null }
interface CatalogConfig { available_chains: string[]; enabled_chains: string[]; schedules: Record<string, string> }
interface CatalogRun { id: string; chain: string; mode: string; status: string; celery_task_id: string | null; categories_found: number; categories_crawled: number; categories_skipped: number; products_found: number; products_new: number; products_updated: number; run_started_at: string; run_finished_at: string | null }
interface ConfigEntry { key: string; label: string; description: string; group: string; is_secret: boolean; is_set: boolean; value: string | null; updated_at: string | null; type?: string; options?: string[] | null; min_value?: number | null; max_value?: number | null; default?: string | null }
interface DedupOverlapRow { chains: string[]; product_count: number }
interface DedupSampleProduct { product_id: string; name: string; chains: string[]; prices: Record<string, number> }
interface DedupStats { total_products: number; products_in_1_chain: number; products_in_2plus_chains: number; products_in_3plus_chains: number; overlap_by_combo: DedupOverlapRow[]; sample_multi_chain: DedupSampleProduct[] }

// ─── Constants ────────────────────────────────────────────────────────────────

// Status → Pill tone (matches the shared emerald/amber/red language).
const STATUS_TONE: Record<string, PillTone> = {
  running: 'amber', completed: 'green', completed_with_errors: 'amber', failed: 'red', cancelled: 'gray',
}
const CHAIN_LOGO: Record<string, string> = { coles: '🔴', woolworths: '🟢', aldi: '🔵', iga: '🟠', dan_murphys: '🟣', drakes: '🟡' }
// Chain → Tailwind badge classes (same convention as pages/Product.tsx & Stores.tsx).
const CHAIN_BADGE: Record<string, string> = {
  coles: 'bg-red-50 text-red-700 border-red-200',
  woolworths: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  aldi: 'bg-blue-50 text-blue-700 border-blue-200',
  iga: 'bg-orange-50 text-orange-700 border-orange-200',
  dan_murphys: 'bg-purple-50 text-purple-700 border-purple-200',
  drakes: 'bg-yellow-50 text-yellow-700 border-yellow-200',
}
const CRAWLER_TABS: { id: CrawlerTab; label: string }[] = [
  { id: 'catalog',    label: 'Catalog Crawl' },
  { id: 'enrichment', label: 'Image Enrichment' },
  { id: 'coverage',   label: 'Coverage' },
]

// ─── Utilities ────────────────────────────────────────────────────────────────

function duration(start: string, end: string | null): string {
  if (!end) return '—'
  let s = Math.round((new Date(end).getTime() - new Date(start).getTime()) / 1000)
  if (s < 60) return `${s}s`
  const parts: string[] = []
  const h = Math.floor(s / 3600); s %= 3600
  const m = Math.floor(s / 60);   s %= 60
  if (h) parts.push(`${h}h`)
  if (m) parts.push(`${m}m`)
  if (s && !h) parts.push(`${s}s`)
  return parts.join(' ')
}

function relativeTime(iso: string | null): string {
  if (!iso) return 'Never'
  let diff = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (diff < 60) return `${diff}s ago`
  const d = Math.floor(diff / 86400); diff %= 86400
  const h = Math.floor(diff / 3600); diff %= 3600
  const m = Math.floor(diff / 60)
  if (d) return h ? `${d}d ${h}h ago` : `${d}d ago`
  if (h) return m ? `${h}h ${m}m ago` : `${h}h ago`
  return `${m}m ago`
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleString('en-AU', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function apiError(e: unknown): string {
  return (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ?? 'Request failed'
}

function titleCase(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1)
}

// ─── Presentational primitives (dense-admin versions of the shared design) ─────

type BtnVariant = 'primary' | 'secondary' | 'danger' | 'ghost'
type BtnSize = 'sm' | 'md'

const BTN_VARIANT: Record<BtnVariant, string> = {
  primary: 'bg-amber-500 text-gray-900 hover:bg-amber-600',
  secondary: 'bg-gray-100 text-gray-700 hover:bg-gray-200',
  danger: 'bg-red-500 text-white hover:bg-red-600',
  ghost: 'bg-transparent text-emerald-600 border border-emerald-200 hover:bg-emerald-50',
}
const BTN_SIZE: Record<BtnSize, string> = {
  sm: 'px-3 py-1.5 text-xs',
  md: 'px-4 py-2 text-sm',
}

function AdmButton({
  variant = 'secondary', size = 'md', className = '', ...rest
}: {
  variant?: BtnVariant; size?: BtnSize; className?: string
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...rest}
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg font-medium whitespace-nowrap transition-colors
        disabled:opacity-50 disabled:cursor-not-allowed ${BTN_VARIANT[variant]} ${BTN_SIZE[size]} ${className}`}
    />
  )
}

type PillTone = 'green' | 'gray' | 'blue' | 'amber' | 'red' | 'indigo'
const PILL_TONE: Record<PillTone, string> = {
  green: 'bg-emerald-100 text-emerald-700',
  gray: 'bg-gray-100 text-gray-600',
  blue: 'bg-blue-100 text-blue-700',
  amber: 'bg-amber-100 text-amber-700',
  red: 'bg-red-100 text-red-700',
  indigo: 'bg-emerald-100 text-emerald-700',
}
function Pill({ tone = 'gray', className = '', children }: { tone?: PillTone; className?: string; children: React.ReactNode }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ${PILL_TONE[tone]} ${className}`}>
      {children}
    </span>
  )
}

function ChainBadge({ chain, size = 'md' }: { chain: string | null; size?: 'sm' | 'md' }) {
  const key = chain ?? 'unknown'
  const cls = CHAIN_BADGE[key] ?? 'bg-gray-50 text-gray-600 border-gray-200'
  const pad = size === 'sm' ? 'px-1.5 py-0.5 text-[10px]' : 'px-2 py-0.5 text-xs'
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border font-semibold ${cls} ${pad}`}>
      <span>{CHAIN_LOGO[key] ?? '🛒'}</span>{titleCase(key)}
    </span>
  )
}

const PlaceholderIcon = ({ className }: { className: string }) => (
  <svg className={className} fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
  </svg>
)

// ─── Shared sub-components ────────────────────────────────────────────────────

function StatCard({ value, label, color }: { value: string; label: string; color: string }) {
  return (
    <div className="bg-white border border-gray-200 rounded-xl shadow-sm px-5 py-4 text-center">
      <div className="text-2xl font-bold" style={{ color }}>{value}</div>
      <div className="text-xs text-gray-500 mt-1">{label}</div>
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  return <Pill tone={STATUS_TONE[status] ?? 'gray'}>{status}</Pill>
}

function RunTable({ rows, columns, maxH = 'max-h-72' }: { rows: React.ReactNode[][]; columns: string[]; maxH?: string }) {
  if (rows.length === 0) return <div className="text-center text-gray-400 py-10 text-sm">No runs yet.</div>
  return (
    <div className={`overflow-auto border border-gray-200 rounded-xl ${maxH}`}>
      <table className="w-full text-sm">
        <thead className="sticky top-0 z-10 bg-white">
          <tr className="border-b border-gray-200">
            {columns.map(c => (
              <th key={c} className="text-left px-3 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-gray-500 whitespace-nowrap">{c}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {rows.map((row, i) => (
            <tr key={i} className="hover:bg-gray-50">
              {row.map((cell, j) => <td key={j} className="px-3 py-2.5 text-gray-700 whitespace-nowrap">{cell}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ChainCard({ chain, isRunning, isDone, isFailed, onDismiss, children }: {
  chain: string; isRunning: boolean; isDone: boolean; isFailed: boolean; onDismiss?: () => void; children: React.ReactNode
}) {
  const ring = isRunning ? 'border-emerald-500' : isFailed ? 'border-red-300' : isDone ? 'border-emerald-300' : 'border-gray-200'
  return (
    <div className={`bg-white border rounded-xl p-4 transition-colors ${ring}`}>
      <div className="flex items-center gap-2 mb-2">
        <span className="text-xl">{CHAIN_LOGO[chain] ?? '🛒'}</span>
        <span className="font-semibold text-sm flex-1">{titleCase(chain)}</span>
        {onDismiss && (
          <button className="text-gray-400 hover:text-gray-700 text-sm leading-none px-1" onClick={onDismiss} title="Dismiss">✕</button>
        )}
      </div>
      {children}
    </div>
  )
}

// A small progress panel used by Scrapers/Enrichment/Catalog cards.
function ProgressPanel({ children }: { children: React.ReactNode }) {
  return <div className="mt-2.5 bg-gray-50 rounded-lg px-3 py-2.5">{children}</div>
}

// ─── Tab: Enrichment ──────────────────────────────────────────────────────────

function EnrichmentTab() {
  const [tasks, setTasks] = useState<Record<string, EnrichTask>>({})
  const pollRefs = useRef<Record<string, ReturnType<typeof setInterval>>>({})

  useEffect(() => () => { Object.values(pollRefs.current).forEach(clearInterval) }, [])

  function startPolling(key: string, taskId: string) {
    if (pollRefs.current[key]) clearInterval(pollRefs.current[key])
    const poll = async () => {
      try {
        const r = await apiClient.get<{ task_id: string; state: string; result: EnrichResult | null }>(`/api/v1/admin/enrich/progress/${taskId}`)
        setTasks(p => ({ ...p, [key]: { taskId, state: r.data.state, result: r.data.result, error: null } }))
        if (r.data.state === 'SUCCESS' || r.data.state === 'FAILURE') {
          clearInterval(pollRefs.current[key]); delete pollRefs.current[key]
        }
      } catch { /* keep polling */ }
    }
    pollRefs.current[key] = setInterval(poll, 2000); void poll()
  }

  async function trigger(chain: string | 'all') {
    setTasks(p => ({ ...p, [chain]: { taskId: '', state: 'PENDING', result: null, error: null } }))
    try {
      const r = await apiClient.post<{ task_id: string }>(chain === 'all' ? '/api/v1/admin/enrich/all' : `/api/v1/admin/enrich/${chain}`)
      setTasks(p => ({ ...p, [chain]: { taskId: r.data.task_id, state: 'PENDING', result: null, error: null } }))
      startPolling(chain, r.data.task_id)
    } catch (e) {
      setTasks(p => ({ ...p, [chain]: { taskId: '', state: 'FAILURE', result: null, error: apiError(e) } }))
    }
  }

  function dismiss(key: string) {
    if (pollRefs.current[key]) { clearInterval(pollRefs.current[key]); delete pollRefs.current[key] }
    setTasks(p => { const n = { ...p }; delete n[key]; return n })
  }

  return (
    <div className="p-6 sm:p-7">
      <div className="flex items-start justify-between gap-5 mb-6">
        <div>
          <h2 className="text-lg font-bold text-gray-900 mb-1.5">Image Enrichment</h2>
          <p className="text-sm text-gray-500 leading-relaxed max-w-xl">Finds products without images, searches Open Food Facts, and re-hosts matches. Runs politely (2–5 s per product).</p>
        </div>
        <AdmButton variant="primary" onClick={() => void trigger('all')}>Enrich All Chains</AdmButton>
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {ALL_CHAINS.map(chain => {
          const task = tasks[chain]
          const running = !!task && task.state !== 'SUCCESS' && task.state !== 'FAILURE'
          return (
            <ChainCard key={chain} chain={chain} isRunning={running} isDone={task?.state === 'SUCCESS'} isFailed={task?.state === 'FAILURE'}
              onDismiss={task && !running ? () => dismiss(chain) : undefined}>
              {task && (
                <ProgressPanel>
                  {task.error ? <div className="text-xs text-red-500">{task.error}</div>
                  : task.state === 'SUCCESS' && task.result ? <div className="text-[11px] text-emerald-600 font-medium">✓ {task.result.enriched} enriched, {task.result.skipped} skipped of {task.result.candidates}</div>
                  : task.state === 'FAILURE' ? <div className="text-xs text-red-500">Task failed</div>
                  : <div className="text-[11px] text-gray-500">⏳ {task.state === 'PENDING' ? 'Queued…' : 'Running…'}</div>}
                </ProgressPanel>
              )}
              <AdmButton variant="secondary" className="w-full mt-2" disabled={running} onClick={() => !running && void trigger(chain)}>
                {running ? '⏳ Enriching…' : 'Enrich'}
              </AdmButton>
            </ChainCard>
          )
        })}
      </div>
    </div>
  )
}

// ─── Tab: Catalog ─────────────────────────────────────────────────────────────

type CatalogSortKey = 'started' | 'duration' | 'new'
type CatalogSortDir = 'asc' | 'desc'

function durationSeconds(run: CatalogRun): number {
  if (!run.run_finished_at) return -1
  return (new Date(run.run_finished_at).getTime() - new Date(run.run_started_at).getTime()) / 1000
}

function CatalogRunHistory({ runs }: { runs: CatalogRun[] }) {
  const [chainF, setChainF] = useState('')
  const [statusF, setStatusF] = useState('')
  const [sortKey, setSortKey] = useState<CatalogSortKey>('started')
  const [sortDir, setSortDir] = useState<CatalogSortDir>('desc')

  const chains = Array.from(new Set(runs.map(r => r.chain))).sort()
  const statuses = Array.from(new Set(runs.map(r => r.status))).sort()

  function toggleSort(key: CatalogSortKey) {
    if (sortKey === key) setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    else { setSortKey(key); setSortDir('desc') }
  }

  const filtered = runs
    .filter(r => (!chainF || r.chain === chainF) && (!statusF || r.status === statusF))
    .sort((a, b) => {
      let diff = 0
      if (sortKey === 'started') diff = new Date(a.run_started_at).getTime() - new Date(b.run_started_at).getTime()
      else if (sortKey === 'duration') diff = durationSeconds(a) - durationSeconds(b)
      else if (sortKey === 'new') diff = a.products_new - b.products_new
      return sortDir === 'desc' ? -diff : diff
    })

  function SortBtn({ k, label }: { k: CatalogSortKey; label: string }) {
    const active = sortKey === k
    return (
      <button onClick={() => toggleSort(k)}
        className={`inline-flex items-center gap-0.5 whitespace-nowrap ${active ? 'text-emerald-700' : 'text-gray-500 hover:text-gray-700'}`}>
        {label}
        <span className="text-[10px]">{active ? (sortDir === 'desc' ? '▾' : '▴') : '⇅'}</span>
      </button>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      {/* Filter + sort bar */}
      <div className="flex flex-wrap items-center gap-2">
        <select value={chainF} onChange={e => setChainF(e.target.value)}
          className="text-xs border border-gray-200 rounded-lg px-2.5 py-1.5 bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-emerald-500">
          <option value="">All chains</option>
          {chains.map(c => <option key={c} value={c}>{CHAIN_LOGO[c] ?? '🛒'} {c}</option>)}
        </select>
        <select value={statusF} onChange={e => setStatusF(e.target.value)}
          className="text-xs border border-gray-200 rounded-lg px-2.5 py-1.5 bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-emerald-500">
          <option value="">All statuses</option>
          {statuses.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <span className="text-[11px] text-gray-400 ml-auto">Sort:</span>
        <div className="inline-flex rounded-lg border border-gray-200 bg-gray-50 px-2.5 py-1.5 gap-3 text-[11px] font-semibold uppercase tracking-wide">
          <SortBtn k="started" label="Started" />
          <SortBtn k="duration" label="Duration" />
          <SortBtn k="new" label="New" />
        </div>
        {(chainF || statusF) && (
          <button onClick={() => { setChainF(''); setStatusF('') }}
            className="text-xs text-gray-400 hover:text-gray-600 underline decoration-dotted">
            Clear
          </button>
        )}
      </div>
      <RunTable
        columns={['Chain', 'Mode', 'Status', 'Started', 'Duration', 'Found', 'New', 'Updated']}
        rows={filtered.map(run => [
          <span key="c">{CHAIN_LOGO[run.chain] ?? '🛒'} {run.chain}</span>,
          <Pill key="m" tone="blue">{run.mode}</Pill>,
          <StatusBadge key="s" status={run.status} />,
          <span key="t">{fmtDate(run.run_started_at)}</span>,
          <span key="d">{duration(run.run_started_at, run.run_finished_at)}</span>,
          <span key="f">{run.products_found.toLocaleString()}</span>,
          <span key="n">{run.products_new.toLocaleString()}</span>,
          <span key="u">{run.products_updated?.toLocaleString() ?? '—'}</span>,
        ])}
      />
      {filtered.length === 0 && runs.length > 0 && (
        <div className="text-center text-gray-400 py-6 text-sm">No runs match the current filters.</div>
      )}
    </div>
  )
}

function CatalogTab() {
  const [config, setConfig] = useState<CatalogConfig | null>(null)
  const [tasks, setTasks] = useState<Record<string, CatalogTask>>({})
  const [runs, setRuns] = useState<CatalogRun[]>([])
  const [productCounts, setProductCounts] = useState<Record<string, number>>({})
  const pollRefs = useRef<Record<string, ReturnType<typeof setInterval>>>({})

  // ALDI flyer scraper state (lives here since there's no separate tab anymore)
  const [flyerTask, setFlyerTask] = useState<FlyerTask | null>(null)
  const [flyerRuns, setFlyerRuns] = useState<FlyerRun[]>([])
  const flyerPollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    void loadConfig(); void loadRuns(); void loadProductCounts(); void recoverActive()
    void loadFlyerRuns(); void recoverFlyerActive()
    const iv = setInterval(() => void loadRuns(), 8000)
    return () => { clearInterval(iv); Object.values(pollRefs.current).forEach(clearInterval); if (flyerPollRef.current) clearInterval(flyerPollRef.current) }
  }, [])

  async function loadProductCounts() {
    try { const r = await apiClient.get<Record<string, number>>('/api/v1/admin/catalog/product-counts'); setProductCounts(r.data) } catch { /* silent */ }
  }
  async function loadConfig() {
    try { const r = await apiClient.get<CatalogConfig>('/api/v1/admin/catalog/config'); setConfig(r.data) } catch { /* silent */ }
  }
  async function loadRuns() {
    try {
      const r = await apiClient.get<CatalogRun[]>('/api/v1/admin/catalog/runs?limit=20')
      setRuns(r.data)
      setTasks(prev => {
        const next = { ...prev }
        for (const chain of Object.keys(next)) {
          const latestRun = r.data.find(run => run.chain === chain)
          if (latestRun && latestRun.status !== 'running' && next[chain]?.state !== 'SUCCESS' && next[chain]?.state !== 'FAILURE') {
            if (latestRun.status === 'cancelled') {
              next[chain] = { ...next[chain], state: 'CANCELLED' }
            } else if (latestRun.status === 'completed' || latestRun.status === 'completed_with_errors') {
              next[chain] = { ...next[chain], state: 'SUCCESS' }
            }
          }
        }
        return next
      })
    } catch { /* silent */ }
  }
  async function recoverActive() {
    try {
      const r = await apiClient.get<{ active: Record<string, string> }>('/api/v1/admin/catalog/active')
      for (const [chain, taskId] of Object.entries(r.data.active)) {
        setTasks(p => ({ ...p, [chain]: { taskId, mode: 'full', state: 'STARTED', result: null, error: null } }))
        if (taskId) startPolling(chain, taskId)
      }
    } catch { /* silent */ }
  }
  function startPolling(chain: string, taskId: string) {
    if (pollRefs.current[chain]) clearInterval(pollRefs.current[chain])
    const poll = async () => {
      try {
        const r = await apiClient.get<{ state: string; result: CatalogResult | null }>(`/api/v1/admin/catalog/progress/${taskId}`)
        setTasks(p => ({ ...p, [chain]: { ...p[chain], taskId, state: r.data.state, result: r.data.result, error: null } }))
        if (r.data.state === 'SUCCESS' || r.data.state === 'FAILURE') {
          clearInterval(pollRefs.current[chain]); delete pollRefs.current[chain]
          setTimeout(() => { void loadRuns(); void loadProductCounts() }, 1500)
        }
      } catch { /* keep polling */ }
    }
    pollRefs.current[chain] = setInterval(poll, 5000); void poll()
  }
  async function trigger(chain: string, mode: 'full' | 'delta' | 'specials') {
    setTasks(p => ({ ...p, [chain]: { taskId: '', mode, state: 'PENDING', result: null, error: null } }))
    try {
      const r = await apiClient.post<{ task_id: string }>(`/api/v1/admin/catalog/${chain}?mode=${mode}`)
      setTasks(p => ({ ...p, [chain]: { taskId: r.data.task_id, mode, state: 'PENDING', result: null, error: null } }))
      startPolling(chain, r.data.task_id)
    } catch (e) {
      setTasks(p => ({ ...p, [chain]: { taskId: '', mode, state: 'FAILURE', result: null, error: apiError(e) } }))
    }
  }
  function dismiss(chain: string) {
    if (pollRefs.current[chain]) { clearInterval(pollRefs.current[chain]); delete pollRefs.current[chain] }
    setTasks(p => { const n = { ...p }; delete n[chain]; return n })
  }
  async function cancel(chain: string) {
    try {
      await apiClient.delete(`/api/v1/admin/catalog/${chain}`)
      setTasks(p => ({ ...p, [chain]: { ...p[chain], state: 'CANCELLED', result: null, error: 'Cancelled by user' } }))
      if (pollRefs.current[chain]) { clearInterval(pollRefs.current[chain]); delete pollRefs.current[chain] }
      setTimeout(() => { void loadRuns(); void loadProductCounts() }, 1500)
    } catch (e) {
      setTasks(p => ({ ...p, [chain]: { ...p[chain], error: apiError(e) } }))
    }
  }

  // ── ALDI flyer helpers ────────────────────────────────────────────────────
  async function loadFlyerRuns() {
    try { const r = await apiClient.get<FlyerRun[]>('/api/v1/admin/scrape/runs?limit=5&chain=aldi'); setFlyerRuns(r.data) } catch { /* silent */ }
  }
  async function recoverFlyerActive() {
    try {
      const r = await apiClient.get<{ active: Record<string, string> }>('/api/v1/admin/scrape/active')
      const taskId = r.data.active['aldi']
      if (taskId) { setFlyerTask({ taskId, progress: { pct: 0, stage: 'starting', message: 'Reconnecting…' }, error: null }); startFlyerPolling(taskId) }
    } catch { /* silent */ }
  }
  function startFlyerPolling(taskId: string) {
    if (flyerPollRef.current) clearInterval(flyerPollRef.current)
    const poll = async () => {
      try {
        const r = await apiClient.get<FlyerProgress>(`/api/v1/admin/scrape/progress/${taskId}`)
        setFlyerTask(t => ({ ...(t ?? { taskId, error: null }), progress: r.data }))
        if (r.data.stage === 'done' || r.data.stage === 'failed' || r.data.stage === 'cancelled') {
          if (flyerPollRef.current) clearInterval(flyerPollRef.current)
          setTimeout(() => void loadFlyerRuns(), 1500)
        }
      } catch { /* keep polling */ }
    }
    flyerPollRef.current = setInterval(poll, 1000); void poll()
  }
  async function triggerFlyer() {
    setFlyerTask({ taskId: '', progress: { pct: 0, stage: 'starting', message: 'Queued…' }, error: null })
    try {
      const r = await apiClient.post<{ task_id: string }>('/api/v1/admin/scrape/aldi')
      setFlyerTask({ taskId: r.data.task_id, progress: { pct: 0, stage: 'starting', message: 'Queued…' }, error: null })
      startFlyerPolling(r.data.task_id)
    } catch (e) {
      setFlyerTask({ taskId: '', progress: null, error: apiError(e) })
    }
  }
  async function cancelFlyer() {
    try {
      await apiClient.delete('/api/v1/admin/scrape/aldi')
      if (flyerPollRef.current) clearInterval(flyerPollRef.current)
      setFlyerTask(t => ({ ...(t ?? { taskId: '', error: null }), progress: { pct: 100, stage: 'cancelled', message: 'Cancelled by user' } }))
      setTimeout(() => void loadFlyerRuns(), 1500)
    } catch (e) {
      setFlyerTask(t => ({ ...(t ?? { taskId: '', progress: null }), error: apiError(e) }))
    }
  }

  const flyerRunning = !!flyerTask && flyerTask.progress?.stage !== 'done' && flyerTask.progress?.stage !== 'failed' && flyerTask.progress?.stage !== 'cancelled'
  const lastFlyer = flyerRuns[0]

  return (
    <div className="p-6 sm:p-7">
      <div className="flex items-start justify-between gap-5 mb-6">
        <div>
          <h2 className="text-lg font-bold text-gray-900 mb-1.5">Catalog Crawl</h2>
          <p className="text-sm text-gray-500 leading-relaxed max-w-xl">Authenticated catalog crawls. <strong>Specials</strong> hits the promotions endpoint (fast, weekly). Full walks every category. Delta re-checks changed categories only. ALDI uses a PDF flyer scraper.</p>
        </div>
      </div>

      {config?.available_chains.length === 0 && <div className="text-center text-gray-400 py-10 text-sm">No catalog scrapers registered yet.</div>}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        {config?.available_chains.map(chain => {
          const isAldi = chain === 'aldi'
          const task = tasks[chain]
          const catalogRunning = !!task && task.state !== 'SUCCESS' && task.state !== 'FAILURE' && task.state !== 'CANCELLED'
          const cardRunning = isAldi ? (catalogRunning || flyerRunning) : catalogRunning
          const enabled = config.enabled_chains.includes(chain)
          const cron = config.schedules[chain] || ''
          return (
            <ChainCard key={chain} chain={chain} isRunning={cardRunning} isDone={isAldi ? false : task?.state === 'SUCCESS'} isFailed={isAldi ? false : task?.state === 'FAILURE'}
              onDismiss={task && !catalogRunning && !isAldi ? () => dismiss(chain) : undefined}>
              <div className="flex flex-col gap-1 text-xs text-gray-500 mb-1.5">
                <Pill tone={enabled ? 'green' : 'gray'}>{enabled ? 'Scheduled' : 'On-demand'}</Pill>
                <span>{cron ? `Cron: ${cron}` : 'Delta: on-demand'}</span>
              </div>
              <div className="flex items-baseline gap-1 mb-2">
                <span className="text-xl font-bold text-gray-800 tabular-nums">
                  {productCounts[chain] != null ? productCounts[chain].toLocaleString() : '—'}
                </span>
                <span className="text-xs text-gray-400">products</span>
              </div>
              {/* catalog task progress */}
              {task && (
                <ProgressPanel>
                  {task.error && task.state === 'CANCELLED' ? <div className="text-xs text-gray-500">↩ Cancelled</div>
                  : task.error ? <div className="text-xs text-red-500">{task.error}</div>
                  : task.state === 'SUCCESS' && task.result ? <div className="text-[11px] text-emerald-600 font-medium">✓ {task.mode}: {task.result.products_found} found, {task.result.products_new} new</div>
                  : task.state === 'FAILURE' ? <div className="text-xs text-red-500">Crawl failed</div>
                  : (() => {
                      const liveRun = runs.find(r => r.chain === chain && r.status === 'running')
                      const isSpecials = task.mode === 'specials'
                      const cats = (!isSpecials && liveRun) ? `${liveRun.categories_crawled}${liveRun.categories_found ? `/${liveRun.categories_found}` : ''} cats` : null
                      const prods = liveRun?.products_found ? `${liveRun.products_found.toLocaleString()} products` : null
                      const detail = [cats, prods].filter(Boolean).join(' · ')
                      return <div className="text-[11px] text-gray-500">⏳ {task.mode} crawl {task.state === 'PENDING' ? 'queued…' : `running… ${detail}`}</div>
                    })()}</ProgressPanel>
              )}
              {/* ALDI flyer progress */}
              {isAldi && flyerTask && (
                <ProgressPanel>
                  {flyerTask.error ? <div className="text-xs text-red-500">{flyerTask.error}</div>
                  : flyerTask.progress ? (() => {
                      const { stage, pct, message } = flyerTask.progress
                      const done = stage === 'done'; const failed = stage === 'failed'; const cancelled = stage === 'cancelled'
                      const color = failed ? 'text-red-500' : cancelled ? 'text-gray-400' : done ? 'text-emerald-600' : 'text-emerald-600'
                      const bar = failed ? 'bg-red-500' : cancelled ? 'bg-gray-300' : 'bg-emerald-500'
                      return <>
                        <div className="flex justify-between text-xs font-semibold mb-1.5">
                          <span className={color}>Flyer: {done ? 'Done' : failed ? 'Failed' : cancelled ? 'Cancelled' : stage}</span>
                          <span className="text-gray-400">{pct}%</span>
                        </div>
                        <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden mb-1.5">
                          <div className={`h-full rounded-full transition-all duration-500 ${bar}`} style={{ width: `${pct}%` }} />
                        </div>
                        <div className="text-[11px] text-gray-500">{message}</div>
                      </>
                    })() : null}
                </ProgressPanel>
              )}
              <div className="flex gap-2 mt-2">
                {/* ALDI: Flyer button replaces Specials; other chains: Specials button */}
                {isAldi
                  ? <AdmButton variant="primary" size="sm" className="flex-1" disabled={cardRunning} onClick={() => !cardRunning && void triggerFlyer()}>
                      {flyerRunning ? '⏳ Flyer…' : lastFlyer ? `★ Flyer (${relativeTime(lastFlyer.run_started_at)})` : '★ Flyer'}
                    </AdmButton>
                  : <AdmButton variant="primary" size="sm" className="flex-1" disabled={catalogRunning} onClick={() => !catalogRunning && void trigger(chain, 'specials')}>
                      {catalogRunning && task?.mode === 'specials' ? '⏳ Specials…' : '★ Specials'}
                    </AdmButton>
                }
                <AdmButton variant="secondary" size="sm" className="flex-1" disabled={cardRunning} onClick={() => !cardRunning && void trigger(chain, 'full')}>
                  {catalogRunning && task?.mode === 'full' ? '⏳ Full…' : '▶ Full'}
                </AdmButton>
                <AdmButton variant="secondary" size="sm" className="flex-1" disabled={cardRunning} onClick={() => !cardRunning && void trigger(chain, 'delta')}>
                  {catalogRunning && task?.mode === 'delta' ? '⏳ Delta…' : '↻ Delta'}
                </AdmButton>
                {catalogRunning && (
                  <AdmButton variant="danger" size="sm" onClick={() => void cancel(chain)} title="Stop this crawl">✕</AdmButton>
                )}
                {isAldi && flyerRunning && (
                  <AdmButton variant="danger" size="sm" onClick={() => void cancelFlyer()} title="Stop flyer scrape">✕</AdmButton>
                )}
              </div>
            </ChainCard>
          )
        })}
      </div>

      <div className="flex items-center justify-between border-t border-gray-100 pt-4 mb-3">
        <span className="text-[11px] font-bold uppercase tracking-wider text-gray-400">Catalog Run History</span>
        <AdmButton variant="ghost" size="sm" onClick={() => { void loadRuns(); void loadProductCounts() }}>↻ Refresh</AdmButton>
      </div>
      <CatalogRunHistory runs={runs} />
    </div>
  )
}

// ─── Tab: Coverage ───────────────────────────────────────────────────────────

interface CoverageRow {
  chain: string
  total_products: number
  brand_pct: number
  image_pct: number
  package_size_pct: number
  unit_price_pct: number
  unit_label_pct: number
  category_pct: number
  gtin_pct: number
  last_crawled: string | null
  categories_crawled: number
  categories_total: number
}

function CoveragePct({ pct }: { pct: number }) {
  const color = pct >= 90 ? 'text-emerald-600' : pct >= 50 ? 'text-amber-600' : 'text-red-500'
  const bar = pct >= 90 ? 'bg-emerald-500' : pct >= 50 ? 'bg-amber-400' : 'bg-red-400'
  return (
    <div className="flex items-center gap-1.5 min-w-[68px]">
      <div className="w-12 h-1.5 rounded-full bg-gray-100 overflow-hidden flex-shrink-0">
        <div className={`h-full rounded-full ${bar}`} style={{ width: `${pct}%` }} />
      </div>
      <span className={`text-xs tabular-nums font-medium ${color}`}>{pct.toFixed(0)}%</span>
    </div>
  )
}

function CoverageTab() {
  const [rows, setRows] = useState<CoverageRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null)

  useEffect(() => {
    void load()
    const iv = setInterval(() => void load(), 30000)
    return () => clearInterval(iv)
  }, [])

  async function load() {
    setLoading(true); setError(null)
    try {
      const r = await apiClient.get<CoverageRow[]>('/api/v1/admin/catalog/coverage')
      setRows(r.data)
      setLastRefreshed(new Date())
    } catch { setError('Failed to load coverage stats') }
    finally { setLoading(false) }
  }

  const FIELDS: { key: keyof CoverageRow; label: string }[] = [
    { key: 'brand_pct',        label: 'Brand' },
    { key: 'image_pct',        label: 'Image' },
    { key: 'package_size_pct', label: 'Pkg Size' },
    { key: 'unit_price_pct',   label: 'Unit Price' },
    { key: 'unit_label_pct',   label: 'Unit Label' },
    { key: 'category_pct',     label: 'Category' },
    { key: 'gtin_pct',         label: 'GTIN' },
  ]

  return (
    <div className="p-6 sm:p-7">
      <div className="flex items-start justify-between gap-5 mb-6">
        <div>
          <h2 className="text-lg font-bold text-gray-900 mb-1.5">Field Coverage</h2>
          <p className="text-sm text-gray-500 leading-relaxed max-w-xl">
            Per-chain breakdown of how many products have each field populated. Auto-refreshes every 30s.
          </p>
        </div>
        <div className="flex items-center gap-3 flex-shrink-0">
          {lastRefreshed && (
            <span className="text-xs text-gray-400">Updated {relativeTime(lastRefreshed.toISOString())}</span>
          )}
          <AdmButton variant="ghost" size="sm" onClick={() => void load()}>↻ Refresh</AdmButton>
        </div>
      </div>

      {loading && rows.length === 0 && <div className="text-center text-gray-400 py-10 text-sm">Loading…</div>}
      {error && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm mb-4">{error}</div>}

      {rows.length > 0 && (
        <div className="overflow-auto border border-gray-200 rounded-xl">
          <table className="w-full text-sm">
            <thead className="sticky top-0 z-10 bg-white">
              <tr className="border-b border-gray-200">
                <th className="text-left px-3 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-gray-500 whitespace-nowrap">Chain</th>
                <th className="text-right px-3 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-gray-500 whitespace-nowrap">Products</th>
                <th className="text-right px-3 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-gray-500 whitespace-nowrap">Categories</th>
                {FIELDS.map(f => (
                  <th key={f.key} className="px-3 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-gray-500 whitespace-nowrap">{f.label}</th>
                ))}
                <th className="text-left px-3 py-2.5 text-[11px] font-semibold uppercase tracking-wide text-gray-500 whitespace-nowrap">Last Crawled</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rows.map(row => (
                <tr key={row.chain} className="hover:bg-gray-50">
                  <td className="px-3 py-2.5">
                    <ChainBadge chain={row.chain} />
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums font-medium text-gray-800">
                    {row.total_products.toLocaleString()}
                  </td>
                  <td className="px-3 py-2.5 text-right tabular-nums text-gray-600">
                    {row.categories_total > 0
                      ? <span>{row.categories_crawled}<span className="text-gray-400">/{row.categories_total}</span></span>
                      : <span className="text-gray-400">—</span>}
                  </td>
                  {FIELDS.map(f => (
                    <td key={f.key} className="px-3 py-2.5">
                      <CoveragePct pct={row[f.key] as number} />
                    </td>
                  ))}
                  <td className="px-3 py-2.5 text-xs text-gray-500 whitespace-nowrap">
                    {row.last_crawled ? relativeTime(row.last_crawled) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {rows.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-4 text-xs text-gray-400">
          <span><span className="inline-block w-3 h-1.5 rounded bg-emerald-500 mr-1" />≥ 90% — good</span>
          <span><span className="inline-block w-3 h-1.5 rounded bg-amber-400 mr-1" />50–89% — partial</span>
          <span><span className="inline-block w-3 h-1.5 rounded bg-red-400 mr-1" />&lt; 50% — needs work</span>
        </div>
      )}
    </div>
  )
}

// ─── Tab: Configs ─────────────────────────────────────────────────────────────

function ConfigRow({ entry, onSaved }: { entry: ConfigEntry; onSaved: () => void }) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState('')
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [savedOk, setSavedOk] = useState(false)

  const type = entry.type ?? 'string'
  // The effective current value: explicit DB value, else the surfaced default.
  const current = entry.value ?? entry.default ?? ''

  // PUT a value directly (used by toggle/select which commit on interaction).
  async function saveValue(v: string) {
    setSaving(true); setError(null)
    try {
      await apiClient.put(`/api/v1/admin/config/${entry.key}`, { value: v })
      setSavedOk(true); setEditing(false); setValue('')
      setTimeout(() => setSavedOk(false), 3000)
      onSaved()
    } catch (e) { setError(apiError(e)) }
    finally { setSaving(false) }
  }

  async function save() {
    if (!value.trim()) return
    await saveValue(value.trim())
  }

  async function remove() {
    if (!confirm(`Remove "${entry.label}"? The service will fall back to the environment variable / default.`)) return
    setDeleting(true); setError(null)
    try { await apiClient.delete(`/api/v1/admin/config/${entry.key}`); onSaved() }
    catch (e) { setError(apiError(e)) }
    finally { setDeleting(false) }
  }

  // ── bool → inline toggle (commits immediately) ──────────────────────────────
  if (type === 'bool') {
    const on = String(current).toLowerCase() === 'true'
    return (
      <div className="p-4 border rounded-xl mb-2 border-gray-200">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center flex-wrap gap-2 mb-1.5">
              <span className="font-semibold text-sm text-gray-900">{entry.label}</span>
              <code className="font-mono text-[11px] bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded">{entry.key}</code>
              {entry.is_set
                ? <Pill tone="green">Set{entry.updated_at ? ` · ${relativeTime(entry.updated_at)}` : ''}</Pill>
                : <Pill tone="gray">Default</Pill>}
              {savedOk && <Pill tone="green">Saved!</Pill>}
            </div>
            <p className="text-sm text-gray-500 leading-relaxed">{entry.description}</p>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            {entry.is_set && (
              <AdmButton variant="ghost" size="sm" onClick={() => void remove()} disabled={deleting}>
                {deleting ? '…' : 'Reset'}
              </AdmButton>
            )}
            <button
              type="button"
              role="switch"
              aria-checked={on}
              disabled={saving}
              onClick={() => void saveValue(on ? 'false' : 'true')}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors disabled:opacity-50 ${on ? 'bg-emerald-600' : 'bg-gray-300'}`}
            >
              <span className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${on ? 'translate-x-5' : 'translate-x-0.5'}`} />
            </button>
          </div>
        </div>
        {error && <div className="mt-1.5 text-xs text-red-500">{error}</div>}
      </div>
    )
  }

  // ── enum → inline select (commits on change) ────────────────────────────────
  if (type === 'enum') {
    return (
      <div className="p-4 border rounded-xl mb-2 border-gray-200">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center flex-wrap gap-2 mb-1.5">
              <span className="font-semibold text-sm text-gray-900">{entry.label}</span>
              <code className="font-mono text-[11px] bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded">{entry.key}</code>
              {entry.is_set
                ? <Pill tone="green">Set{entry.updated_at ? ` · ${relativeTime(entry.updated_at)}` : ''}</Pill>
                : <Pill tone="gray">Default</Pill>}
              {savedOk && <Pill tone="green">Saved!</Pill>}
            </div>
            <p className="text-sm text-gray-500 leading-relaxed">{entry.description}</p>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            {entry.is_set && (
              <AdmButton variant="ghost" size="sm" onClick={() => void remove()} disabled={deleting}>
                {deleting ? '…' : 'Reset'}
              </AdmButton>
            )}
            <select
              className="input-base text-sm"
              value={current}
              disabled={saving}
              onChange={e => void saveValue(e.target.value)}
            >
              {(entry.options ?? []).map(o => <option key={o} value={o}>{o}</option>)}
            </select>
          </div>
        </div>
        {error && <div className="mt-1.5 text-xs text-red-500">{error}</div>}
      </div>
    )
  }

  // ── numeric (int/float) + string/secret → Set/Update flow ───────────────────
  const isNumeric = type === 'int' || type === 'float'
  const inputType = entry.is_secret ? 'password' : isNumeric ? 'number' : 'text'
  const step = type === 'float' ? 'any' : isNumeric ? '1' : undefined

  return (
    <div className={`p-4 border rounded-xl mb-2 transition-colors ${editing ? 'border-emerald-300 bg-emerald-50/40' : 'border-gray-200'}`}>
      <div className="flex items-start justify-between gap-3 mb-1.5">
        <div className="flex items-center flex-wrap gap-2">
          <span className="font-semibold text-sm text-gray-900">{entry.label}</span>
          <code className="font-mono text-[11px] bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded">{entry.key}</code>
          {entry.is_set
            ? <Pill tone="green">Set{entry.updated_at ? ` · ${relativeTime(entry.updated_at)}` : ''}</Pill>
            : <Pill tone="gray">{entry.default != null ? 'Default' : 'Not set'}</Pill>}
          {savedOk && <Pill tone="green">Saved!</Pill>}
        </div>
        <div className="flex gap-2 flex-shrink-0">
          {entry.is_set && !editing && (
            <AdmButton variant="ghost" size="sm" onClick={() => void remove()} disabled={deleting}>
              {deleting ? '…' : 'Remove'}
            </AdmButton>
          )}
          <AdmButton variant="primary" size="sm" onClick={() => { setEditing(e => !e); setValue(''); setError(null) }}>
            {editing ? 'Cancel' : entry.is_set ? 'Update' : 'Set'}
          </AdmButton>
        </div>
      </div>
      <p className="text-sm text-gray-500 leading-relaxed">{entry.description}</p>
      {!editing && !entry.is_secret && entry.value && (
        <div className="mt-2 font-mono text-xs text-gray-700 bg-gray-50 px-2.5 py-1.5 rounded-md break-all">{entry.value}</div>
      )}
      {!editing && !entry.is_set && entry.default != null && entry.default !== '' && (
        <div className="mt-2 text-xs text-gray-400">default: <span className="font-mono">{entry.default}</span></div>
      )}
      {editing && (
        <div className="flex gap-2 mt-3">
          <input
            className="input-base flex-1 text-sm"
            type={inputType}
            step={step}
            min={entry.min_value ?? undefined}
            max={entry.max_value ?? undefined}
            placeholder={entry.is_secret ? 'Paste value (stored encrypted, never shown)' : isNumeric ? `Enter a number${entry.min_value != null || entry.max_value != null ? ` (${entry.min_value ?? '−∞'}–${entry.max_value ?? '∞'})` : ''}` : 'Enter value'}
            value={value}
            onChange={e => setValue(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && void save()}
            autoFocus
          />
          <AdmButton variant="primary" onClick={() => void save()} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </AdmButton>
        </div>
      )}
      {error && <div className="mt-1.5 text-xs text-red-500">{error}</div>}
    </div>
  )
}

function ConfigsTab() {
  const [configs, setConfigs] = useState<ConfigEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeGroup, setActiveGroup] = useState<string | null>(null)

  useEffect(() => { void load() }, [])

  async function load() {
    setLoading(true); setError(null)
    try {
      const r = await apiClient.get<ConfigEntry[]>('/api/v1/admin/config')
      setConfigs(r.data)
      if (r.data.length > 0 && activeGroup === null) {
        const firstGroup = r.data[0].group
        setActiveGroup(firstGroup)
      }
    }
    catch { setError('Could not load config keys — is the backend running and migrated?') }
    finally { setLoading(false) }
  }

  const groups = configs.reduce<Record<string, ConfigEntry[]>>((acc, e) => { (acc[e.group] ??= []).push(e); return acc }, {})
  const groupNames = Object.keys(groups)
  const setCount = configs.filter(c => c.is_set).length

  return (
    <div className="p-6 sm:p-7">
      <div className="flex items-start justify-between gap-5 mb-6">
        <div>
          <h2 className="text-lg font-bold text-gray-900 mb-1.5">Configs</h2>
          <p className="text-sm text-gray-500 leading-relaxed max-w-xl">
            Encrypted API keys and service settings stored in the database.
            Secrets are Fernet-encrypted at rest and <strong>never returned in plain text</strong>.
            Values set here override environment variables at runtime.
          </p>
        </div>
        <div className="flex items-center gap-3 flex-shrink-0">
          {configs.length > 0 && (
            <Pill tone="gray">{setCount} / {configs.length} configured</Pill>
          )}
          <AdmButton variant="ghost" size="sm" onClick={() => void load()}>↻ Refresh</AdmButton>
        </div>
      </div>

      {loading && <div className="text-center text-gray-400 py-10 text-sm">Loading…</div>}
      {error && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm mb-4">{error}</div>}
      {!loading && !error && groupNames.length > 0 && (
        <>
          <div className="flex flex-wrap gap-2 mb-5">
            {groupNames.map(group => {
              const groupSetCount = groups[group].filter(e => e.is_set).length
              const isActive = activeGroup === group
              return (
                <button
                  key={group}
                  onClick={() => setActiveGroup(group)}
                  className={`inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-full text-sm font-medium border transition-colors
                    ${isActive
                      ? 'bg-emerald-600 text-white border-emerald-600'
                      : 'bg-white text-gray-600 border-gray-200 hover:border-emerald-300 hover:text-emerald-700'}`}
                >
                  {group}
                  {groupSetCount > 0 && (
                    <span className={`text-xs font-semibold px-1.5 py-0.5 rounded-full ${isActive ? 'bg-emerald-500 text-white' : 'bg-gray-100 text-gray-500'}`}>
                      {groupSetCount}
                    </span>
                  )}
                </button>
              )
            })}
          </div>
          {activeGroup && groups[activeGroup] && (
            <div>
              {groups[activeGroup].map(entry => (
                <ConfigRow key={entry.key} entry={entry} onSaved={() => void load()} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ─── Tab: Dedup Health ────────────────────────────────────────────────────────

function DedupTab() {
  const [data, setData] = useState<DedupStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => { void load() }, [])

  async function load() {
    setLoading(true)
    try {
      const r = await apiClient.get<DedupStats>('/api/v1/admin/dedup/stats')
      setData(r.data)
      setError(null)
    } catch { setError('Could not load dedup stats') }
    finally { setLoading(false) }
  }

  if (loading) return <div className="text-center text-gray-400 py-10 text-sm">Loading…</div>
  if (error) return <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">{error}</div>
  if (!data) return null

  const matchPct = data.total_products > 0
    ? ((data.products_in_2plus_chains / data.total_products) * 100).toFixed(1)
    : '0.0'

  return (
    <div>
      <div className="flex items-start justify-between gap-5 mb-5">
        <div>
          <h2 className="text-lg font-bold text-gray-900 mb-1">Dedup Health</h2>
          <p className="text-sm text-gray-500 leading-relaxed max-w-2xl">
            Products matched across chains via GTIN + fuzzy name. A product appearing in 2+ chains means one shared row — one watchlist entry covers all stores.
          </p>
        </div>
        <AdmButton variant="ghost" size="sm" onClick={load}>↻ Refresh</AdmButton>
      </div>

      {/* Summary stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <StatCard value={data.total_products.toLocaleString()} label="Products with prices" color="#4f46e5" />
        <StatCard value={data.products_in_1_chain.toLocaleString()} label="Single-chain only" color="#6b7280" />
        <StatCard value={data.products_in_2plus_chains.toLocaleString()} label="Matched 2+ chains" color="#10b981" />
        <StatCard value={`${matchPct}%`} label="Cross-chain match rate" color={parseFloat(matchPct) > 5 ? '#10b981' : '#f59e0b'} />
      </div>

      {/* Overlap by combination */}
      {data.overlap_by_combo.length > 0 ? (
        <div className="mb-7">
          <h3 className="text-[11px] font-bold uppercase tracking-wider text-gray-500 mb-2.5">Overlap by chain combination</h3>
          <div className="flex flex-wrap gap-2.5">
            {data.overlap_by_combo.map((row, i) => (
              <div key={i} className="bg-emerald-50 border border-emerald-200 rounded-lg px-4 py-2.5 flex flex-col items-center min-w-[120px]">
                <div className="flex gap-1 mb-1">
                  {row.chains.map(c => <span key={c}>{CHAIN_LOGO[c] ?? '⚫'}</span>)}
                </div>
                <div className="text-sm font-bold text-emerald-800">{row.product_count.toLocaleString()}</div>
                <div className="text-[11px] text-gray-500 mt-0.5">{row.chains.map(titleCase).join(' + ')}</div>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="bg-amber-50 border border-amber-200 rounded-lg px-4 py-3.5 mb-6 text-sm text-amber-800">
          <strong>No cross-chain matches yet.</strong> Run catalog crawls for 2+ chains, then re-run to check. Dedup fires on the next ingestion of each chain.
        </div>
      )}

      {/* Sample matched products */}
      {data.sample_multi_chain.length > 0 && (
        <div>
          <h3 className="text-[11px] font-bold uppercase tracking-wider text-gray-500 mb-2.5">Sample matched products</h3>
          <RunTable
            columns={['Product', 'Chains', 'Prices']}
            maxH="max-h-64"
            rows={data.sample_multi_chain.map(p => [
              <span key="n" className="font-medium text-gray-900">{p.name}</span>,
              <span key="c" className="flex gap-1.5 flex-wrap">
                {p.chains.map(c => <ChainBadge key={c} chain={c} size="sm" />)}
              </span>,
              <span key="p" className="flex gap-3 flex-wrap">
                {p.chains.map(c => (
                  <span key={c} className="text-xs text-gray-700">
                    <span className="text-gray-400">{titleCase(c)}:</span> <span className="text-emerald-600 font-semibold">${p.prices[c]?.toFixed(2) ?? '—'}</span>
                  </span>
                ))}
              </span>,
            ])}
          />
        </div>
      )}
    </div>
  )
}

// ─── Tab: Dedup Review ────────────────────────────────────────────────────────

interface MatchProductDetail {
  id: string; name: string; brand: string | null; category: string | null
  image_url: string | null; chain: string | null; price_aud: number | null
  store_url?: string | null; package_size?: string | null; unit_price?: number | null; unit_price_label?: string | null
}
interface MatchCandidate {
  id: string; score: number; status: string; proposed_by: string
  product_a: MatchProductDetail; product_b: MatchProductDetail
  created_at: string
  ai_verdict?: string | null; ai_confidence?: number | null
  ai_keep?: string | null; ai_reason?: string | null
}
interface CandidatePage { items: MatchCandidate[]; total: number }
interface SearchResult {
  id: string; name: string; brand: string | null; category: string | null
  image_url: string | null; chain: string | null; price_aud: number | null
}

type Side = 'left' | 'right'
type Decision = 'approved' | 'rejected' | 'skipped'

// Imperative handle exposed by CandidateCard so keyboard shortcuts can drive the top card.
interface CandidateHandle { decide: (decision: Decision, keep?: Side) => void }

// A single product panel inside a candidate card. When `onKeep` is provided it
// renders a "Keep this one" action — approving merges the OTHER product into it.
function MatchProductCard({ p, onKeep, keepLabel, busy, aiSuggested }: {
  p: MatchProductDetail; onKeep?: () => void; keepLabel?: string; busy?: boolean; aiSuggested?: boolean
}) {
  return (
    <div className="flex flex-col h-full">
      <div className="flex gap-3 flex-1">
        {p.image_url
          ? <img className="w-16 h-16 object-contain rounded-lg border border-gray-100 flex-shrink-0 bg-white" src={p.image_url} alt={p.name} />
          : <div className="w-16 h-16 rounded-lg border border-gray-100 bg-gray-50 flex items-center justify-center flex-shrink-0">
              <PlaceholderIcon className="w-7 h-7 text-gray-300" />
            </div>}
        <div className="flex-1 min-w-0">
          <div className="text-sm font-semibold text-gray-900 leading-snug mb-1 line-clamp-2">{p.name}</div>
          {(p.brand || p.category) && (
            <div className="text-xs text-gray-500 mb-1.5">
              {p.brand}{p.brand && p.category ? ' · ' : ''}{p.category}
            </div>
          )}
          <div className="flex items-center gap-2 flex-wrap">
            <ChainBadge chain={p.chain} size="sm" />
            {p.price_aud != null && <span className="text-base font-bold text-emerald-600">${p.price_aud.toFixed(2)}</span>}
            {p.unit_price != null && <span className="text-xs text-gray-400">${p.unit_price.toFixed(2)} / {p.unit_price_label ?? 'unit'}</span>}
            {p.store_url && (
              <a
                href={p.store_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-emerald-600 hover:text-emerald-800 hover:underline"
              >
                View ↗
              </a>
            )}
          </div>
          {p.package_size && (
            <div className="text-xs text-gray-400 mt-1">{p.package_size}</div>
          )}
        </div>
      </div>
      {onKeep && (
        <AdmButton
          variant="primary"
          size="sm"
          className={`w-full mt-3 ${aiSuggested ? 'ring-2 ring-emerald-400 ring-offset-1' : ''}`}
          disabled={busy}
          onClick={onKeep}
        >
          ✓ Keep this one{keepLabel ? ` (${keepLabel})` : ''}{aiSuggested ? ' · AI' : ''}
        </AdmButton>
      )}
    </div>
  )
}

const CandidateCard = forwardRef<CandidateHandle, {
  candidate: MatchCandidate
  active?: boolean
  onDecided: (id: string) => void
}>(function CandidateCard({ candidate, active, onDecided }, ref) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [fading, setFading] = useState(false)

  const decide = useCallback(async (decision: Decision, keep?: Side) => {
    setBusy(true); setErr(null)
    const keepProduct = keep === 'right' ? candidate.product_b : candidate.product_a
    const dropProduct = keep === 'right' ? candidate.product_a : candidate.product_b
    try {
      await apiClient.post(`/api/v1/admin/dedup/candidates/${candidate.id}/decide`, {
        decision,
        keep_id: decision === 'approved' ? keepProduct.id : undefined,
        drop_id: decision === 'approved' ? dropProduct.id : undefined,
      })
      setFading(true)
      setTimeout(() => onDecided(candidate.id), 300)
    } catch (e) {
      setErr(apiError(e))
      setBusy(false)
    }
  }, [candidate, onDecided])

  // Expose the decide fn to the parent so keyboard shortcuts can drive the top card.
  useImperativeHandle(ref, () => ({ decide }), [decide])

  const scoreTone: PillTone = candidate.score >= 97 ? 'green' : 'amber'

  // AI judge verdict → chip tone. Populated only by the Claude dedup pass.
  const AI_TONE: Record<string, PillTone> = {
    same: 'green', different: 'gray', vetoed: 'red', skipped: 'amber',
  }
  const aiTone: PillTone = candidate.ai_verdict ? (AI_TONE[candidate.ai_verdict] ?? 'gray') : 'gray'
  const aiLabel = candidate.ai_verdict
    ? `AI: ${candidate.ai_verdict}${candidate.ai_confidence != null ? ` ${Math.round(candidate.ai_confidence * 100)}%` : ''}`
    : null

  return (
    <div
      className={`bg-white border rounded-xl shadow-sm transition-opacity duration-300 overflow-hidden
        ${fading ? 'opacity-0' : 'opacity-100'} ${active ? 'ring-2 ring-emerald-400 border-emerald-300' : 'border-gray-200'}`}
    >
      {/* Header row: score + provenance */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-gray-100 bg-gray-50/60">
        <div className="flex items-center gap-2">
          <Pill tone={scoreTone}>{candidate.score.toFixed(1)}% match</Pill>
          {aiLabel && (
            <Pill tone={aiTone} className="capitalize" >
              <span title={candidate.ai_reason ?? undefined}>{aiLabel}</span>
            </Pill>
          )}
          {active && <span className="text-[11px] text-emerald-500 font-medium">← focused</span>}
        </div>
        <div className="flex items-center gap-2 text-xs text-gray-400">
          <span className="text-gray-500">{candidate.proposed_by}</span>
          <span>·</span>
          <span>{relativeTime(candidate.created_at)}</span>
        </div>
      </div>

      {/* Two products with a VS node between them */}
      <div className="p-4 grid grid-cols-1 sm:grid-cols-[1fr_auto_1fr] gap-4 items-stretch">
        <MatchProductCard p={candidate.product_a} keepLabel="A" busy={busy} aiSuggested={candidate.ai_verdict === 'same' && candidate.ai_keep === 'a'} onKeep={() => decide('approved', 'left')} />
        <div className="hidden sm:flex items-center justify-center">
          <span className="w-9 h-9 rounded-full bg-gray-100 text-gray-400 text-xs font-bold flex items-center justify-center">VS</span>
        </div>
        <MatchProductCard p={candidate.product_b} keepLabel="B" busy={busy} aiSuggested={candidate.ai_verdict === 'same' && candidate.ai_keep === 'b'} onKeep={() => decide('approved', 'right')} />
      </div>

      {/* Footer: secondary actions */}
      <div className="flex items-center justify-end gap-2 px-4 py-2.5 border-t border-gray-100 bg-gray-50/60">
        {err && <div className="text-xs text-red-500 mr-auto">{err}</div>}
        <AdmButton variant="danger" size="sm" disabled={busy} onClick={() => decide('rejected')}>Reject</AdmButton>
        <AdmButton variant="secondary" size="sm" disabled={busy} onClick={() => decide('skipped')}>Skip</AdmButton>
      </div>
    </div>
  )
})

function DedupReviewTab({ dedupRunning }: { dedupRunning: boolean }) {
  const [statusFilter, setStatusFilter] = useState<'pending' | 'approved' | 'rejected' | 'skipped'>('pending')
  const [minScore, setMinScore] = useState(85)
  const [maxScore, setMaxScore] = useState(100)
  const [chainFilter, setChainFilter] = useState('')
  const [items, setItems] = useState<MatchCandidate[]>([])
  const [total, setTotal] = useState(0)
  const [, setOffset] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showHelp, setShowHelp] = useState(false)

  // Manual link panel
  const [showLink, setShowLink] = useState(false)
  const [searchQ, setSearchQ] = useState('')
  const [searchResults, setSearchResults] = useState<SearchResult[]>([])
  const [linkA, setLinkA] = useState<SearchResult | null>(null)
  const [linkB, setLinkB] = useState<SearchResult | null>(null)
  const [linkBusy, setLinkBusy] = useState(false)
  const [linkMsg, setLinkMsg] = useState<string | null>(null)
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Ref to the top (focused) card so keyboard shortcuts can drive it.
  const topCardRef = useRef<CandidateHandle | null>(null)
  // Stable ref for offset so loadCandidates doesn't need offset in its deps.
  const offsetRef = useRef(0)

  const LIMIT = 50

  const loadCandidates = useCallback(async (reset = false) => {
    setLoading(true); setError(null)
    const off = reset ? 0 : offsetRef.current
    try {
      const params: Record<string, string | number> = {
        status: statusFilter, min_score: minScore, max_score: maxScore, limit: LIMIT, offset: off,
      }
      if (chainFilter) params.chain = chainFilter
      const r = await apiClient.get<CandidatePage>('/api/v1/admin/dedup/candidates', { params })
      if (reset) {
        setItems(r.data.items)
        offsetRef.current = LIMIT
        setOffset(LIMIT)
      } else {
        setItems(prev => [...prev, ...r.data.items])
        offsetRef.current = off + LIMIT
        setOffset(off + LIMIT)
      }
      setTotal(r.data.total)
    } catch (e) { setError(apiError(e)) }
    finally { setLoading(false) }
  }, [statusFilter, minScore, maxScore, chainFilter])

  useEffect(() => { offsetRef.current = 0; void loadCandidates(true) }, [statusFilter, minScore, maxScore, chainFilter])

  // While an AI dedup run is active, refresh the count every 8s so merges show up.
  useEffect(() => {
    if (!dedupRunning || statusFilter !== 'pending') return
    const iv = setInterval(() => void loadCandidates(true), 8000)
    return () => clearInterval(iv)
  }, [dedupRunning, statusFilter, loadCandidates])

  function handleDecided(id: string) {
    setItems(prev => prev.filter(c => c.id !== id))
    setTotal(prev => Math.max(0, prev - 1))
  }

  // Keyboard shortcuts drive the top (first) card in the queue. Only active on
  // the Pending queue, and ignored while typing in an input/select/textarea.
  useEffect(() => {
    if (statusFilter !== 'pending') return
    const handler = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null
      const tag = el?.tagName
      if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA' || el?.isContentEditable) return
      if (e.metaKey || e.ctrlKey || e.altKey) return

      const top = topCardRef.current
      const key = e.key.toLowerCase()
      if (key === '?') { e.preventDefault(); setShowHelp(v => !v); return }
      if (!top || items.length === 0) return
      if (key === 'a' || e.key === 'ArrowLeft') { e.preventDefault(); top.decide('approved', 'left') }
      else if (key === 'd' || e.key === 'ArrowRight') { e.preventDefault(); top.decide('approved', 'right') }
      else if (key === 'r') { e.preventDefault(); top.decide('rejected') }
      else if (key === 's') { e.preventDefault(); top.decide('skipped') }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [statusFilter, items.length])

  function handleSearchChange(q: string) {
    setSearchQ(q)
    if (searchTimer.current) clearTimeout(searchTimer.current)
    if (q.trim().length < 2) { setSearchResults([]); return }
    searchTimer.current = setTimeout(async () => {
      try {
        const r = await apiClient.get<SearchResult[]>('/api/v1/admin/dedup/search', { params: { q, limit: 10 } })
        setSearchResults(r.data)
      } catch { /* ignore */ }
    }, 300)
  }

  async function handlePropose() {
    if (!linkA || !linkB) return
    setLinkBusy(true); setLinkMsg(null)
    try {
      await apiClient.post('/api/v1/admin/dedup/propose', { product_a_id: linkA.id, product_b_id: linkB.id })
      setLinkMsg('Proposed successfully — check the Pending queue.')
      setLinkA(null); setLinkB(null); setSearchQ(''); setSearchResults([])
      if (statusFilter === 'pending') void loadCandidates(true)
    } catch (e) { setLinkMsg(`Error: ${apiError(e)}`) }
    finally { setLinkBusy(false) }
  }

  const hasMore = items.length < total

  return (
    <div className="flex flex-col gap-5">
      {dedupRunning && statusFilter === 'pending' && (
        <div className="flex items-center gap-2 bg-amber-50 border border-amber-200 rounded-lg px-4 py-2.5 text-sm text-amber-800">
          <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse flex-shrink-0" />
          AI Dedup run is active — list refreshes every 8s as merges are committed.
        </div>
      )}
      {/* Sticky toolbar */}
      <div className="sticky top-0 z-10 -mx-6 sm:-mx-7 px-6 sm:px-7 py-3 bg-white/95 backdrop-blur border-b border-gray-100">
        <div className="flex items-center flex-wrap gap-3">
          {/* Status segmented control */}
          <div className="inline-flex rounded-lg border border-gray-200 bg-gray-50 p-0.5">
            {(['pending', 'approved', 'rejected', 'skipped'] as const).map(s => (
              <button
                key={s}
                className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-colors
                  ${statusFilter === s ? 'bg-white text-emerald-700 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}
                onClick={() => { setStatusFilter(s); setOffset(0) }}
              >
                {titleCase(s)}
              </button>
            ))}
          </div>

          {/* Score range */}
          <label className="flex items-center gap-1.5 text-xs text-gray-500">
            Score
            <input className="w-14 px-2 py-1 border border-gray-300 rounded-md text-xs text-center focus:outline-none focus:border-emerald-500"
              type="number" min={0} max={100} value={minScore} onChange={e => setMinScore(Number(e.target.value))} />
            –
            <input className="w-14 px-2 py-1 border border-gray-300 rounded-md text-xs text-center focus:outline-none focus:border-emerald-500"
              type="number" min={0} max={100} value={maxScore} onChange={e => setMaxScore(Number(e.target.value))} />
          </label>

          {/* Chain filter */}
          <select className="px-2.5 py-1.5 border border-gray-300 rounded-md text-xs bg-white focus:outline-none focus:border-emerald-500"
            value={chainFilter} onChange={e => setChainFilter(e.target.value)}>
            <option value="">All chains</option>
            {ALL_CHAINS.map(c => <option key={c} value={c}>{titleCase(c)}</option>)}
          </select>

          {/* Live count + help toggle */}
          <div className="ml-auto flex items-center gap-3">
            <span className="text-xs text-gray-500"><span className="font-bold text-gray-900">{total.toLocaleString()}</span> {statusFilter}</span>
            <button className="text-xs text-emerald-600 hover:text-emerald-700 font-medium" onClick={() => setShowHelp(v => !v)}>
              {showHelp ? 'Hide shortcuts' : '⌨ Shortcuts'}
            </button>
          </div>
        </div>

        {/* Keyboard hint bar (pending only) */}
        {statusFilter === 'pending' && (
          <div className="mt-2 text-[11px] text-gray-400 flex flex-wrap gap-x-3 gap-y-1">
            <span><kbd className="font-mono bg-gray-100 text-gray-600 px-1 rounded">A</kbd>/<kbd className="font-mono bg-gray-100 text-gray-600 px-1 rounded">←</kbd> keep left</span>
            <span><kbd className="font-mono bg-gray-100 text-gray-600 px-1 rounded">D</kbd>/<kbd className="font-mono bg-gray-100 text-gray-600 px-1 rounded">→</kbd> keep right</span>
            <span><kbd className="font-mono bg-gray-100 text-gray-600 px-1 rounded">R</kbd> reject</span>
            <span><kbd className="font-mono bg-gray-100 text-gray-600 px-1 rounded">S</kbd> skip</span>
            <span><kbd className="font-mono bg-gray-100 text-gray-600 px-1 rounded">?</kbd> help</span>
          </div>
        )}
      </div>

      {showHelp && (
        <div className="bg-emerald-50 border border-emerald-200 rounded-lg px-4 py-3 text-sm text-emerald-900">
          <p className="font-semibold mb-1.5">Keyboard shortcuts</p>
          <ul className="space-y-1 text-xs text-emerald-800">
            <li><kbd className="font-mono bg-white px-1.5 py-0.5 rounded border border-emerald-200">A</kbd> or <kbd className="font-mono bg-white px-1.5 py-0.5 rounded border border-emerald-200">←</kbd> — approve, keeping the <strong>left</strong> product (merges right into it)</li>
            <li><kbd className="font-mono bg-white px-1.5 py-0.5 rounded border border-emerald-200">D</kbd> or <kbd className="font-mono bg-white px-1.5 py-0.5 rounded border border-emerald-200">→</kbd> — approve, keeping the <strong>right</strong> product (merges left into it)</li>
            <li><kbd className="font-mono bg-white px-1.5 py-0.5 rounded border border-emerald-200">R</kbd> — reject (not a match)</li>
            <li><kbd className="font-mono bg-white px-1.5 py-0.5 rounded border border-emerald-200">S</kbd> — skip (decide later)</li>
            <li>Shortcuts act on the <strong>top card</strong> and only on the Pending queue. They're ignored while typing in a field.</li>
          </ul>
        </div>
      )}

      {error && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">{error}</div>}

      {/* Candidate cards */}
      <div className="flex flex-col gap-4">
        {items.map((c, i) => (
          <CandidateCard
            key={c.id}
            ref={i === 0 ? topCardRef : undefined}
            candidate={c}
            active={i === 0 && statusFilter === 'pending'}
            onDecided={handleDecided}
          />
        ))}
        {!loading && items.length === 0 && (
          <div className="text-center py-16">
            <div className="text-4xl mb-2">🎉</div>
            <div className="text-gray-500 text-sm">No {statusFilter} candidates — queue is clear.</div>
          </div>
        )}
        {loading && (
          <div className="flex justify-center py-16">
            <div className="w-8 h-8 border-2 border-gray-200 border-t-emerald-600 rounded-full animate-spin-slow" />
          </div>
        )}
      </div>

      {hasMore && (
        <div className="flex justify-center py-2">
          <AdmButton variant="secondary" onClick={() => loadCandidates(false)} disabled={loading}>
            Load more ({(total - items.length).toLocaleString()} remaining)
          </AdmButton>
        </div>
      )}

      {/* Manual link panel */}
      <div className="border-t border-gray-100 pt-4">
        <button className="text-sm font-semibold text-emerald-600 hover:text-emerald-700" onClick={() => setShowLink(v => !v)}>
          {showLink ? '▲' : '▼'} Manual product link
        </button>
        {showLink && (
          <div className="mt-3.5 flex flex-col gap-3 p-4 border border-emerald-200 rounded-xl bg-emerald-50/40">
            <p className="text-sm text-gray-500">Search for two products that are the same item and propose them as a match.</p>
            <input
              className="input-base text-sm"
              placeholder="Search product name…"
              value={searchQ}
              onChange={e => handleSearchChange(e.target.value)}
            />
            {searchResults.length > 0 && (
              <div className="border border-gray-200 rounded-lg bg-white max-h-60 overflow-y-auto divide-y divide-gray-100">
                {searchResults.map(sr => (
                  <div key={sr.id} className="flex items-center gap-2 px-3 py-2.5">
                    <span className="flex-1 text-sm text-gray-900 truncate">{sr.name}</span>
                    {sr.chain && <ChainBadge chain={sr.chain} size="sm" />}
                    {sr.price_aud != null && <span className="text-xs font-semibold text-emerald-600">${sr.price_aud.toFixed(2)}</span>}
                    <AdmButton variant="primary" size="sm" disabled={!!linkA && !!linkB}
                      onClick={() => { if (!linkA) setLinkA(sr); else if (!linkB && sr.id !== linkA.id) setLinkB(sr) }}>
                      {!linkA ? 'Set A' : 'Set B'}
                    </AdmButton>
                  </div>
                ))}
              </div>
            )}
            <div className="flex flex-col gap-1.5">
              <div className="flex items-center gap-2 text-sm">
                <span className="font-bold text-gray-700 w-5">A:</span>
                {linkA ? <span className="text-gray-900">{linkA.name} <button className="text-gray-400 hover:text-gray-700" onClick={() => setLinkA(null)}>✕</button></span> : <span className="text-gray-400">not set</span>}
              </div>
              <div className="flex items-center gap-2 text-sm">
                <span className="font-bold text-gray-700 w-5">B:</span>
                {linkB ? <span className="text-gray-900">{linkB.name} <button className="text-gray-400 hover:text-gray-700" onClick={() => setLinkB(null)}>✕</button></span> : <span className="text-gray-400">not set</span>}
              </div>
            </div>
            <AdmButton variant="primary" className="self-start" disabled={!linkA || !linkB || linkBusy} onClick={handlePropose}>
              {linkBusy ? 'Proposing…' : 'Propose Match'}
            </AdmButton>
            {linkMsg && <div className={`text-sm ${linkMsg.startsWith('Error') ? 'text-red-500' : 'text-emerald-600'}`}>{linkMsg}</div>}
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Tab: AI Dedup Run ────────────────────────────────────────────────────────

interface PairEvent {
  judged: number; score: number; decision: string
  confidence: number | null; reason: string | null
  chain_a: string; chain_b: string; name_a: string; name_b: string; dry_run: boolean
}

interface DoneEvent {
  judged: number; same: number; different: number; vetoed: number; merged: number; dry_run: boolean
}

interface ConfigEvent {
  model: string; conf_min: number; max_tokens: number
  limit: number; min_fuzzy: number; auto_merge: boolean; dry_run: boolean
}

type LogLine =
  | { type: 'config'; data: ConfigEvent }
  | { type: 'status'; data: { message: string } }
  | { type: 'pair'; data: PairEvent }
  | { type: 'done'; data: DoneEvent }
  | { type: 'error'; data: { message: string } }

function decisionTone(d: string): PillTone {
  if (d === 'same') return 'green'
  if (d === 'different') return 'gray'
  if (d === 'vetoed') return 'red'
  return 'amber'
}

interface DedupRunState {
  limit: number
  minFuzzy: number
  autoMerge: boolean
  dryRun: boolean
  running: boolean
  lines: LogLine[]
  done: DoneEvent | null
}

function DedupRunTab({
  state, setState, esRef,
}: {
  state: DedupRunState
  setState: React.Dispatch<React.SetStateAction<DedupRunState>>
  esRef: React.MutableRefObject<EventSource | null>
}) {
  const { limit, minFuzzy, autoMerge, dryRun, running, lines, done } = state
  const logRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [lines])

  function start() {
    if (running) return
    setState(s => ({ ...s, lines: [], done: null, running: true }))

    async function openStream() {
      let token = ''
      try {
        const { auth } = await import('../lib/firebase')
        if (auth.currentUser) token = await auth.currentUser.getIdToken()
      } catch { /* ignore — fall through to localStorage */ }
      if (!token) token = localStorage.getItem('auth_token') || ''

      const params = new URLSearchParams({
        limit: String(limit),
        min_fuzzy: String(minFuzzy),
        auto_merge: String(autoMerge),
        dry_run: String(dryRun),
        token,
      })
      const es = new EventSource(`/api/v1/admin/dedup/run?${params}`)
      esRef.current = es

      function onEvent(type: LogLine['type']) {
        return (e: MessageEvent) => {
          const data = JSON.parse(e.data)
          setState(s => ({ ...s, lines: [...s.lines, { type, data } as LogLine] }))
          if (type === 'done') {
            setState(s => ({ ...s, done: data as DoneEvent, running: false }))
            es.close()
          }
          if (type === 'error') {
            setState(s => ({ ...s, running: false }))
            es.close()
          }
        }
      }

      es.addEventListener('config', onEvent('config'))
      es.addEventListener('status', onEvent('status'))
      es.addEventListener('pair', onEvent('pair'))
      es.addEventListener('done', onEvent('done'))
      es.addEventListener('error', onEvent('error'))
      es.onerror = () => {
        setState(s => ({ ...s, lines: [...s.lines, { type: 'error', data: { message: 'Connection lost.' } }], running: false }))
        es.close()
      }
    }
    openStream()
  }

  function stop() {
    esRef.current?.close()
    setState(s => ({ ...s, running: false, lines: [...s.lines, { type: 'status', data: { message: '— stopped by user —' } }] }))
  }

  return (
    <div className="space-y-5">
      {/* Options */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <label className="flex flex-col gap-1 text-sm font-medium text-gray-700">
          Limit (pairs)
          <input
            type="number" min={0} value={limit}
            onChange={e => setState(s => ({ ...s, limit: Number(e.target.value) }))}
            className="input-base" disabled={running}
          />
          <span className="text-xs text-gray-400 font-normal">0 = unlimited</span>
        </label>
        <label className="flex flex-col gap-1 text-sm font-medium text-gray-700">
          Min fuzzy score
          <input
            type="number" min={0} max={100} step={1} value={minFuzzy}
            onChange={e => setState(s => ({ ...s, minFuzzy: Number(e.target.value) }))}
            className="input-base" disabled={running}
          />
        </label>
        <div className="flex flex-col gap-3 pt-1">
          <label className="flex items-center gap-2 text-sm font-medium text-gray-700 cursor-pointer select-none">
            <button
              role="switch" aria-checked={dryRun} disabled={running}
              onClick={() => setState(s => ({ ...s, dryRun: !s.dryRun }))}
              className={`relative inline-flex h-5 w-9 rounded-full transition-colors focus:outline-none
                ${dryRun ? 'bg-emerald-600' : 'bg-gray-300'} ${running ? 'opacity-50 cursor-not-allowed' : ''}`}
            >
              <span className={`inline-block h-4 w-4 mt-0.5 rounded-full bg-white shadow transition-transform ${dryRun ? 'translate-x-4' : 'translate-x-0.5'}`} />
            </button>
            Dry run
          </label>
          <label className="flex items-center gap-2 text-sm font-medium text-gray-700 cursor-pointer select-none">
            <button
              role="switch" aria-checked={autoMerge} disabled={running || dryRun}
              onClick={() => setState(s => ({ ...s, autoMerge: !s.autoMerge }))}
              className={`relative inline-flex h-5 w-9 rounded-full transition-colors focus:outline-none
                ${autoMerge ? 'bg-emerald-600' : 'bg-gray-300'} ${(running || dryRun) ? 'opacity-50 cursor-not-allowed' : ''}`}
            >
              <span className={`inline-block h-4 w-4 mt-0.5 rounded-full bg-white shadow transition-transform ${autoMerge ? 'translate-x-4' : 'translate-x-0.5'}`} />
            </button>
            Auto-merge
          </label>
        </div>
        <div className="flex items-end pb-5">
          {running
            ? <AdmButton variant="danger" size="sm" onClick={stop}>Stop</AdmButton>
            : <AdmButton variant="primary" size="sm" onClick={start}>Run AI Dedup</AdmButton>
          }
        </div>
      </div>

      {dryRun && (
        <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
          Dry run — verdicts will be shown but nothing will be written to the database.
        </div>
      )}

      {/* Live log */}
      {lines.length > 0 && (
        <div
          ref={logRef}
          className="bg-gray-950 rounded-xl p-4 h-96 overflow-y-auto font-mono text-xs space-y-1"
        >
          {lines.map((line, i) => {
            if (line.type === 'config') {
              const d = line.data
              return (
                <div key={i} className="text-gray-400">
                  model={d.model} conf_min={d.conf_min} max_tokens={d.max_tokens}
                  {d.limit ? ` limit=${d.limit}` : ' limit=unlimited'}
                  {d.dry_run ? ' [dry-run]' : ''}
                  {d.auto_merge ? ' [auto-merge]' : ''}
                </div>
              )
            }
            if (line.type === 'status') {
              return <div key={i} className="text-gray-400">{line.data.message}</div>
            }
            if (line.type === 'pair') {
              const d = line.data
              const tone = decisionTone(d.decision)
              const confStr = d.confidence != null ? ` (${(d.confidence * 100).toFixed(0)}%)` : ''
              return (
                <div key={i} className="flex items-start gap-2 flex-wrap">
                  <span className="text-gray-500 shrink-0 w-6 text-right">{d.judged}</span>
                  <span className={`shrink-0 font-bold ${tone === 'green' ? 'text-emerald-400' : tone === 'red' ? 'text-red-400' : 'text-gray-400'}`}>
                    {d.decision.toUpperCase()}{confStr}
                  </span>
                  <span className="text-emerald-300 shrink-0">[{d.score.toFixed(1)}%]</span>
                  <span className="text-white">{d.name_a}</span>
                  <span className="text-gray-500">↔</span>
                  <span className="text-white">{d.name_b}</span>
                  <span className="text-gray-600 shrink-0">[{d.chain_a}↔{d.chain_b}]</span>
                  {d.reason && <span className="text-gray-500 truncate max-w-xs">{d.reason}</span>}
                </div>
              )
            }
            if (line.type === 'done') {
              const d = line.data
              return (
                <div key={i} className="text-emerald-400 font-bold mt-2">
                  ✓ Done{d.dry_run ? ' (dry-run)' : ''}: {d.judged} judged —
                  {d.same} same, {d.different} different, {d.vetoed} vetoed
                  {d.merged > 0 ? `, ${d.merged} auto-merged` : ''}
                </div>
              )
            }
            if (line.type === 'error') {
              return <div key={i} className="text-red-400">{line.data.message}</div>
            }
            return null
          })}
          {running && <div className="text-gray-500 animate-pulse">…</div>}
        </div>
      )}

      {/* Summary card after run */}
      {done && (
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
          {[
            { label: 'Judged', value: done.judged, color: 'text-gray-900' },
            { label: 'Same', value: done.same, color: 'text-emerald-600' },
            { label: 'Different', value: done.different, color: 'text-gray-500' },
            { label: 'Vetoed', value: done.vetoed, color: 'text-red-500' },
            { label: 'Merged', value: done.merged, color: 'text-emerald-600' },
          ].map(s => (
            <div key={s.label} className="bg-gray-50 rounded-lg p-3 text-center">
              <div className={`text-2xl font-bold ${s.color}`}>{s.value}</div>
              <div className="text-xs text-gray-500 mt-0.5">{s.label}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Main Admin component ────────────────────────────────────────────────────

const DEDUP_RUN_INITIAL: DedupRunState = {
  limit: 20, minFuzzy: 85, autoMerge: false, dryRun: true,
  running: false, lines: [], done: null,
}

export default function Admin() {
  const [topTab, setTopTab] = useState<TopTab>('crawlers')
  const [crawlerTab, setCrawlerTab] = useState<CrawlerTab>('catalog')
  const [dedupSubTab, setDedupSubTab] = useState<DedupSubTab>('health')
  const [stats, setStats] = useState<Stats | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [registeredChains, setRegisteredChains] = useState<number>(0)
  const [dedupRunState, setDedupRunState] = useState<DedupRunState>(DEDUP_RUN_INITIAL)
  const dedupEsRef = useRef<EventSource | null>(null)

  useEffect(() => { void loadStats() }, [])

  // Close the EventSource when navigating away from Admin entirely.
  useEffect(() => {
    return () => {
      dedupEsRef.current?.close()
    }
  }, [])

  async function loadStats() {
    try {
      const [statsRes, catalogRes] = await Promise.all([
        apiClient.get<Stats>('/api/v1/admin/stats'),
        apiClient.get<CatalogConfig>('/api/v1/admin/catalog/config'),
      ])
      setStats(statsRes.data)
      setRegisteredChains(catalogRes.data.available_chains.length)
      setError(null)
    }
    catch { setError('Could not load stats. Is the backend running?') }
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-[1280px] mx-auto px-6 sm:px-8 lg:px-10 py-8">
        <div className="mb-6">
          <h1 className="text-2xl font-bold text-gray-900">Admin Panel</h1>
          <p className="text-sm text-gray-500 mt-0.5">Scraper management &amp; data ops</p>
        </div>

        {error && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm mb-5">{error}</div>}

        {stats && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <StatCard value={stats.total_products.toLocaleString()} label="Total Products" color="#4f46e5" />
            <StatCard value={stats.total_price_reports.toLocaleString()} label="Price Reports" color="#0ea5e9" />
            <StatCard value={Object.keys(stats.last_scrape_by_chain).filter(k => stats.last_scrape_by_chain[k]).length.toString()} label="Chains Scraped" color="#10b981" />
            <StatCard value={registeredChains.toString()} label="Registered Chains" color="#f59e0b" />
          </div>
        )}

        {/* Top-level section switcher */}
        <div className="flex gap-2 mb-4">
          {(['crawlers', 'dedup', 'configs'] as const).map(t => (
            <button
              key={t}
              className={`px-6 py-2 rounded-lg text-sm font-semibold border transition-colors inline-flex items-center gap-2
                ${topTab === t
                  ? 'bg-emerald-600 text-white border-emerald-600'
                  : 'bg-white text-gray-600 border-gray-200 hover:border-emerald-300 hover:text-emerald-700'}`}
              onClick={() => setTopTab(t)}
            >
              {titleCase(t)}
              {t === 'dedup' && dedupRunState.running && (
                <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse" title="AI Dedup running" />
              )}
            </button>
          ))}
        </div>

        {topTab === 'crawlers' && (
          <>
            <div className="flex gap-1 border-b border-gray-200 mb-0">
              {CRAWLER_TABS.map(t => (
                <button
                  key={t.id}
                  className={`px-4 py-2.5 text-sm font-medium -mb-px border-b-2 transition-colors whitespace-nowrap
                    ${crawlerTab === t.id
                      ? 'text-emerald-700 border-emerald-600'
                      : 'text-gray-500 border-transparent hover:text-gray-700'}`}
                  onClick={() => setCrawlerTab(t.id)}
                >
                  {t.label}
                </button>
              ))}
            </div>
            <div className="bg-white border border-gray-200 rounded-b-xl rounded-tr-xl min-h-[400px]">
              {crawlerTab === 'catalog'     && <CatalogTab />}
              {crawlerTab === 'enrichment'  && <EnrichmentTab />}
              {crawlerTab === 'coverage'    && <CoverageTab />}
            </div>
          </>
        )}

        {topTab === 'dedup' && (
          <>
            <div className="flex gap-1 border-b border-gray-200">
              {(['health', 'review', 'run'] as const).map(t => (
                <button
                  key={t}
                  className={`px-4 py-2.5 text-sm font-medium -mb-px border-b-2 transition-colors inline-flex items-center gap-2
                    ${dedupSubTab === t
                      ? 'text-emerald-700 border-emerald-600'
                      : 'text-gray-500 border-transparent hover:text-gray-700'}`}
                  onClick={() => setDedupSubTab(t)}
                >
                  {titleCase(t)}
                  {t === 'run' && dedupRunState.running && (
                    <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
                  )}
                  {t === 'review' && dedupRunState.running && (
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" title="Auto-refreshing while run is active" />
                  )}
                </button>
              ))}
            </div>
            <div className="bg-white border border-gray-200 rounded-b-xl rounded-tr-xl min-h-[400px] p-6 sm:p-7">
              {dedupSubTab === 'health' && <DedupTab />}
              {dedupSubTab === 'review' && <DedupReviewTab dedupRunning={dedupRunState.running} />}
              {dedupSubTab === 'run' && (
                <DedupRunTab state={dedupRunState} setState={setDedupRunState} esRef={dedupEsRef} />
              )}
            </div>
          </>
        )}

        {topTab === 'configs' && (
          <div className="bg-white border border-gray-200 rounded-xl min-h-[400px]">
            <ConfigsTab />
          </div>
        )}
      </div>
    </div>
  )
}
