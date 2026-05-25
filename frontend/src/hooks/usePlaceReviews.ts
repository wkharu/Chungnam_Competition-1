import { useState, useCallback, useRef } from 'react'
import type { PlaceReview } from '@/types'
import { readFetchErrorMessage } from '@/lib/apiErrorMessage'

interface ReviewData {
  rating: number
  review_count: number
  reviews: PlaceReview[]
  /** API가 내려주는 목표 노출 개수(기본 5) */
  reviews_shown?: number
  /** 코스 기준 좌표와 Google 매칭 장소 간 거리(m), 없으면 미표시 */
  place_match_distance_m?: number | null
  website: string
  google_maps: string
  place_id?: string
  place_name?: string
  place_address?: string
  place_lat?: number | null
  place_lng?: number | null
  open_now: boolean | null
  places_status?: 'ok' | 'missing_key' | 'request_failed' | 'no_match' | 'match_too_far'
  places_status_message?: string
  photo_url?: string | null
}

export function usePlaceReviews() {
  const [data, setData] = useState<ReviewData | null>(null)
  const [loading, setLoading] = useState(false)
  const [fetched, setFetched] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const lastArgsRef = useRef<{ name: string; lat: number; lng: number; address: string } | null>(null)

  const fetch = useCallback(async (name: string, lat: number, lng: number, address = '', topReviews = 3) => {
    lastArgsRef.current = { name, lat, lng, address }
    setLoading(true)
    setError(null)
    try {
      const res = await window.fetch(
        `/api/place-reviews?name=${encodeURIComponent(name)}&lat=${lat}&lng=${lng}&address=${encodeURIComponent(address)}&top_reviews=${Math.max(1, Math.min(5, topReviews))}`
      )
      if (!res.ok) {
        setData(null)
        setError(await readFetchErrorMessage(res, `불러오기 실패 (${res.status})`))
        return
      }
      const json = (await res.json()) as ReviewData
      setData(
        json && typeof json === 'object' && Array.isArray(json.reviews) ? json : null,
      )
    } catch {
      setData(null)
      setError('네트워크 오류로 리뷰를 불러오지 못했어요')
    } finally {
      setLoading(false)
      setFetched(true)
    }
  }, [])

  const reset = useCallback(() => {
    setData(null)
    setFetched(false)
    setError(null)
    lastArgsRef.current = null
  }, [])

  const retry = useCallback(() => {
    const l = lastArgsRef.current
    if (l) void fetch(l.name, l.lat, l.lng, l.address)
  }, [fetch])

  return { data, loading, fetched, error, fetch, reset, retry }
}
