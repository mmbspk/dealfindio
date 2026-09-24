import React, { useEffect, useState } from 'react'
import { Link, useNavigate, useLocation } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import { apiClient } from '../api/client'

export const Header: React.FC = () => {
  const { user, loading, isAdmin, signOut } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [unreadCount, setUnreadCount] = useState(0)
  const [menuOpen, setMenuOpen] = useState(false)

  // Close the mobile menu whenever the route changes.
  useEffect(() => { setMenuOpen(false) }, [location.pathname])

  function navClass(path: string) {
    const active = path === '/' ? location.pathname === '/' : location.pathname.startsWith(path)
    return active
      ? 'text-[#176f50] font-semibold border-b-2 border-[#176f50] pb-0.5 transition'
      : 'text-gray-600 hover:text-[#176f50] transition font-medium'
  }

  // Mobile menu links get block styling + an active surface instead of the underline.
  function mobileNavClass(path: string) {
    const active = path === '/' ? location.pathname === '/' : location.pathname.startsWith(path)
    return active
      ? 'block px-3 py-2 rounded-lg bg-emerald-50 text-[#135d46] font-semibold'
      : 'block px-3 py-2 rounded-lg text-gray-700 hover:bg-gray-50 font-medium'
  }

  useEffect(() => {
    if (!user) { setUnreadCount(0); return }
    let cancelled = false
    apiClient.get('/api/v1/notifications/unread-count')
      .then((r) => { if (!cancelled) setUnreadCount(r.data.count ?? 0) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [user])

  async function handleSignOut() {
    await signOut()
    navigate('/')
  }

  const initials = user
    ? (user.displayName ?? user.email ?? '?')
        .split(/[\s@]/)
        .filter(Boolean)
        .slice(0, 2)
        .map((s) => s[0].toUpperCase())
        .join('')
    : ''

  return (
    <header className="bg-white border-b border-gray-100 sticky top-0 z-50" style={{ height: 72 }}>
      <div className="max-w-[1280px] mx-auto px-6 sm:px-8 lg:px-12 h-full flex justify-between items-center">
        <Link to="/" className="flex items-center">
          <img
            src="/dealfindio-logo-horizontal.svg"
            alt="dealfindio"
            style={{ height: 52, width: 'auto', maxWidth: 220 }}
          />
        </Link>

        {/* Desktop nav */}
        <nav className="hidden md:flex gap-5 items-center">
          <Link to="/" className={navClass('/')}>Explore</Link>
          <Link to="/products" className={navClass('/products')}>Compare</Link>
          <Link to="/deals" className={navClass('/deals')}>Deals</Link>
          <Link to="/stores" className={navClass('/stores')}>Stores</Link>
          <Link to="/watchlist" className={navClass('/watchlist')}>Saved</Link>
          {isAdmin && (
            <Link to="/admin" className={navClass('/admin')}>Admin</Link>
          )}

          {/* Bell icon with unread badge */}
          {user && (
            <Link
              to="/notifications"
              className="relative p-1.5 text-gray-500 hover:text-[#176f50] transition-colors cursor-pointer"
              aria-label={unreadCount > 0 ? `${unreadCount} unread notifications` : 'Notifications'}
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75}
                  d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
              </svg>
              {unreadCount > 0 && (
                <span className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] flex items-center justify-center bg-[#176f50] text-white text-[10px] font-bold rounded-full px-1">
                  {unreadCount > 99 ? '99+' : unreadCount}
                </span>
              )}
            </Link>
          )}

          {/* Auth section */}
          {!loading && (
            user ? (
              <div className="flex items-center gap-3 ml-2">
                <Link
                  to="/profile"
                  className="w-8 h-8 rounded-full bg-[#176f50] flex items-center justify-center text-white text-sm font-bold hover:bg-[#135d46] transition cursor-pointer"
                  title={user.email ?? 'Profile'}
                  aria-label="Profile"
                >
                  {initials}
                </Link>
                <button
                  onClick={handleSignOut}
                  className="text-sm font-medium text-gray-500 hover:text-red-600 transition cursor-pointer"
                >
                  Logout
                </button>
              </div>
            ) : (
              <Link
                to="/login"
                className="ml-2 bg-lime-300 text-gray-900 px-4 py-1.5 rounded-lg text-sm font-semibold hover:bg-lime-400 transition"
              >
                Login
              </Link>
            )
          )}
        </nav>

        {/* Mobile cluster: bell + hamburger */}
        <div className="flex items-center gap-1 md:hidden">
          {user && (
            <Link
              to="/notifications"
              className="relative p-2 text-gray-500 hover:text-[#176f50] transition-colors"
              aria-label={unreadCount > 0 ? `${unreadCount} unread notifications` : 'Notifications'}
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75}
                  d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
              </svg>
              {unreadCount > 0 && (
                <span className="absolute top-0.5 right-0.5 min-w-[18px] h-[18px] flex items-center justify-center bg-[#176f50] text-white text-[10px] font-bold rounded-full px-1">
                  {unreadCount > 99 ? '99+' : unreadCount}
                </span>
              )}
            </Link>
          )}
          <button
            onClick={() => setMenuOpen((o) => !o)}
            className="p-2 text-gray-700 hover:text-[#176f50] transition-colors"
            aria-label={menuOpen ? 'Close menu' : 'Open menu'}
            aria-expanded={menuOpen}
          >
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              {menuOpen ? (
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              ) : (
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
              )}
            </svg>
          </button>
        </div>
      </div>

      {/* Mobile dropdown panel */}
      {menuOpen && (
        <nav className="md:hidden border-t border-gray-100 bg-white px-4 py-3 space-y-1">
          <Link to="/" className={mobileNavClass('/')}>Home</Link>
          <Link to="/stores" className={mobileNavClass('/stores')}>Stores</Link>
          <Link to="/products" className={mobileNavClass('/products')}>Products</Link>
          <Link to="/deals" className={mobileNavClass('/deals')}>Deals</Link>
          <Link to="/watchlist" className={mobileNavClass('/watchlist')}>Watchlist</Link>
          {isAdmin && (
            <Link to="/admin" className={mobileNavClass('/admin')}>Admin</Link>
          )}

          {!loading && (
            <div className="pt-2 mt-2 border-t border-gray-100">
              {user ? (
                <div className="flex items-center justify-between px-3 py-2">
                  <Link to="/profile" className="flex items-center gap-2 text-gray-700 font-medium">
                    <span className="w-8 h-8 rounded-full bg-[#176f50] flex items-center justify-center text-white text-sm font-bold">
                      {initials}
                    </span>
                    <span className="truncate max-w-[180px]">{user.email ?? 'Profile'}</span>
                  </Link>
                  <button
                    onClick={handleSignOut}
                    className="text-sm font-medium text-gray-500 hover:text-red-600 transition"
                  >
                    Logout
                  </button>
                </div>
              ) : (
                <Link
                  to="/login"
                  className="block text-center bg-lime-300 text-gray-900 px-4 py-2.5 rounded-lg text-sm font-semibold hover:bg-lime-400 transition"
                >
                  Login
                </Link>
              )}
            </div>
          )}
        </nav>
      )}
    </header>
  )
}
