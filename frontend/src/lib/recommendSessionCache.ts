/**
 * 추천 API 응답이 커서 history.state에 넣기 어려울 때를 대비해
 * /result 진입 시 복원할 수 있도록 sessionStorage에 보관합니다.
 */
import type { RecommendResponse } from '@/types'
import { scopedStorageKey } from '@/lib/appUserSession'

const K_CANON = 'passquest:recommend:canon_qs'
const K_JSON = 'passquest:recommend:payload_json'

/** mock·altId 제외 후 정렬 (같은 추천 세션은 상세 화면에서 alt만 바뀔 수 있음) */
export function canonicalTripSearchParams(sp: URLSearchParams): string {
  const entries = [...sp.entries()].filter(([k]) => k !== 'mock' && k !== 'altId')
  entries.sort((a, b) => a[0].localeCompare(b[0]))
  return new URLSearchParams(entries).toString()
}

export function canonicalQueryString(qs: string): string {
  const q = qs.trim().startsWith('?') ? qs.trim().slice(1) : qs.trim()
  return canonicalTripSearchParams(new URLSearchParams(q))
}

export function isCurrentRecommendPayload(data: RecommendResponse | null | undefined): data is RecommendResponse {
  if (!data || typeof data !== 'object') return false
  const counts = data.main_scoring_model?.destination_source_counts
  const tourApiCount =
    counts && typeof counts === 'object' && !Array.isArray(counts)
      ? Number((counts as Record<string, unknown>).tourapi || 0)
      : 0
  const hasStepImage = Boolean(data.top_course?.steps?.some(s => String(s.image || '').trim()))
  return tourApiCount > 0 && hasStepImage
}

export function saveRecommendPayloadForResult(
  resultQueryString: string,
  data: RecommendResponse,
): void {
  try {
    const canon = canonicalQueryString(resultQueryString)
    sessionStorage.setItem(scopedStorageKey(K_CANON), canon)
    sessionStorage.setItem(scopedStorageKey(K_JSON), JSON.stringify(data))
  } catch {
    /* 용량 초과·비공개 모드 등 — 이후 location.state·재요청에 의존 */
  }
}

export function loadRecommendPayloadForResult(sp: URLSearchParams): RecommendResponse | null {
  if (sp.get('mock') === '1') return null
  try {
    const cur = canonicalTripSearchParams(sp)
    const saved = sessionStorage.getItem(scopedStorageKey(K_CANON))
    if (!saved || saved !== cur) return null
    const raw = sessionStorage.getItem(scopedStorageKey(K_JSON))
    if (!raw) return null
    const parsed = JSON.parse(raw) as RecommendResponse
    return isCurrentRecommendPayload(parsed) ? parsed : null
  } catch {
    return null
  }
}
