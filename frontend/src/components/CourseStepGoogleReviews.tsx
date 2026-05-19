import { useCallback, useState } from 'react'
import { ExternalLink, MessageSquare, Star } from 'lucide-react'
import { usePlaceReviews } from '@/hooks/usePlaceReviews'
import type { CourseStep } from '@/types'

function ReviewLine({
  author,
  rating,
  text,
  relative,
}: {
  author: string
  rating: number
  text: string
  relative: string
}) {
  return (
    <div className="border-t border-border/25 pt-2 first:border-t-0 first:pt-0">
      <div className="flex items-center gap-1.5 mb-1 flex-wrap">
        <span className="text-[11px] font-semibold text-foreground">{author}</span>
        <div className="flex">
          {Array.from({ length: 5 }).map((_, s) => (
            <Star
              key={s}
              className={`w-2.5 h-2.5 ${
                s < rating ? 'fill-amber-400 text-amber-400' : 'text-muted-foreground/30'
              }`}
            />
          ))}
        </div>
        {relative ? (
          <span className="text-[10px] text-muted-foreground ml-auto">{relative}</span>
        ) : null}
      </div>
      <p className="text-[11px] text-foreground/85 leading-relaxed line-clamp-5">{text}</p>
    </div>
  )
}

/** 코스 단계 카드 안에서 Google API 리뷰 상위 N개 펼침 */
export default function CourseStepGoogleReviews({
  step,
  variant = 'light',
}: {
  step: CourseStep
  /** 결과 화면(밝은 카드) vs 다크 영역 */
  variant?: 'light' | 'dark'
}) {
  const lat = step.lat
  const lng = step.lng
  const ok =
    typeof lat === 'number' &&
    typeof lng === 'number' &&
    !Number.isNaN(lat) &&
    !Number.isNaN(lng) &&
    Math.abs(lat) > 1e-6 &&
    Math.abs(lng) > 1e-6

  const { data, loading, fetched, fetch } = usePlaceReviews()
  const [open, setOpen] = useState(false)

  const addr = step.address ?? ''

  const toggle = useCallback(() => {
    if (!open && !fetched) {
      void fetch(step.name, lat as number, lng as number, addr)
    }
    setOpen((v) => !v)
  }, [open, fetched, fetch, step.name, lat, lng, addr])

  if (!ok) {
    return (
      <p className="text-[10px] text-muted-foreground mt-2">
        위치 좌표가 없어 Google 리뷰를 불러올 수 없어요.
      </p>
    )
  }

  const btnBase =
    variant === 'dark'
      ? 'text-white/90 bg-white/15 hover:bg-white/25 border-white/25'
      : 'text-foreground bg-primary/10 hover:bg-primary/15 border-primary/25'

  return (
    <div className="mt-2 pt-2 border-t border-border/30">
      <div className="flex flex-wrap items-center gap-1.5">
        <button
          type="button"
          onClick={toggle}
          className={`inline-flex items-center gap-1 text-[11px] font-semibold px-2.5 py-1 rounded-lg border transition-colors ${btnBase}`}
        >
          <MessageSquare className="w-3 h-3" />
          {open ? '리뷰 접기' : 'Google 리뷰 보기'}
        </button>
        {open && fetched && data?.google_maps ? (
          <a
            href={data.google_maps}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[11px] px-2 py-1 rounded-lg border border-border/40 text-foreground/80 hover:bg-muted/80 inline-flex items-center gap-0.5"
          >
            <ExternalLink className="w-3 h-3" />
            지도
          </a>
        ) : null}
        {open && fetched && data?.website ? (
          <a
            href={data.website}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[11px] px-2 py-1 rounded-lg border border-border/40 text-foreground/80 hover:bg-muted/80 inline-flex items-center gap-0.5"
          >
            <ExternalLink className="w-3 h-3" />
            홈페이지
          </a>
        ) : null}
      </div>

      {open && (
        <div className="mt-2">
          {loading ? (
            <div className="space-y-2">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-10 rounded-lg bg-muted animate-pulse" />
              ))}
            </div>
          ) : !data || !data.reviews?.length ? (
            <p className="text-[11px] text-muted-foreground mt-1">
              표시할 리뷰가 없거나 로드에 실패했어요.
            </p>
          ) : (
            <div className="rounded-xl bg-white/85 border border-border/35 px-2.5 py-2 mt-1.5 space-y-1.5">
              <p className="text-[10px] font-semibold text-foreground/90">
                Google 최신 리뷰 · {data.reviews.length}개
                {data.review_count > data.reviews.length
                  ? ` (전체 ${data.review_count.toLocaleString()}건 중)`
                  : null}
              </p>
              {(data.rating ?? 0) > 0 ? (
                <p className="text-[11px] text-foreground/80">
                  <span className="font-bold">{(data.rating as number).toFixed(1)}</span>
                  <span className="text-muted-foreground">
                    {' '}
                    · 리뷰 {data.review_count.toLocaleString()}건
                  </span>
                </p>
              ) : null}
              {data.reviews.map((r, i) => (
                <ReviewLine
                  key={`${r.author}-${i}`}
                  author={r.author}
                  rating={r.rating}
                  text={r.text}
                  relative={r.relative}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
