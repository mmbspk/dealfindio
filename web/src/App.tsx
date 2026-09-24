import React, { useEffect } from 'react'
import { BrowserRouter as Router, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import { Header } from './components/Header'
import { Footer } from './components/Footer'
import { ProtectedRoute } from './components/ProtectedRoute'
import { Home } from './pages/Home'
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

function AdminRoute({ children }: { children: React.ReactNode }) {
  const { user, loading, isAdmin } = useAuth()
  const location = useLocation()
  if (loading) return null
  if (!user) return <Navigate to={`/login?next=${encodeURIComponent(location.pathname)}`} replace />
  if (!isAdmin) return <Navigate to="/" replace />
  return <>{children}</>
}

function AppRoutes() {
  return (
    <div className="flex flex-col min-h-screen bg-gray-50">
      <ScrollToTop />
      <Header />
      <main className="flex-1">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/login" element={<Login />} />
          <Route path="/deals" element={<Deals />} />
          <Route path="/products" element={<Products />} />
          <Route path="/stores" element={<Stores />} />
          <Route path="/product/:productId" element={<Product />} />
          <Route path="/watchlist" element={<ProtectedRoute><Watchlist /></ProtectedRoute>} />
          <Route path="/notifications" element={<ProtectedRoute><Notifications /></ProtectedRoute>} />
          <Route path="/notifications/preferences" element={<ProtectedRoute><NotificationPreferences /></ProtectedRoute>} />
          <Route path="/profile" element={<ProtectedRoute><Profile /></ProtectedRoute>} />
          <Route path="/admin" element={<AdminRoute><Admin /></AdminRoute>} />
          <Route
            path="*"
            element={
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
            }
          />
        </Routes>
      </main>
      <Footer />
    </div>
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
