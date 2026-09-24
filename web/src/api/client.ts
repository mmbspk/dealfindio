import axios, { AxiosInstance, AxiosError } from 'axios'

const API_BASE = import.meta.env.VITE_API_URL || ''

export const apiClient: AxiosInstance = axios.create({
  baseURL: API_BASE,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Attach a fresh Firebase ID token before every request.
// Falls back to a stored static token if Firebase isn't initialised yet.
apiClient.interceptors.request.use(async (config) => {
  try {
    const { auth } = await import('../lib/firebase')
    if (auth.currentUser) {
      const token = await auth.currentUser.getIdToken()
      config.headers.Authorization = `Bearer ${token}`
      return config
    }
  } catch {
    // Firebase not ready — fall through to localStorage fallback
  }
  const stored = localStorage.getItem('auth_token')
  if (stored) {
    config.headers.Authorization = `Bearer ${stored}`
  }
  return config
})

// Error handling
apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('auth_token')
    }
    return Promise.reject(error)
  }
)

export default apiClient
