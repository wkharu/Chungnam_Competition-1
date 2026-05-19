import type { ConsumerStep } from '@/lib/consumerCourseTypes'
import { appImageSrc, COURSE_IMAGE_FALLBACK } from '@/lib/courseImageFallback'
import { usePlaceReviews } from '@/hooks/usePlaceReviews'
import { MessageSquare, Star } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

function LatestReviewPreview({ step }: { step: ConsumerStep }) {
  const lat = step.lat
  const lng = step.lng
  const canFetch =
    typeof lat === 'number' &&
    typeof lng === 'number' &&
    Number.isFinite(lat) &&
    Number.isFinite(lng) &&
    Math.abs(lat) > 1e-6 &&
    Math.abs(lng) > 1e-6
  const { data, loading, fetch } = usePlaceReviews()

  useEffect(() => {
    if (!canFetch) return
    void fetch(step.name, lat as number, lng as number, step.address, 3)
  }, [canFetch, fetch, lat, lng, step.address, step.name])

  if (!canFetch) return null

  const reviews = data?.places_status === 'ok' ? (data.reviews ?? []).slice(0, 3) : []
  if (loading && !data) {
    return (
      <div className="mt-3 rounded-xl border border-[#eadfce] bg-white/65 px-3 py-2">
        <div className="h-3 w-24 rounded bg-stone-200 animate-pulse" />
        <div className="mt-2 space-y-1.5">
          <div className="h-3 rounded bg-stone-100 animate-pulse" />
          <div className="h-3 w-4/5 rounded bg-stone-100 animate-pulse" />
        </div>
      </div>
    )
  }
  if (!reviews.length) return null

  return (
    <div className="mt-3 rounded-xl border border-[#eadfce] bg-white/75 px-3 py-2.5">
      <div className="flex items-center gap-1.5 text-[11px] font-black text-[#7b4b32] mb-2">
        <MessageSquare className="w-3 h-3" />
        최신 리뷰 {reviews.length}개
        {typeof data?.rating === 'number' && data.rating > 0 ? (
          <span className="ml-auto inline-flex items-center gap-0.5 text-amber-800">
            <Star className="w-3 h-3 fill-amber-400 text-amber-400" />
            {data.rating.toFixed(1)}
          </span>
        ) : null}
      </div>
      <div className="space-y-1.5">
        {reviews.map((review, index) => (
          <p key={`${review.author}-${index}`} className="text-[12px] leading-relaxed text-[#5f5046]">
            <span className="font-bold text-[#3a2a20]">{review.author}</span>
            <span className="text-amber-700 font-bold"> ★{review.rating}</span>
            {review.relative ? <span className="text-[#9a8170]"> · {review.relative}</span> : null}
            <br />
            <span className="line-clamp-2">{review.text}</span>
          </p>
        ))}
      </div>
    </div>
  )
}

export function CourseStepCard({ step, onOpen }: { step: ConsumerStep; onOpen: () => void }) {
  const [broken, setBroken] = useState(false)
  const retryRef = useRef(0)
  const originalSrc = appImageSrc(step.image)
  const src = broken ? COURSE_IMAGE_FALLBACK : originalSrc

  return (
    <div className="rounded-2xl border border-[#eadfce] bg-[#fffdf8] p-2.5 active:scale-[0.99] transition">
      <button type="button" onClick={onOpen} className="w-full text-left flex gap-3">
        <div className="relative shrink-0">
          <img
            src={src}
            alt=""
            className="w-[5rem] h-[5rem] rounded-lg object-cover bg-stone-100"
            onError={(e) => {
              if (retryRef.current < 1 && originalSrc !== COURSE_IMAGE_FALLBACK) {
                retryRef.current += 1
                setTimeout(() => {
                  const img = e.target as HTMLImageElement
                  if (img) img.src = originalSrc + (originalSrc.includes('?') ? '&_r=1' : '?_r=1')
                }, 1500)
              } else {
                setBroken(true)
              }
            }}
          />
          <span className="absolute -top-1 -left-1 grid h-6 min-w-6 place-items-center rounded-full bg-[#f28c6b] px-1.5 text-[11px] font-black text-white">
            {step.id.match(/step-(\d+)/)?.[1] ? Number(step.id.match(/step-(\d+)/)?.[1]) + 1 : ''}
          </span>
        </div>
        <div className="min-w-0 flex-1 py-0.5">
          <p className="text-[12px] font-bold text-[#c56642] uppercase tracking-wide">{step.role}</p>
          <p className="text-[16px] font-extrabold text-[#2b1b12] leading-snug mt-0.5">{step.name}</p>
          {(typeof step.rating === 'number' && step.rating > 0) ||
          (typeof step.reviewCount === 'number' && step.reviewCount > 0) ? (
            <p className="text-[12px] font-semibold text-amber-800/95 mt-1">
              {typeof step.rating === 'number' && step.rating > 0 ? <>★ {step.rating.toFixed(1)}</> : null}
              {typeof step.reviewCount === 'number' && step.reviewCount > 0 ? (
                <>
                  {typeof step.rating === 'number' && step.rating > 0 ? ' · ' : null}
                  리뷰 {step.reviewCount.toLocaleString('ko-KR')}건
                </>
              ) : null}
            </p>
          ) : null}
          <div className="flex flex-wrap gap-1.5 mt-2">
            {step.tags.map(t => (
              <span key={t} className="text-[11px] font-semibold text-[#7b6a5c] bg-[#f5ede2] px-2 py-0.5 rounded-full">
                {t}
              </span>
            ))}
          </div>
        </div>
      </button>
      <LatestReviewPreview step={step} />
    </div>
  )
}
