import React, { useState, useEffect } from 'react'
import { sendPasswordResetEmail } from 'firebase/auth'
import { auth } from '../lib/firebase'
import { apiClient } from '../api/client'
import { useAuth } from '../contexts/AuthContext'
import { useGeolocation } from '../hooks/useGeolocation'

async function geocodeAU(query: string): Promise<{ lat: number; lon: number; label: string } | null> {
  try {
    const url = `https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(query)}&countrycodes=au&format=json&limit=1&accept-language=en`
    const r = await fetch(url, { headers: { 'Accept-Language': 'en' } })
    const data = await r.json()
    if (!data.length) return null
    const parts = data[0].display_name.split(',')
    const label = parts.slice(0, 2).join(',').trim()
    return { lat: parseFloat(data[0].lat), lon: parseFloat(data[0].lon), label }
  } catch {
    return null
  }
}

interface UserProfile {
  id: string
  email: string
  display_name?: string
  created_at?: string
  reputation_score?: number
  tier?: string
  default_radius_km?: number
  home_lat?: number | null
  home_lon?: number | null
}

export const Profile: React.FC = () => {
  const { signOut } = useAuth()
  const [user, setUser] = useState<UserProfile | null>(null)
  const [userLoading, setUserLoading] = useState(true)

  // Edit profile state
  const [editing, setEditing] = useState(false)
  const [editName, setEditName] = useState('')
  const [editRadius, setEditRadius] = useState(10)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')

  // Preferences (localStorage only — no backend field yet)
  const [emailNotifications, setEmailNotifications] = useState(() =>
    localStorage.getItem('pref_email_notifications') !== 'false'
  )
  const [saveSearchHistory, setSaveSearchHistory] = useState(() =>
    localStorage.getItem('pref_save_search_history') !== 'false'
  )

  // Location
  const { position, status, error: geoError, requestLocation, clearLocation } = useGeolocation()
  const locationEnabled = status === 'granted' && position !== null

  // Location editing state
  const [locationQuery, setLocationQuery] = useState('')
  const [locationGeocoding, setLocationGeocoding] = useState(false)
  const [locationGeoError, setLocationGeoError] = useState('')
  const [locationGpsRequesting, setLocationGpsRequesting] = useState(false)

  // Password reset
  const [pwResetSent, setPwResetSent] = useState(false)
  const [pwResetError, setPwResetError] = useState('')

  useEffect(() => {
    apiClient
      .get<UserProfile>('/api/v1/users/me')
      .then((r) => {
        setUser(r.data)
        setEditName(r.data.display_name ?? '')
        setEditRadius(r.data.default_radius_km ?? 10)
      })
      .catch(() => setUser(null))
      .finally(() => setUserLoading(false))
  }, [])

  function handleLocationToggle() {
    if (locationEnabled) {
      clearLocation()
    } else {
      requestLocation()
    }
  }

  async function handleGpsInEdit() {
    setLocationGpsRequesting(true)
    setLocationGeoError('')
    requestLocation()
  }

  // When GPS comes back while we requested it from edit form, stop the spinner
  useEffect(() => {
    if (locationGpsRequesting && (status === 'granted' || status === 'denied' || status === 'error')) {
      setLocationGpsRequesting(false)
      if (status === 'denied') setLocationGeoError('Location permission denied.')
      else if (status === 'error') setLocationGeoError(geoError ?? 'Location error.')
    }
  }, [status, locationGpsRequesting, geoError])

  async function handleLocationSearch() {
    if (!locationQuery.trim()) return
    setLocationGeocoding(true)
    setLocationGeoError('')
    const result = await geocodeAU(locationQuery.trim())
    setLocationGeocoding(false)
    if (!result) {
      setLocationGeoError('Location not found. Try a suburb name or postcode.')
      return
    }
    const stored = { lat: result.lat, lon: result.lon, accuracy: 1000, label: result.label }
    localStorage.setItem('dealfindio_location', JSON.stringify(stored))
    window.dispatchEvent(new StorageEvent('storage', {
      key: 'dealfindio_location',
      newValue: JSON.stringify(stored),
    }))
    setLocationQuery('')
  }

  function toggleEmailNotifications() {
    const next = !emailNotifications
    setEmailNotifications(next)
    localStorage.setItem('pref_email_notifications', String(next))
  }

  function toggleSaveSearchHistory() {
    const next = !saveSearchHistory
    setSaveSearchHistory(next)
    localStorage.setItem('pref_save_search_history', String(next))
  }

  async function handleSaveProfile() {
    if (!editName.trim()) {
      setSaveError('Display name cannot be empty.')
      return
    }
    setSaving(true)
    setSaveError('')
    try {
      const res = await apiClient.patch<UserProfile>('/api/v1/users/me', {
        display_name: editName.trim(),
        default_radius_km: editRadius,
      })
      setUser(res.data)
      setEditing(false)
    } catch {
      setSaveError('Failed to save. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  async function handleChangePassword() {
    if (!user?.email) return
    setPwResetError('')
    try {
      await sendPasswordResetEmail(auth, user.email)
      setPwResetSent(true)
    } catch (e: unknown) {
      setPwResetError('Failed to send reset email. Try again.')
    }
  }

  async function handleLogout() {
    await signOut()
  }

  const initials = user?.display_name
    ? user.display_name.split(' ').map((w) => w[0]).join('').toUpperCase().slice(0, 2)
    : user?.email?.[0]?.toUpperCase() ?? '?'

  const memberSince = user?.created_at
    ? new Date(user.created_at).toLocaleDateString('en-AU', { month: 'long', year: 'numeric' })
    : null

  return (
    <div className="min-h-screen bg-gray-50 py-12">
      <div className="max-w-[1280px] mx-auto px-6 sm:px-8 lg:px-10">
      <div className="max-w-3xl mx-auto">
        <h1 className="text-4xl font-bold text-gray-900 mb-8">My Profile</h1>

        {/* User Card */}
        <div className="bg-white rounded-lg shadow-md p-8 mb-8">
          {userLoading ? (
            <div className="flex items-center gap-4 mb-8">
              <div className="w-16 h-16 bg-gray-200 rounded-full animate-pulse" />
              <div className="space-y-2">
                <div className="h-6 w-40 bg-gray-200 rounded animate-pulse" />
                <div className="h-4 w-56 bg-gray-200 rounded animate-pulse" />
              </div>
            </div>
          ) : editing ? (
            <div className="mb-6">
              <h2 className="text-xl font-bold text-gray-900 mb-4">Edit Profile</h2>
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-1">
                    Display Name
                  </label>
                  <input
                    type="text"
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    maxLength={255}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500"
                  />
                </div>
                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-1">
                    Default Search Radius: {editRadius} km
                  </label>
                  <input
                    type="range"
                    min={1}
                    max={100}
                    value={editRadius}
                    onChange={(e) => setEditRadius(Number(e.target.value))}
                    className="w-full accent-emerald-600"
                  />
                  <div className="flex justify-between text-xs text-gray-400 mt-1">
                    <span>1 km</span><span>100 km</span>
                  </div>
                </div>
                {/* Location */}
                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-1">
                    Home Location
                  </label>
                  {position && (
                    <div className="flex items-center gap-2 mb-2 text-sm text-gray-700">
                      <svg className="w-4 h-4 text-emerald-500 flex-shrink-0" fill="currentColor" viewBox="0 0 20 20">
                        <path fillRule="evenodd" d="M5.05 4.05a7 7 0 119.9 9.9L10 18.9l-4.95-4.95a7 7 0 010-9.9zM10 11a2 2 0 100-4 2 2 0 000 4z" clipRule="evenodd"/>
                      </svg>
                      <span>{position.label ?? `${position.lat.toFixed(4)}, ${position.lon.toFixed(4)}`}</span>
                      <button
                        type="button"
                        onClick={clearLocation}
                        className="ml-auto text-xs text-gray-400 hover:text-red-500 transition cursor-pointer"
                      >
                        Clear
                      </button>
                    </div>
                  )}
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={handleGpsInEdit}
                      disabled={locationGpsRequesting || status === 'requesting'}
                      className="flex items-center gap-1.5 px-3 py-2 text-sm border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50 transition cursor-pointer"
                    >
                      {locationGpsRequesting || status === 'requesting' ? (
                        <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
                        </svg>
                      ) : (
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"/>
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"/>
                        </svg>
                      )}
                      Use GPS
                    </button>
                    <input
                      type="text"
                      value={locationQuery}
                      onChange={(e) => setLocationQuery(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && handleLocationSearch()}
                      placeholder="Suburb or postcode"
                      className="flex-1 px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-500"
                    />
                    <button
                      type="button"
                      onClick={handleLocationSearch}
                      disabled={locationGeocoding || !locationQuery.trim()}
                      className="px-3 py-2 text-sm bg-amber-500 text-gray-900 rounded-lg hover:bg-amber-600 disabled:opacity-50 transition cursor-pointer"
                    >
                      {locationGeocoding ? '…' : 'Set'}
                    </button>
                  </div>
                  {locationGeoError && (
                    <p className="mt-1.5 text-xs text-red-600">{locationGeoError}</p>
                  )}
                </div>

                {saveError && <p className="text-sm text-red-600">{saveError}</p>}
                <div className="flex gap-3">
                  <button
                    onClick={handleSaveProfile}
                    disabled={saving}
                    className="px-5 py-2 bg-amber-500 text-gray-900 rounded-lg font-semibold hover:bg-amber-600 disabled:opacity-50 transition"
                  >
                    {saving ? 'Saving…' : 'Save'}
                  </button>
                  <button
                    onClick={() => { setEditing(false); setSaveError('') }}
                    className="px-5 py-2 border border-gray-300 text-gray-700 rounded-lg font-semibold hover:bg-gray-50 transition"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            </div>
          ) : (
            <div className="flex items-start justify-between mb-8">
              <div className="flex items-center gap-4">
                <div className="w-16 h-16 bg-gradient-to-br from-emerald-400 to-emerald-600 rounded-full flex items-center justify-center">
                  <span className="text-white text-2xl font-bold">{initials}</span>
                </div>
                <div>
                  <h2 className="text-2xl font-bold text-gray-900">
                    {user?.display_name ?? user?.email ?? 'Guest'}
                  </h2>
                  {user?.email && user.display_name && (
                    <p className="text-gray-600">{user.email}</p>
                  )}
                  {user?.tier && (
                    <span className="inline-block mt-1 px-2 py-0.5 text-xs font-semibold bg-emerald-100 text-emerald-700 rounded-full capitalize">
                      {user.tier}
                    </span>
                  )}
                </div>
              </div>
              <button
                onClick={() => setEditing(true)}
                className="px-4 py-2 text-emerald-600 border border-emerald-600 rounded-lg hover:bg-emerald-50 transition font-semibold cursor-pointer"
              >
                Edit Profile
              </button>
            </div>
          )}

          {!editing && (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6 border-t pt-6">
              <div>
                <p className="text-sm font-semibold text-gray-500 uppercase mb-2">Location</p>
                {position ? (
                  <p className="text-sm text-gray-900">
                    {position.label ?? `${position.lat.toFixed(4)}, ${position.lon.toFixed(4)}`}
                  </p>
                ) : (
                  <p className="text-sm text-gray-400">Not set</p>
                )}
              </div>
              <div>
                <p className="text-sm font-semibold text-gray-500 uppercase mb-2">Search Radius</p>
                <p className="text-sm text-gray-900">{user?.default_radius_km ?? 10} km</p>
              </div>
              {memberSince && (
                <div>
                  <p className="text-sm font-semibold text-gray-500 uppercase mb-2">Member Since</p>
                  <p className="text-sm text-gray-900">{memberSince}</p>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Preferences */}
        <div className="bg-white rounded-lg shadow-md p-8 mb-8">
          <h3 className="text-2xl font-bold text-gray-900 mb-6">Preferences</h3>
          <div className="space-y-4">
            <div className="flex items-center justify-between p-4 border border-gray-200 rounded-lg">
              <div>
                <p className="font-semibold text-gray-900">Email Notifications</p>
                <p className="text-sm text-gray-600">Get deals and price alerts</p>
              </div>
              <button
                onClick={toggleEmailNotifications}
                aria-pressed={emailNotifications}
                aria-label="Toggle email notifications"
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors cursor-pointer ${emailNotifications ? 'bg-emerald-600' : 'bg-gray-300'}`}
              >
                <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${emailNotifications ? 'translate-x-6' : 'translate-x-1'}`} />
              </button>
            </div>

            <div className="flex items-center justify-between p-4 border border-gray-200 rounded-lg">
              <div>
                <p className="font-semibold text-gray-900">Save Search History</p>
                <p className="text-sm text-gray-600">Remember my recent searches</p>
              </div>
              <button
                onClick={toggleSaveSearchHistory}
                aria-pressed={saveSearchHistory}
                aria-label="Toggle save search history"
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors cursor-pointer ${saveSearchHistory ? 'bg-emerald-600' : 'bg-gray-300'}`}
              >
                <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${saveSearchHistory ? 'translate-x-6' : 'translate-x-1'}`} />
              </button>
            </div>

            <div className="flex items-center justify-between p-4 border border-gray-200 rounded-lg">
              <div>
                <p className="font-semibold text-gray-900">Location Services</p>
                <p className="text-sm text-gray-600">
                  {status === 'requesting'
                    ? 'Requesting location...'
                    : status === 'denied'
                    ? 'Permission denied — enable in browser settings'
                    : status === 'unavailable'
                    ? 'Not supported by your browser'
                    : status === 'error'
                    ? geoError ?? 'Location error'
                    : locationEnabled
                    ? `Using your location (±${Math.round(position!.accuracy)}m)`
                    : 'Help us find nearby stores'}
                </p>
              </div>
              <button
                onClick={handleLocationToggle}
                disabled={status === 'requesting' || status === 'unavailable'}
                aria-pressed={locationEnabled}
                aria-label="Toggle location services"
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer ${locationEnabled ? 'bg-emerald-600' : 'bg-gray-300'}`}
              >
                <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${locationEnabled ? 'translate-x-6' : 'translate-x-1'}`} />
              </button>
            </div>
          </div>
        </div>

        {/* Account Actions */}
        <div className="bg-white rounded-lg shadow-md p-8">
          <h3 className="text-2xl font-bold text-gray-900 mb-6">Account</h3>
          <div className="space-y-3">
            {pwResetSent ? (
              <div className="p-4 bg-green-50 border border-green-200 rounded-lg text-green-800 text-sm font-medium">
                Password reset email sent to {user?.email}. Check your inbox.
              </div>
            ) : (
              <button
                onClick={handleChangePassword}
                className="w-full px-4 py-3 text-left text-gray-900 font-semibold border border-gray-200 rounded-lg hover:border-gray-300 hover:bg-gray-50 transition cursor-pointer"
              >
                Change Password
              </button>
            )}
            {pwResetError && (
              <p className="text-sm text-red-600 px-1">{pwResetError}</p>
            )}
            <button
              onClick={handleLogout}
              className="w-full px-4 py-3 text-left text-red-600 font-semibold border border-red-200 rounded-lg hover:bg-red-50 transition cursor-pointer"
            >
              Logout
            </button>
            <button
              onClick={() => window.confirm('To delete your account, please contact support@dealfindio.com.au') }
              className="w-full px-4 py-3 text-left text-red-600 font-semibold border border-red-200 rounded-lg hover:bg-red-50 transition cursor-pointer"
            >
              Delete Account
            </button>
          </div>
        </div>
        </div>
      </div>
    </div>
  )
}
