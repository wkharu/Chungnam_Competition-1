import { useEffect, useState } from 'react'
import { ImageOff } from 'lucide-react'
import { appImageSrc, COURSE_IMAGE_FALLBACK } from '@/lib/courseImageFallback'
import type { ConsumerStep } from '@/lib/consumerCourseTypes'

export function CourseHeroCover({
  primarySrc,
  fallbackStep,
}: {
  primarySrc: string
  fallbackStep?: ConsumerStep | null
}) {
  const [src, setSrc] = useState(() => appImageSrc(primarySrc))
  const [showImg, setShowImg] = useState(true)

  useEffect(() => {
    setSrc(appImageSrc(primarySrc))
    setShowImg(true)
  }, [primarySrc])

  useEffect(() => {
    const initialSrc = appImageSrc(primarySrc)
    if (primarySrc.trim() && initialSrc !== COURSE_IMAGE_FALLBACK) return
    if (!fallbackStep) return
    const lat = fallbackStep.lat
    const lng = fallbackStep.lng
    const canFetch =
      typeof lat === 'number' &&
      typeof lng === 'number' &&
      Number.isFinite(lat) &&
      Number.isFinite(lng) &&
      Math.abs(lat) > 1e-6 &&
      Math.abs(lng) > 1e-6
    if (!canFetch) return
    let cancelled = false
    const params = new URLSearchParams({
      name: fallbackStep.name,
      lat: String(lat),
      lng: String(lng),
      address: fallbackStep.address || '',
      top_reviews: '1',
    })
    void fetch(`/api/place-reviews?${params.toString()}`)
      .then(r => (r.ok ? r.json() : null))
      .then((body: { photo_url?: string | null } | null) => {
        if (!cancelled && body?.photo_url) {
          setSrc(appImageSrc(body.photo_url))
          setShowImg(true)
        }
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [fallbackStep, primarySrc])

  return (
    <div className="relative aspect-[16/10] w-full overflow-hidden bg-[linear-gradient(135deg,#efe6d8,#e8f3ee_58%,#e7eef8)]">
      {showImg ? (
        <img
          src={src}
          alt=""
          className="w-full h-full object-cover"
          onError={() => {
            if (src !== COURSE_IMAGE_FALLBACK) {
              setSrc(COURSE_IMAGE_FALLBACK)
            } else {
              setShowImg(false)
            }
          }}
        />
      ) : (
        <div className="w-full h-full min-h-[11rem] flex flex-col items-center justify-center gap-2 text-slate-500">
          <ImageOff className="w-10 h-10" strokeWidth={1.5} />
          <span className="text-[12px] font-semibold tracking-wide">코스 이미지 준비 중</span>
        </div>
      )}
      <div className="absolute inset-x-0 bottom-0 h-24 bg-gradient-to-t from-[#2b1b12]/42 to-transparent pointer-events-none" />
    </div>
  )
}
