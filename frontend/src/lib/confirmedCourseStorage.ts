import type { RecommendResponse } from '@/types'
import { getActiveParticipantId, scopedStorageKey } from '@/lib/appUserSession'

const STORAGE_KEY = 'chungnam_confirmed_course_v1'

/** 브라우저에 저장되는 “확정 코스” (미리보기·세션 state와 분리) */
export interface ConfirmedCourseState {
  version: 1
  savedAt: string
  /** 결과/재구성 화면 복원용 쿼리 (지역·일정·동행 등) */
  resultQueryString: string
  /**
   * 선택한 코스 식별자. 1순위(top_course)면 null.
   * /result/course 의 altId와 동일 규칙(null·main → 메인 코스).
   */
  committedCourseAltId: string | null
  recommendPayload: RecommendResponse
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function parseStored(raw: string | null): ConfirmedCourseState | null {
  if (!raw) return null
  try {
    const j = JSON.parse(raw) as unknown
    if (!isRecord(j)) return null
    if (j.version !== 1) return null
    if (typeof j.savedAt !== 'string') return null
    if (typeof j.resultQueryString !== 'string') return null
    if (j.committedCourseAltId !== null && typeof j.committedCourseAltId !== 'string') return null
    if (!isRecord(j.recommendPayload)) return null
    return j as unknown as ConfirmedCourseState
  } catch {
    return null
  }
}

export function loadConfirmedCourse(): ConfirmedCourseState | null {
  if (typeof window === 'undefined') return null
  if (!getActiveParticipantId()) return null
  return parseStored(window.localStorage.getItem(scopedStorageKey(STORAGE_KEY)))
}

export function saveConfirmedCourse(state: Omit<ConfirmedCourseState, 'version' | 'savedAt'>): boolean {
  if (typeof window === 'undefined') return false
  if (!getActiveParticipantId()) return false
  const full: ConfirmedCourseState = {
    version: 1,
    savedAt: new Date().toISOString(),
    ...state,
  }
  try {
    window.localStorage.setItem(scopedStorageKey(STORAGE_KEY), JSON.stringify(full))
    window.dispatchEvent(new Event('chungnam-confirmed-course-changed'))
    return true
  } catch {
    return false
  }
}

export function clearConfirmedCourse(): void {
  if (typeof window === 'undefined') return
  if (!getActiveParticipantId()) return
  window.localStorage.removeItem(scopedStorageKey(STORAGE_KEY))
  window.dispatchEvent(new Event('chungnam-confirmed-course-changed'))
}

/** URL altId → 저장용 키 (메인 코스는 null) */
export function altIdToCommittedKey(altId: string | null | undefined): string | null {
  if (!altId || altId === 'main') return null
  return altId
}
