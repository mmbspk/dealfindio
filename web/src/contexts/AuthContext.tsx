import React, { createContext, useContext, useEffect, useState } from 'react'
import {
  User,
  onAuthStateChanged,
  signInWithEmailAndPassword,
  createUserWithEmailAndPassword,
  signOut as firebaseSignOut,
  signInWithPopup,
  GoogleAuthProvider,
  AuthError,
} from 'firebase/auth'
import { auth } from '../lib/firebase'

interface AuthContextValue {
  user: User | null
  loading: boolean
  isAdmin: boolean
  signIn: (email: string, password: string) => Promise<void>
  signUp: (email: string, password: string) => Promise<void>
  signOut: () => Promise<void>
  signInWithGoogle: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

const googleProvider = new GoogleAuthProvider()

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [isAdmin, setIsAdmin] = useState(false)

  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, async (firebaseUser) => {
      setUser(firebaseUser)
      if (firebaseUser) {
        let token: string
        try {
          token = await firebaseUser.getIdToken()
          localStorage.setItem('auth_token', token)
        } catch (e) {
          console.error('[AuthContext] getIdToken failed:', e)
          setLoading(false)
          return
        }
        // Fetch admin status from backend
        try {
          const res = await fetch('/api/v1/users/me', {
            headers: { Authorization: `Bearer ${token}` },
          })
          if (res.ok) {
            const data = await res.json()
            setIsAdmin(data.is_admin === true)
          } else {
            const body = await res.text()
            console.warn('[AuthContext] /api/v1/users/me returned', res.status, body)
            setIsAdmin(false)
          }
        } catch (e) {
          console.error('[AuthContext] fetch /api/v1/users/me failed:', e)
          setIsAdmin(false)
        }
      } else {
        localStorage.removeItem('auth_token')
        setIsAdmin(false)
      }
      setLoading(false)
    })
    return unsubscribe
  }, [])

  async function signIn(email: string, password: string) {
    await signInWithEmailAndPassword(auth, email, password)
  }

  async function signUp(email: string, password: string) {
    await createUserWithEmailAndPassword(auth, email, password)
  }

  async function signOut() {
    await firebaseSignOut(auth)
  }

  async function signInWithGoogle() {
    await signInWithPopup(auth, googleProvider)
  }

  return (
    <AuthContext.Provider value={{ user, loading, isAdmin, signIn, signUp, signOut, signInWithGoogle }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}

export function firebaseErrorMessage(err: unknown): string {
  const code = (err as AuthError)?.code ?? ''
  switch (code) {
    case 'auth/user-not-found':
    case 'auth/wrong-password':
    case 'auth/invalid-credential':
      return 'Incorrect email or password.'
    case 'auth/email-already-in-use':
      return 'An account with this email already exists.'
    case 'auth/weak-password':
      return 'Password must be at least 6 characters.'
    case 'auth/invalid-email':
      return 'Please enter a valid email address.'
    case 'auth/too-many-requests':
      return 'Too many attempts. Please try again later.'
    case 'auth/popup-closed-by-user':
      return 'Sign-in cancelled.'
    default:
      return 'Something went wrong. Please try again.'
  }
}
