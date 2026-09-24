/**
 * Notification preferences screen — accessible from Profile tab.
 * Configures push/email channels, quiet hours, and timezone.
 */

import { useEffect, useState } from 'react'
import {
  ActivityIndicator,
  Alert,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TouchableOpacity,
  View,
} from 'react-native'
import { Picker } from '@react-native-picker/picker'
import apiClient from '@/api/client'

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
  { value: 'Australia/Sydney', label: 'Sydney / Melbourne (AEST)' },
  { value: 'Australia/Brisbane', label: 'Brisbane (AEST)' },
  { value: 'Australia/Perth', label: 'Perth (AWST)' },
  { value: 'Australia/Adelaide', label: 'Adelaide (ACST)' },
  { value: 'Australia/Darwin', label: 'Darwin (ACST)' },
  { value: 'Australia/Hobart', label: 'Hobart (AEST)' },
]

const HOUR_OPTIONS: { value: number | null; label: string }[] = [
  { value: null, label: 'Disabled' },
  ...Array.from({ length: 24 }, (_, i) => {
    const h = i % 12 === 0 ? 12 : i % 12
    const ampm = i < 12 ? 'am' : 'pm'
    return { value: i, label: `${h}:00 ${ampm}` }
  }),
]

function SectionHeader({ title }: { title: string }) {
  return (
    <View style={styles.sectionHeader}>
      <Text style={styles.sectionTitle}>{title}</Text>
    </View>
  )
}

function PrefRow({ label, sub, value, onChange }: { label: string; sub?: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <View style={styles.row}>
      <View style={styles.rowText}>
        <Text style={styles.rowLabel}>{label}</Text>
        {sub ? <Text style={styles.rowSub}>{sub}</Text> : null}
      </View>
      <Switch
        value={value}
        onValueChange={onChange}
        trackColor={{ false: '#e5e7eb', true: '#6366f1' }}
        thumbColor="#fff"
        ios_backgroundColor="#e5e7eb"
      />
    </View>
  )
}

export default function NotificationPreferencesScreen() {
  const [prefs, setPrefs] = useState<Prefs | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    apiClient.get('/api/v1/notifications/preferences')
      .then((r) => setPrefs(r.data))
      .catch(() => Alert.alert('Error', 'Failed to load preferences.'))
      .finally(() => setLoading(false))
  }, [])

  function set<K extends keyof Prefs>(key: K, value: Prefs[K]) {
    setPrefs((prev) => prev ? { ...prev, [key]: value } : prev)
  }

  async function save() {
    if (!prefs) return
    setSaving(true)
    try {
      const resp = await apiClient.put('/api/v1/notifications/preferences', prefs)
      setPrefs(resp.data)
      Alert.alert('Saved', 'Notification preferences updated.')
    } catch {
      Alert.alert('Error', 'Failed to save preferences.')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator size="large" color="#4f46e5" />
      </View>
    )
  }

  if (!prefs) return null

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <SectionHeader title="Push Notifications" />
      <View style={styles.card}>
        <PrefRow label="Price match" sub="When a product hits your target price" value={prefs.notif_push_price_match} onChange={(v) => set('notif_push_price_match', v)} />
        <PrefRow label="Discount alert" sub="When a product reaches your target % off" value={prefs.notif_push_pct_off} onChange={(v) => set('notif_push_pct_off', v)} />
        <PrefRow label="Flash sale (40%+ off)" sub="Big deals on watched products" value={prefs.notif_push_flash_sale} onChange={(v) => set('notif_push_flash_sale', v)} />
        <PrefRow label="Predicted price drop" sub="ML-predicted upcoming deals" value={prefs.notif_push_prediction} onChange={(v) => set('notif_push_prediction', v)} />
      </View>

      <SectionHeader title="Email Notifications" />
      <View style={styles.card}>
        <PrefRow label="Price match" value={prefs.notif_email_price_match} onChange={(v) => set('notif_email_price_match', v)} />
        <PrefRow label="Discount alert" value={prefs.notif_email_pct_off} onChange={(v) => set('notif_email_pct_off', v)} />
        <PrefRow label="Predicted price drop" value={prefs.notif_email_prediction} onChange={(v) => set('notif_email_prediction', v)} />
        <PrefRow label="Weekly deals digest" sub="Top deals from your watchlist every Monday" value={prefs.notif_email_weekly_digest} onChange={(v) => set('notif_email_weekly_digest', v)} />
      </View>

      <SectionHeader title="Quiet Hours" />
      <View style={styles.card}>
        <Text style={styles.quietNote}>Push notifications are silenced during these hours.</Text>
        <View style={styles.pickerRow}>
          <View style={styles.pickerWrap}>
            <Text style={styles.pickerLabel}>From</Text>
            <View style={styles.pickerBox}>
              <Picker
                selectedValue={prefs.notif_quiet_hours_start ?? null}
                onValueChange={(v) => set('notif_quiet_hours_start', v)}
                style={styles.picker}
              >
                {HOUR_OPTIONS.map((o) => (
                  <Picker.Item key={String(o.value)} label={o.label} value={o.value} />
                ))}
              </Picker>
            </View>
          </View>
          <View style={styles.pickerWrap}>
            <Text style={styles.pickerLabel}>Until</Text>
            <View style={styles.pickerBox}>
              <Picker
                selectedValue={prefs.notif_quiet_hours_end ?? null}
                onValueChange={(v) => set('notif_quiet_hours_end', v)}
                style={styles.picker}
              >
                {HOUR_OPTIONS.map((o) => (
                  <Picker.Item key={String(o.value)} label={o.label} value={o.value} />
                ))}
              </Picker>
            </View>
          </View>
        </View>
        <Text style={[styles.pickerLabel, { marginTop: 16, marginBottom: 4 }]}>Timezone</Text>
        <View style={styles.pickerBox}>
          <Picker
            selectedValue={prefs.notif_timezone}
            onValueChange={(v) => set('notif_timezone', v)}
            style={styles.picker}
          >
            {AU_TIMEZONES.map((tz) => (
              <Picker.Item key={tz.value} label={tz.label} value={tz.value} />
            ))}
          </Picker>
        </View>
      </View>

      <TouchableOpacity
        style={[styles.saveBtn, saving && styles.saveBtnDisabled]}
        onPress={save}
        disabled={saving}
        activeOpacity={0.8}
      >
        {saving ? (
          <ActivityIndicator size="small" color="#fff" />
        ) : (
          <Text style={styles.saveBtnText}>Save Preferences</Text>
        )}
      </TouchableOpacity>
    </ScrollView>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f9fafb' },
  content: { padding: 16, paddingBottom: 32 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  sectionHeader: { paddingTop: 16, paddingBottom: 6, paddingHorizontal: 4 },
  sectionTitle: { fontSize: 11, fontWeight: '700', color: '#9ca3af', textTransform: 'uppercase', letterSpacing: 0.5 },
  card: { backgroundColor: '#fff', borderRadius: 12, borderWidth: 1, borderColor: '#f3f4f6', overflow: 'hidden', marginBottom: 4 },
  row: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingVertical: 13, borderBottomWidth: 1, borderBottomColor: '#f9fafb' },
  rowText: { flex: 1, marginRight: 12 },
  rowLabel: { fontSize: 15, color: '#111827', fontWeight: '500' },
  rowSub: { fontSize: 12, color: '#9ca3af', marginTop: 1 },
  quietNote: { fontSize: 13, color: '#6b7280', paddingHorizontal: 16, paddingTop: 12, paddingBottom: 4 },
  pickerRow: { flexDirection: 'row', gap: 12, paddingHorizontal: 16, paddingBottom: 4 },
  pickerWrap: { flex: 1 },
  pickerLabel: { fontSize: 12, fontWeight: '600', color: '#6b7280', marginBottom: 4, paddingHorizontal: 16 },
  pickerBox: { borderWidth: 1, borderColor: '#e5e7eb', borderRadius: 8, overflow: 'hidden', marginHorizontal: 4 },
  picker: { height: 44 },
  saveBtn: {
    marginTop: 24, backgroundColor: '#4f46e5', borderRadius: 12,
    paddingVertical: 14, alignItems: 'center',
  },
  saveBtnDisabled: { opacity: 0.6 },
  saveBtnText: { color: '#fff', fontSize: 16, fontWeight: '700' },
})
