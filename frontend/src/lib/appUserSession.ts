const ACTIVE_PARTICIPANT_KEY = 'chungnam_active_participant_id_v1'

export function normalizeParticipantId(value: string): string {
  const cleaned = value
    .trim()
    .replace(/\s+/g, '-')
    .replace(/[^0-9A-Za-z가-힣_-]/g, '')
    .slice(0, 24)
  return cleaned || createParticipantId()
}

export function createParticipantId(): string {
  return `guest-${Math.floor(1000 + Math.random() * 9000)}`
}

export function getActiveParticipantId(): string | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.sessionStorage.getItem(ACTIVE_PARTICIPANT_KEY)
    return raw ? normalizeParticipantId(raw) : null
  } catch {
    return null
  }
}

export function setActiveParticipantId(value: string): string {
  const id = normalizeParticipantId(value)
  if (typeof window !== 'undefined') {
    try {
      window.sessionStorage.setItem(ACTIVE_PARTICIPANT_KEY, id)
      window.dispatchEvent(new Event('chungnam-participant-changed'))
    } catch {
      /* ignore storage errors */
    }
  }
  return id
}

export function scopedStorageKey(base: string): string {
  const id = getActiveParticipantId()
  return id ? `${base}:${id}` : `${base}:unassigned`
}
