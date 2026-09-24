/**
 * Axios API client for the dealfindio backend.
 *
 * Mirrors the web client's interceptor pattern but uses AsyncStorage (instead
 * of localStorage) for the auth token, since React Native has no `window`.
 */

import axios, { AxiosError, AxiosInstance } from 'axios'
import AsyncStorage from '@react-native-async-storage/async-storage'
import Constants from 'expo-constants'

const API_BASE =
  (Constants.expoConfig?.extra?.apiUrl as string | undefined) ||
  'http://localhost:8010'

const AUTH_TOKEN_KEY = 'auth_token'

export const apiClient: AxiosInstance = axios.create({
  baseURL: API_BASE,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Attach the Firebase auth token (if present) to every request.
apiClient.interceptors.request.use(async (config) => {
  const token = await AsyncStorage.getItem(AUTH_TOKEN_KEY)
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// On 401, clear the stored token so the app can prompt for re-login.
apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    if (error.response?.status === 401) {
      await AsyncStorage.removeItem(AUTH_TOKEN_KEY)
    }
    return Promise.reject(error)
  }
)

/** Persist the auth token (call after Firebase login). */
export async function setAuthToken(token: string): Promise<void> {
  await AsyncStorage.setItem(AUTH_TOKEN_KEY, token)
}

/** Clear the auth token (call on logout). */
export async function clearAuthToken(): Promise<void> {
  await AsyncStorage.removeItem(AUTH_TOKEN_KEY)
}

export default apiClient
