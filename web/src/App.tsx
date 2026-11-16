import React, { useEffect } from 'react'
import { BrowserRouter as Router, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import { Header } from './components/Header'
import { Footer } from './components/Footer'
import { ProtectedRoute } from './components/ProtectedRoute'
import { Home } from './pages/Home'
import { Landing } from './pages/Landing'
import { Login } from './pages/Login'
import { Products } from './pages/Products'
import { Product } from './pages/Product'
import { Profile } from './pages/Profile'
import { Stores } from './pages/Stores'
import { Watchlist } from './pages/Watchlist'
import { Notifications } from './pages/Notifications'
import { NotificationPreferences } from './pages/NotificationPreferences'
import { Deals } from './pages/Deals'
import Admin from './pages/Admin'
import './App.css'

function ScrollToTop() {
  const { pathname, search } = useLocation()
  useEffect(() => { window.scrollTo(0, 0) }, [pathname, search])
  return null
}

function AdminRoute({ children }: { readonly children: React.ReactNode }) {
  const { user, loading, isAdmin } = useAuth()
  const location = useLocation()
  if (loading) return null
  if (!user) return <Navigate to={`/login?next=${encodeURIComponent(location.pathname)}`} replace />
  if (!isAdmin) return <Navigate to="/" replace />
  return <>{children}</>
}

function LandingRoute({ children }: { readonly children: React.ReactNode }) {
  const { user, loading } = useAuth()
  if (loading) return null
  if (user) return <Navigate to="/app" replace />
  return <>{children}</>
}

function AppShell({ children }: { readonly children: React.ReactNode }) {
  return (
    <div className="flex flex-col min-h-screen bg-gray-50">
      <Header />
      <main className="flex-1">{children}</main>
      <Footer />
    </div>
  )
}

function AppRoutes() {
  return (
    <>
      <ScrollToTop />
      <Routes>
        {/* Marketing landing — own nav/footer, no app shell */}
        <Route path="/" element={<LandingRoute><Landing /></LandingRoute>} />

        {/* App routes — wrapped in Header + Footer shell */}
        <Route path="/app" element={<AppShell><Home /></AppShell>} />
        <Route path="/login" element={<AppShell><Login /></AppShell>} />
        <Route path="/deals" element={<AppShell><Deals /></AppShell>} />
        <Route path="/products" element={<AppShell><Products /></AppShell>} />
        <Route path="/stores" element={<AppShell><Stores /></AppShell>} />
        <Route path="/product/:productId" element={<AppShell><Product /></AppShell>} />
        <Route path="/watchlist" element={<AppShell><ProtectedRoute><Watchlist /></ProtectedRoute></AppShell>} />
        <Route path="/notifications" element={<AppShell><ProtectedRoute><Notifications /></ProtectedRoute></AppShell>} />
        <Route path="/notifications/preferences" element={<AppShell><ProtectedRoute><NotificationPreferences /></ProtectedRoute></AppShell>} />
        <Route path="/profile" element={<AppShell><ProtectedRoute><Profile /></ProtectedRoute></AppShell>} />
        <Route path="/admin" element={<AppShell><AdminRoute><Admin /></AdminRoute></AppShell>} />
        <Route
          path="*"
          element={
            <AppShell>
              <div className="min-h-screen flex items-center justify-center">
                <div className="text-center">
                  <h1 className="text-4xl font-bold text-gray-900 mb-4">404 - Page Not Found</h1>
                  <p className="text-gray-600 mb-8">The page you're looking for doesn't exist.</p>
                  <a
                    href="/"
                    className="inline-block bg-amber-500 text-gray-900 px-6 py-3 rounded-lg font-semibold hover:bg-amber-600 transition"
                  >
                    Go Home
                  </a>
                </div>
              </div>
            </AppShell>
          }
        />
      </Routes>
    </>
  )
}

function App() {
  return (
    <AuthProvider>
      <Router>
        <AppRoutes />
      </Router>
    </AuthProvider>
  )
}

export default App
