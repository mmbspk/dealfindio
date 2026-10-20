/**
 * Profile tab: sign-in form (unauthenticated) or user profile card (authenticated).
 */

import { useEffect, useState } from 'react'
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native'
import { router } from 'expo-router'
import {
  signInWithEmail,
  registerWithEmail,
  signOut,
  getStoredIdToken,
  getStoredEmail,
  FIREBASE_API_KEY,
} from '@/firebaseAuth'
import apiClient, { setAuthToken, clearAuthToken } from '@/api/client'
import { useStore } from '@/store/store'
import type { UserProfile } from '@/types'

const TIER_COLORS: Record<string, string> = {
  bronze: '#b45309',
  silver: '#5d7469',
  gold: '#f59e0b',
}

export default function ProfileScreen() {
  const { user, setUser } = useStore()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [isRegistering, setIsRegistering] = useState(false)
  const [authLoading, setAuthLoading] = useState(false)
  const [authError, setAuthError] = useState<string | null>(null)
  const [profileLoading, setProfileLoading] = useState(true)
  const [signedInEmail, setSignedInEmail] = useState<string | null>(null)

  // On mount: check for a stored token from a previous session
  useEffect(() => {
    void restoreSession()
    // restoreSession is defined in the same scope and never changes; adding it
    // to deps would require useCallback wrapping with no runtime benefit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function restoreSession() {
    const token = await getStoredIdToken()
    const storedEmail = await getStoredEmail()
    if (token) {
      await setAuthToken(token)
      setSignedInEmail(storedEmail)
      await loadProfile()
    } else {
      setProfileLoading(false)
    }
  }

  async function loadProfile() {
    setProfileLoading(true)
    try {
      const resp = await apiClient.get<UserProfile>('/api/v1/users/me')
      setUser(resp.data)
    } catch {
      setUser(null)
    } finally {
      setProfileLoading(false)
    }
  }

  async function handleSignIn() {
    const trimmed = email.trim()
    if (!trimmed || !password) return

    if (FIREBASE_API_KEY === 'REPLACE_WITH_YOUR_FIREBASE_WEB_API_KEY') {
      setAuthError(
        'Firebase not configured.\nAdd your API key to app.json → extra.firebaseApiKey.'
      )
      return
    }

    setAuthLoading(true)
    setAuthError(null)
    try {
      const fbUser = isRegistering
        ? await registerWithEmail(trimmed, password)
        : await signInWithEmail(trimmed, password)

      await setAuthToken(fbUser.idToken)
      setSignedInEmail(fbUser.email)
      await loadProfile()
    } catch (e: unknown) {
      const raw = (e as { message?: string })?.message ?? 'Sign-in failed.'
      setAuthError(friendlyError(raw))
    } finally {
      setAuthLoading(false)
    }
  }

  async function handleSignOut() {
    await signOut()
    await clearAuthToken()
    setUser(null)
    setSignedInEmail(null)
  }

  if (profileLoading) {
    return <ActivityIndicator size="large" color="#164b40" style={styles.spinner} />
  }

  // ── Unauthenticated ────────────────────────────────────────────────────────
  if (!signedInEmail) {
    return (
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView
          contentContainerStyle={styles.authContainer}
          keyboardShouldPersistTaps="handled"
        >
          <Text style={styles.authTitle}>dealfindio</Text>
          <Text style={styles.authSubtitle}>
            {isRegistering ? 'Create an account' : 'Sign in to track deals'}
          </Text>

          <TextInput
            style={styles.input}
            placeholder="Email"
            placeholderTextColor="#71877b"
            keyboardType="email-address"
            autoCapitalize="none"
            autoCorrect={false}
            value={email}
            onChangeText={setEmail}
          />
          <TextInput
            style={styles.input}
            placeholder="Password"
            placeholderTextColor="#71877b"
            secureTextEntry
            value={password}
            onChangeText={setPassword}
          />

          {authError ? <Text style={styles.authError}>{authError}</Text> : null}

          <Pressable
            style={[styles.primaryBtn, authLoading && styles.primaryBtnDisabled]}
            onPress={handleSignIn}
            disabled={authLoading}
          >
            {authLoading ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={styles.primaryBtnText}>
                {isRegistering ? 'Create account' : 'Sign in'}
              </Text>
            )}
          </Pressable>

          <Pressable
            onPress={() => {
              setIsRegistering(!isRegistering)
              setAuthError(null)
            }}
          >
            <Text style={styles.toggleText}>
              {isRegistering
                ? 'Already have an account? Sign in'
                : "Don't have an account? Register"}
            </Text>
          </Pressable>
        </ScrollView>
      </KeyboardAvoidingView>
    )
  }

  // ── Authenticated ──────────────────────────────────────────────────────────
  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.scrollContent}>
      {user ? (
        <View style={styles.profileCard}>
          <Text style={styles.emailText}>{user.email}</Text>
          <View
            style={[
              styles.tierBadge,
              { backgroundColor: TIER_COLORS[user.tier] ?? '#5d7469' },
            ]}
          >
            <Text style={styles.tierText}>{user.tier.toUpperCase()}</Text>
          </View>
          <View style={styles.statsRow}>
            <View style={styles.stat}>
              <Text style={styles.statValue}>{user.contribution_count}</Text>
              <Text style={styles.statLabel}>Reports</Text>
            </View>
            <View style={styles.stat}>
              <Text style={styles.statValue}>
                {user.reputation_score.toFixed(1)}
              </Text>
              <Text style={styles.statLabel}>Reputation</Text>
            </View>
          </View>
        </View>
      ) : (
        <View style={styles.profileCard}>
          <Text style={styles.emailText}>{signedInEmail}</Text>
          <Text style={styles.newUserNote}>
            Profile not found on server — make sure the backend is reachable.
          </Text>
        </View>
      )}

      <View style={styles.actions}>
        <Pressable style={styles.action} onPress={() => router.push('/scan')}>
          <Text style={styles.actionText}>📷  Scan Barcode</Text>
        </Pressable>
        <Pressable
          style={styles.action}
          onPress={() => router.push('/upload-receipt')}
        >
          <Text style={styles.actionText}>🧾  Upload Receipt</Text>
        </Pressable>
        <Pressable
          style={[styles.action, styles.signOutBtn]}
          onPress={handleSignOut}
        >
          <Text style={styles.signOutText}>Sign out</Text>
        </Pressable>
      </View>
    </ScrollView>
  )
}

function friendlyError(msg: string): string {
  const m = msg.toUpperCase()
  if (m.includes('EMAIL_NOT_FOUND') || m.includes('INVALID_PASSWORD') || m.includes('INVALID_LOGIN_CREDENTIALS'))
    return 'Incorrect email or password.'
  if (m.includes('EMAIL_EXISTS'))
    return 'That email is already registered. Sign in instead.'
  if (m.includes('WEAK_PASSWORD'))
    return 'Password must be at least 6 characters.'
  if (m.includes('INVALID_EMAIL'))
    return 'Please enter a valid email address.'
  if (m.includes('TOO_MANY_ATTEMPTS') || m.includes('BLOCKED'))
    return 'Too many attempts. Try again later.'
  if (m.includes('NETWORK') || m.includes('FETCH'))
    return 'Network error. Check your connection.'
  return msg
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: '#f5f7f2' },
  spinner: { flex: 1, backgroundColor: '#f5f7f2' },
  container: { flex: 1, backgroundColor: '#f5f7f2' },
  scrollContent: { padding: 16 },

  authContainer: {
    flexGrow: 1,
    justifyContent: 'center',
    padding: 24,
    gap: 12,
  },
  authTitle: {
    fontSize: 28,
    fontWeight: '700',
    color: '#164b40',
    textAlign: 'center',
    marginBottom: 4,
  },
  authSubtitle: {
    fontSize: 15,
    color: '#5d7469',
    textAlign: 'center',
    marginBottom: 16,
  },
  input: {
    backgroundColor: '#fff',
    borderWidth: 1,
    borderColor: '#d1d5db',
    borderRadius: 12,
    padding: 14,
    fontSize: 16,
    color: '#122923',
  },
  authError: {
    color: '#ef4444',
    fontSize: 14,
    textAlign: 'center',
  },
  primaryBtn: {
    backgroundColor: '#164b40',
    borderRadius: 12,
    padding: 16,
    alignItems: 'center',
    marginTop: 4,
  },
  primaryBtnDisabled: { opacity: 0.6 },
  primaryBtnText: { color: '#fff', fontSize: 16, fontWeight: '600' },
  toggleText: {
    color: '#164b40',
    fontSize: 14,
    textAlign: 'center',
    marginTop: 4,
  },

  profileCard: {
    backgroundColor: '#fff',
    borderRadius: 12,
    padding: 20,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: '#dce5dd',
  },
  emailText: { fontSize: 18, fontWeight: '600', color: '#122923' },
  newUserNote: {
    color: '#5d7469',
    marginTop: 8,
    fontSize: 13,
    textAlign: 'center',
  },
  tierBadge: {
    marginTop: 10,
    paddingHorizontal: 14,
    paddingVertical: 4,
    borderRadius: 999,
  },
  tierText: { color: '#fff', fontWeight: '700', fontSize: 12 },
  statsRow: { flexDirection: 'row', marginTop: 20, gap: 40 },
  stat: { alignItems: 'center' },
  statValue: { fontSize: 24, fontWeight: '700', color: '#164b40' },
  statLabel: { fontSize: 12, color: '#5d7469', marginTop: 2 },

  actions: { marginTop: 24, gap: 10 },
  action: {
    backgroundColor: '#fff',
    borderRadius: 12,
    padding: 16,
    borderWidth: 1,
    borderColor: '#dce5dd',
  },
  actionText: { fontSize: 16, color: '#122923', fontWeight: '500' },
  signOutBtn: { borderColor: '#fca5a5' },
  signOutText: {
    fontSize: 16,
    color: '#ef4444',
    fontWeight: '500',
    textAlign: 'center',
  },
})
