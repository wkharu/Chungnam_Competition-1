import { useEffect, useMemo, useState } from 'react'
import { X, MapPin, ExternalLink } from 'lucide-react'
import type { ConsumerStep } from '@/lib/consumerCourseTypes'
import { appImageSrc, COURSE_IMAGE_FALLBACK } from '@/lib/courseImageFallback'
import { usePlaceReviews } from '@/hooks/usePlaceReviews'
import { Badge } from '@/components/consumer/Badge'
import type { RecommendResponse } from '@/types'
import { resolveReviewLookupCoords, type ReviewLookupCoordsSource } from '@/lib/recommendGeo'

function coordsHintLine(source: ReviewLookupCoordsSource): string | null {
  if (source === 'payload') return '추천·코스 정보로 검색 위치를 맞췄어요'
  if (source === 'fallback') return '지역 기준 위치로 검색했어요. 현장과 다를 수 있어요'
  return null
}

function openStatusLabel(open: boolean | null | undefined): string {
  if (open === true) return '운영 중'
  if (open === false) return '운영 확인 필요'
  return '정보 없음'
}

function googleMapsSearchUrl(name: string, address?: string, placeId?: string): string {
  const query = [name, address].filter(Boolean).join(' ')
  const params = new URLSearchParams({ api: '1', query: query || name })
  if (placeId) params.set('query_place_id', placeId)
  return `https://www.google.com/maps/search/?${params.toString()}`
}

export function PlaceDetailModal({
  step,
  open,
  onClose,
  recommendData,
}: {
  step: ConsumerStep | null
  open: boolean
  onClose: () => void
  /** 스텝에 좌표가 없을 때 리뷰 검색·지도 링크 보강용 */
  recommendData?: RecommendResponse | null
}) {
  const { data, loading, error, fetch, reset, retry } = usePlaceReviews()
  const [imgBroken, setImgBroken] = useState(false)
  const [expanded, setExpanded] = useState<Record<number, boolean>>({})

  const lookup = useMemo(() => {
    if (!step) return null
    const c = resolveReviewLookupCoords(recommendData, step)
    return { ...c, hint: coordsHintLine(c.source) }
  }, [step, recommendData])

  useEffect(() => {
    if (!open || !step || !lookup) return
    setImgBroken(false)
    reset()
    void fetch(step.name, lookup.lat, lookup.lng, step.address)
  }, [open, step?.id, step?.name, step?.address, lookup?.lat, lookup?.lng, lookup?.source, fetch, reset])

  if (!open || !step || !lookup) return null

  const hero = imgBroken ? COURSE_IMAGE_FALLBACK : appImageSrc(data?.photo_url || step.image)
  const rating = data?.rating ?? step.rating ?? null
  const reviewCount = data?.review_count ?? step.reviewCount ?? 0
  const reviews = data?.reviews ?? []
  const placesStatus = data?.places_status
  const placesStatusMessage = data?.places_status_message?.trim()
  const tags =
    step.reviewTags.length > 0
      ? step.reviewTags
      : ['가볍게 둘러보기', '현장 확인 추천']

  const matchedName = data?.place_name?.trim() || step.name
  const matchedAddress = data?.place_address?.trim() || step.address
  const mapsUrl =
    data?.google_maps?.trim() ||
    googleMapsSearchUrl(matchedName, matchedAddress, data?.place_id?.trim())

  return (
    <div
      className="fixed inset-0 z-[100] flex items-end sm:items-center justify-center bg-black/45 backdrop-blur-[2px]"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div
        className="bg-white/96 backdrop-blur-2xl border border-slate-200/80 w-full max-w-lg max-h-[92dvh] rounded-t-lg sm:rounded-lg overflow-hidden flex flex-col shadow-[0_-24px_64px_-28px_rgba(15,23,42,0.22)] sm:shadow-2xl"
        onClick={e => e.stopPropagation()}
      >
        <div className="relative aspect-[16/10] w-full bg-stone-200 shrink-0">
          <img src={hero} alt="" className="w-full h-full object-cover" onError={() => setImgBroken(true)} />
          <button
            type="button"
            onClick={onClose}
            className="absolute top-3 right-3 h-10 w-10 rounded-lg bg-white/95 shadow flex items-center justify-center"
            aria-label="닫기"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-4 pt-4 pb-8">
          <p className="text-[13px] font-bold text-teal-700">{step.role}</p>
          <h2 className="text-[22px] font-extrabold text-slate-950 leading-tight mt-1">{step.name}</h2>
          {data?.place_name?.trim() && data.place_name.trim() !== step.name ? (
            <p className="text-[12px] text-stone-500 font-semibold mt-1">
              Google Maps 매칭: {data.place_name.trim()}
            </p>
          ) : null}

          <div className="flex flex-wrap gap-2 mt-3">
            {typeof rating === 'number' && rating > 0 ? (
              <Badge className="bg-amber-50 text-amber-950 ring-amber-200">★ {rating.toFixed(1)}</Badge>
            ) : null}
            {reviewCount > 0 ? (
              <Badge className="text-stone-700">리뷰 {reviewCount}</Badge>
            ) : null}
            <Badge>{openStatusLabel(data?.open_now)}</Badge>
            {step.tourPassCandidate ? (
              <Badge className="bg-orange-50 text-orange-950 ring-orange-200">투어패스 활용 후보</Badge>
            ) : null}
          </div>
          <p className="text-[12px] text-stone-500 font-medium mt-2">혜택·입장은 방문 전 확인이 필요해요</p>

          {lookup.hint ? (
            <p className="text-[11px] text-sky-900 font-semibold mt-2 rounded-xl px-3 py-2 bg-sky-50/90 border border-sky-100">
              {lookup.hint}
            </p>
          ) : null}

          {typeof data?.place_match_distance_m === 'number' && data.place_match_distance_m > 1800 ? (
            <p className="text-[11px] text-amber-900 font-semibold mt-2 rounded-xl px-3 py-2 bg-amber-50/90 border border-amber-100">
              검색된 Google 장소가 코스 좌표와 약 {Math.round(data.place_match_distance_m / 100) / 10}km 떨어져 있어요.
              동명이인일 수 있으니 지도에서 한 번 더 확인해 주세요.
            </p>
          ) : null}

          {step.address ? (
            <p className="text-[14px] text-stone-700 mt-4 flex gap-2 leading-snug">
              <MapPin className="w-4 h-4 shrink-0 mt-0.5 text-stone-500" />
              {step.address}
            </p>
          ) : null}

          <a
            href={mapsUrl}
            target="_blank"
            rel="noreferrer"
            className="mt-3 flex h-12 w-full items-center justify-center gap-2 rounded-lg border border-slate-200 bg-white text-[15px] font-bold text-slate-950"
          >
            <ExternalLink className="w-4 h-4" />
            지도에서 {matchedName} 보기
          </a>

          <p className="text-[13px] font-extrabold text-stone-800 mt-6 mb-2">리뷰에서 자주 나온 말</p>
          <div className="flex flex-wrap gap-1.5">
            {tags.map(t => (
              <span key={t} className="text-[11px] font-bold bg-stone-100 text-stone-700 px-2 py-1 rounded-lg">
                {t}
              </span>
            ))}
          </div>

          <p className="text-[13px] font-extrabold text-stone-800 mt-5 mb-2">리뷰</p>
          {loading ? (
            <p className="text-sm text-stone-500 py-6 text-center">불러오는 중…</p>
          ) : error ? (
            <div className="rounded-lg border border-red-200 bg-red-50/60 px-4 py-6 text-center">
              <p className="text-[14px] font-semibold text-stone-800">{error}</p>
              <button
                type="button"
                className="mt-3 text-[14px] font-bold text-teal-700 underline"
                onClick={() => retry()}
              >
                다시 시도
              </button>
            </div>
          ) : placesStatus && placesStatus !== 'ok' ? (
            <div className="rounded-lg border border-amber-200 bg-amber-50/70 px-4 py-6 text-center">
              <p className="text-[14px] font-semibold text-amber-950">
                Google Places 데이터를 불러오지 못했어요
              </p>
              <p className="text-[13px] text-amber-900/80 mt-1 break-words">
                {placesStatus === 'request_failed'
                  ? '현재 PC/네트워크에서 Google Places 연결이 거부되고 있어요.'
                  : placesStatusMessage || '검색 결과가 비어 있거나 장소 매칭이 맞지 않을 수 있어요.'}
              </p>
            </div>
          ) : reviews.length === 0 ? (
            <div className="rounded-lg border border-dashed border-slate-200 bg-white px-4 py-6 text-center">
              <p className="text-[14px] font-semibold text-stone-700">아직 표시할 리뷰가 부족해요</p>
              <p className="text-[13px] text-stone-500 mt-1">
                Google에 등록된 최근 리뷰가 없거나, 검색 결과가 비어 있을 수 있어요
              </p>
            </div>
          ) : (
            <ul className="space-y-3">
              {reviews.map((r, i) => {
                const isLong = r.text.length > 120
                const show = expanded[i] || !isLong
                const text = show ? r.text : `${r.text.slice(0, 120)}…`
                return (
                  <li key={`${r.author}-${i}`} className="rounded-lg border border-slate-100 bg-white p-3">
                    <div className="flex justify-between text-[12px] font-bold text-stone-800">
                      <span>{r.author}</span>
                      <span className="text-amber-700">★ {r.rating}점</span>
                    </div>
                    <p className="text-[13px] text-stone-700 mt-1.5 leading-relaxed whitespace-pre-wrap">{text}</p>
                    <div className="flex justify-between items-center mt-1">
                      {r.relative ? (
                        <span className="text-[11px] text-stone-400">{r.relative}</span>
                      ) : (
                        <span />
                      )}
                      {isLong ? (
                        <button
                          type="button"
                          className="text-[12px] font-bold text-teal-700"
                          onClick={() => setExpanded(e => ({ ...e, [i]: !e[i] }))}
                        >
                          {expanded[i] ? '접기' : '더보기'}
                        </button>
                      ) : null}
                    </div>
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      </div>
    </div>
  )
}
