/**
 * Firebase Auth via REST API.
 *
 * The Firebase JS SDK v10 requires the Old Architecture native bridge, which
 * Expo Go does not provide (it always runs New Architecture). This module
 * implements sign-in/sign-up/token-refresh using Firebase's Identity Toolkit
 * REST API directly — no native modules, no bridge, works everywhere.
 *
 * Docs: https://firebase.google.com/docs/reference/rest/auth
 */

import AsyncStorage from '@react-native-async-storage/async-storage'
import Constants from 'expo-constants'

const extra = Constants.expoConfig?.extra as Record<string, string> | undefined

export const FIREBASE_API_KEY =
  extra?.firebaseApiKey ?? 'REPLACE_WITH_YOUR_FIREBASE_WEB_API_KEY'

const BASE = 'https://identitytoolkit.googleapis.com/v1/accounts'

const STORAGE_ID_TOKEN = 'firebase_id_token'
const STORAGE_REFRESH_TOKEN = 'firebase_refresh_token'
const STORAGE_EMAIL = 'firebase_email'

export interface FirebaseUser {
  email: string
  localId: string
  idToken: string
  refreshToken: string
  expiresIn: string
}

type AuthEndpoint = 'signInWithPassword' | 'signUp'

async function callAuth(endpoint: AuthEndpoint, email: string, password: string): Promise<FirebaseUser> {
  const res = await fetch(`${BASE}:${endpoint}?key=${FIREBASE_API_KEY}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password, returnSecureToken: true }),
  })
  const data = await res.json() as Record<string, unknown>
  if (!res.ok) {
    const errObj = data.error as Record<string, unknown> | undefined
    const msg = (errObj?.message as string) ?? JSON.stringify(data)
    throw new Error(msg)
  }
  return data as unknown as FirebaseUser
}

export async function signInWithEmail(email: string, password: string): Promise<FirebaseUser> {
  const user = await callAuth('signInWithPassword', email, password)
  await persistUser(user)
  return user
}

export async function registerWithEmail(email: string, password: string): Promise<FirebaseUser> {
  const user = await callAuth('signUp', email, password)
  await persistUser(user)
  return user
}

export async function signOut(): Promise<void> {
  await AsyncStorage.multiRemove([STORAGE_ID_TOKEN, STORAGE_REFRESH_TOKEN, STORAGE_EMAIL])
}

export async function getStoredIdToken(): Promise<string | null> {
  return AsyncStorage.getItem(STORAGE_ID_TOKEN)
}

export async function getStoredEmail(): Promise<string | null> {
  return AsyncStorage.getItem(STORAGE_EMAIL)
}

/** Refresh the ID token using the stored refresh token. Returns null if not signed in. */
export async function refreshIdToken(): Promise<string | null> {
  const refreshToken = await AsyncStorage.getItem(STORAGE_REFRESH_TOKEN)
  if (!refreshToken) return null

  const res = await fetch(
    `https://securetoken.googleapis.com/v1/token?key=${FIREBASE_API_KEY}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: `grant_type=refresh_token&refresh_token=${encodeURIComponent(refreshToken)}`,
    }
  )
  const data = await res.json() as Record<string, string>
  if (!res.ok) {
    await signOut()
    return null
  }
  const idToken = data.id_token
  await AsyncStorage.setItem(STORAGE_ID_TOKEN, idToken)
  return idToken
}

async function persistUser(user: FirebaseUser): Promise<void> {
  await AsyncStorage.multiSet([
    [STORAGE_ID_TOKEN, user.idToken],
    [STORAGE_REFRESH_TOKEN, user.refreshToken],
    [STORAGE_EMAIL, user.email],
  ])
}
