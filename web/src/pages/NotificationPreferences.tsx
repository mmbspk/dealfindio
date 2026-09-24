import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiClient } from '../api/client'

interface Prefs {
  notif_push_price_match: boolean
  notif_push_pct_off: boolean
  notif_push_prediction: boolean
  notif_push_flash_sale: boolean
  notif_email_price_match: boolean
  notif_email_pct_off: boolean
  notif_email_prediction: boolean
  notif_email_weekly_digest: boolean
  notif_quiet_hours_start: number | null
  notif_quiet_hours_end: number | null
  notif_timezone: string
}

const AU_TIMEZONES = [
  { value: 'Australia/Sydney', label: 'Sydney / Melbourne (AEST/AEDT)' },
  { value: 'Australia/Brisbane', label: 'Brisbane (AEST)' },
  { value: 'Australia/Perth', label: 'Perth (AWST)' },
  { value: 'Australia/Adelaide', label: 'Adelaide (ACST/ACDT)' },
  { value: 'Australia/Darwin', label: 'Darwin (ACST)' },
  { value: 'Australia/Hobart', label: 'Hobart (AEST/AEDT)' },
]

const HOURS = Array.from({ length: 24 }, (_, i) => {
  const h = i % 12 === 0 ? 12 : i % 12
  const ampm = i < 12 ? 'am' : 'pm'
  return { value: i, label: `${h}:00 ${ampm}` }
})

function Toggle({ checked, onChange, label, sub }: { checked: boolean; onChange: (v: boolean) => void; label: string; sub?: string }) {
  return (
    <label className="flex items-center justify-between py-3 cursor-pointer group">
      <div>
        <span className="text-sm font-medium text-gray-800">{label}</span>
        {sub && <p className="text-xs text-gray-400 mt-0.5">{sub}</p>}
      </div>
      <button
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-emerald-500 focus:ring-offset-1 cursor-pointer ${
          checked ? 'bg-emerald-600' : 'bg-gray-200'
        }`}
      >
        <span
          className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
            checked ? 'translate-x-6' : 'translate-x-1'
          }`}
        />
      </button>
    </label>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-xl border border-gray-100 shadow-sm overflow-hidden mb-5">
      <div className="px-5 py-3 border-b border-gray-100 bg-gray-50">
        <h2 className="text-sm font-semibold text-gray-700 uppercase tracking-wider">{title}</h2>
      </div>
      <div className="px-5 divide-y divide-gray-100">{children}</div>
    </div>
  )
}

export const NotificationPreferences: React.FC = () => {
  const [prefs, setPrefs] = useState<Prefs | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    apiClient.get('/api/v1/notifications/preferences')
      .then((r) => setPrefs(r.data))
      .catch(() => setError('Failed to load preferences.'))
      .finally(() => setLoading(false))
  }, [])

  function set<K extends keyof Prefs>(key: K, value: Prefs[K]) {
    setPrefs((prev) => prev ? { ...prev, [key]: value } : prev)
    setSaved(false)
  }

  async function save() {
    if (!prefs) return
    setSaving(true)
    setError('')
    try {
      const resp = await apiClient.put('/api/v1/notifications/preferences', prefs)
      setPrefs(resp.data)
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    } catch {
      setError('Failed to save preferences.')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <svg className="w-10 h-10 text-emerald-600 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-50 py-12">
      <div className="max-w-[1280px] mx-auto px-6 sm:px-8 lg:px-10">
      <div className="max-w-2xl mx-auto">
        {/* Header */}
        <div className="flex items-center gap-3 mb-8">
          <Link to="/notifications" className="text-gray-400 hover:text-gray-600 cursor-pointer">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
          </Link>
          <h1 className="text-3xl font-bold text-gray-900">Notification Preferences</h1>
        </div>

        {error && (
          <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded-lg text-sm">{error}</div>
        )}

        {prefs && (
          <>
            <Section title="Push Notifications">
              <Toggle
                checked={prefs.notif_push_price_match}
                onChange={(v) => set('notif_push_price_match', v)}
                label="Price match"
                sub="When a product hits your target price"
              />
              <Toggle
                checked={prefs.notif_push_pct_off}
                onChange={(v) => set('notif_push_pct_off', v)}
                label="Discount alert"
                sub="When a product reaches your target % off"
              />
              <Toggle
                checked={prefs.notif_push_flash_sale}
                onChange={(v) => set('notif_push_flash_sale', v)}
                label="Flash sale (40%+ off)"
                sub="Big deals on watched products"
              />
              <Toggle
                checked={prefs.notif_push_prediction}
                onChange={(v) => set('notif_push_prediction', v)}
                label="Predicted price drop"
                sub="ML-predicted upcoming deals on your watchlist"
              />
            </Section>

            <Section title="Email Notifications">
              <Toggle
                checked={prefs.notif_email_price_match}
                onChange={(v) => set('notif_email_price_match', v)}
                label="Price match"
                sub="Email when a product hits your target price"
              />
              <Toggle
                checked={prefs.notif_email_pct_off}
                onChange={(v) => set('notif_email_pct_off', v)}
                label="Discount alert"
              />
              <Toggle
                checked={prefs.notif_email_prediction}
                onChange={(v) => set('notif_email_prediction', v)}
                label="Predicted price drop"
              />
              <Toggle
                checked={prefs.notif_email_weekly_digest}
                onChange={(v) => set('notif_email_weekly_digest', v)}
                label="Weekly deals digest"
                sub="Top deals from your watchlist every Monday"
              />
            </Section>

            <Section title="Quiet Hours">
              <div className="py-3">
                <p className="text-sm text-gray-500 mb-4">Push notifications are silenced during these hours.</p>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-medium text-gray-500 mb-1.5">From</label>
                    <select
                      value={prefs.notif_quiet_hours_start ?? ''}
                      onChange={(e) => set('notif_quiet_hours_start', e.target.value === '' ? null : Number(e.target.value))}
                      className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500 cursor-pointer"
                    >
                      <option value="">Disabled</option>
                      {HOURS.map((h) => <option key={h.value} value={h.value}>{h.label}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-gray-500 mb-1.5">Until</label>
                    <select
                      value={prefs.notif_quiet_hours_end ?? ''}
                      onChange={(e) => set('notif_quiet_hours_end', e.target.value === '' ? null : Number(e.target.value))}
                      className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500 cursor-pointer"
                    >
                      <option value="">Disabled</option>
                      {HOURS.map((h) => <option key={h.value} value={h.value}>{h.label}</option>)}
                    </select>
                  </div>
                </div>
                <div className="mt-4">
                  <label className="block text-xs font-medium text-gray-500 mb-1.5">Timezone</label>
                  <select
                    value={prefs.notif_timezone}
                    onChange={(e) => set('notif_timezone', e.target.value)}
                    className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500 cursor-pointer"
                  >
                    {AU_TIMEZONES.map((tz) => <option key={tz.value} value={tz.value}>{tz.label}</option>)}
                  </select>
                </div>
              </div>
            </Section>

            {/* Save */}
            <div className="flex items-center justify-end gap-3 mt-2">
              {saved && <span className="text-sm text-emerald-600 font-medium">Saved!</span>}
              <button
                onClick={save}
                disabled={saving}
                className="px-6 py-2.5 bg-amber-500 text-gray-900 text-sm font-semibold rounded-lg hover:bg-amber-600 disabled:opacity-50 transition-colors cursor-pointer"
              >
                {saving ? 'Saving…' : 'Save preferences'}
              </button>
            </div>
          </>
        )}
      </div>
      </div>
    </div>
  )
}
