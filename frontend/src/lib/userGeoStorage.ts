import { scopedStorageKey } from '@/lib/appUserSession'

const STORAGE_KEY = 'chungnam_user_geo_v1'
const MAX_AGE_MS = 1000 * 60 * 30 // 30분마다 다시 측정 가능

export interface StoredUserGeo {
  lat: number
  lng: number
  savedAt: number
}

export function readStoredUserGeo(): { lat: number; lng: number } | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.sessionStorage.getItem(scopedStorageKey(STORAGE_KEY))
    if (!raw) return null
    const j = JSON.parse(raw) as StoredUserGeo
    if (
      typeof j.lat !== 'number' ||
      typeof j.lng !== 'number' ||
      typeof j.savedAt !== 'number' ||
      Number.isNaN(j.lat) ||
      Number.isNaN(j.lng)
    ) {
      return null
    }
    if (Date.now() - j.savedAt > MAX_AGE_MS) return null
    return { lat: j.lat, lng: j.lng }
  } catch {
    return null
  }
}

export function writeStoredUserGeo(lat: number, lng: number): void {
  if (typeof window === 'undefined') return
  try {
    const payload: StoredUserGeo = { lat, lng, savedAt: Date.now() }
    window.sessionStorage.setItem(scopedStorageKey(STORAGE_KEY), JSON.stringify(payload))
    window.dispatchEvent(new Event('chungnam-user-geo-changed'))
  } catch {
    /* ignore quota */
  }
}
