import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Loader2 } from 'lucide-react'
import { useRecommendFromRoute } from '@/hooks/useRecommendFromRoute'
import {
  appendStoredUserGeo,
  toRecommendQuery,
  toResultQueryString,
  tripFormFromSearchParams,
} from '@/lib/tripParams'
import type { RecommendResponse, Weather } from '@/types'
import { Button } from '@/components/ui/button'
import { CONSUMER_APP_NAME } from '@/config/app'
import { saveConfirmedCourse } from '@/lib/confirmedCourseStorage'
import { buildConsumerCourse } from '@/lib/mapRecommendToConsumerCourse'
import type { ConsumerStep } from '@/lib/consumerCourseTypes'
import { Badge } from '@/components/consumer/Badge'
import { CourseHeroCover } from '@/components/consumer/CourseHeroCover'
import { CourseStepCard } from '@/components/consumer/CourseStepCard'
import { PlaceDetailModal } from '@/components/consumer/PlaceDetailModal'
import { LiveStatusBar } from '@/components/consumer/LiveStatusBar'
import { readFetchErrorMessage } from '@/lib/apiErrorMessage'
import { logPassQuestEvent } from '@/lib/passQuestAnalytics'
import { saveRecommendPayloadForResult } from '@/lib/recommendSessionCache'

function weatherLooksUsable(w: Weather | null | undefined): boolean {
  if (!w || typeof w !== 'object') return false
  if (typeof w.temp === 'number' && !Number.isNaN(w.temp)) return true
  if (typeof w.sky_text === 'string' && w.sky_text.trim().length > 0) return true
  if (typeof w.precip_prob === 'number' && !Number.isNaN(w.precip_prob)) return true
  return false
}

function ResultWeatherBand({ weather }: { weather: Weather }) {
  const slot = weather.fcst_time_slot?.trim()
  const timeLine =
    slot && slot.length >= 4
      ? `${(weather.current_date_iso ?? '').slice(0, 10)} · ${slot.slice(0, 2)}:${slot.slice(2)}`
      : weather.current_date_iso
        ? `${weather.current_date_iso.slice(0, 10)} 기준`
        : null

  const line = [
    weather.sky_text?.trim() || null,
    typeof weather.temp === 'number' && !Number.isNaN(weather.temp) ? `${Math.round(weather.temp)}°` : null,
    typeof weather.precip_prob === 'number' && !Number.isNaN(weather.precip_prob)
      ? `강수 ${Math.round(weather.precip_prob)}%`
      : null,
  ]
    .filter(Boolean)
    .join(' · ')

  const liveForecast = weather.weather_source === 'vilagefcst' && !weather.weather_fallback

  return (
    <section className="consumer-card-elevated px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[10px] font-semibold uppercase tracking-wide text-[#9a8170]">오늘 날씨</p>
        {liveForecast ? (
          <span className="text-[10px] font-bold text-[#8b5f40] bg-[#fff0e9] px-2 py-0.5 rounded-full">실시간 예보</span>
        ) : (
          <span className="text-[10px] font-semibold text-[#7b6a5c] bg-[#f5ede2] px-2 py-0.5 rounded-full">참고값</span>
        )}
      </div>
      <p className="text-[17px] font-black text-[#2b1b12] mt-1 tracking-tight">{line || '날씨 정보 없음'}</p>
      {weather.hour != null && weather.minute != null ? (
        <p className="text-[12px] text-stone-500 mt-1.5">
          동선 시각 {String(weather.hour).padStart(2, '0')}:{String(weather.minute).padStart(2, '0')}
        </p>
      ) : null}
      {timeLine ? <p className="text-[11px] text-stone-400 mt-0.5">{timeLine}</p> : null}

      {weather.weather_fallback ? (
        <p className="text-[12px] text-stone-600 mt-3 leading-relaxed border-l-2 border-orange-200 pl-3">
          실시간 기상청 예보에 연결되지 않았어요. 위 표시는 참고용이니 출발 전 날씨를 한 번 더 확인해 주세요.
        </p>
      ) : null}

      {weather.forecast_anchor_reason === 'gps_nearest' && weather.forecast_anchor_city ? (
        <p className="text-[11px] text-emerald-900 mt-2 rounded-xl bg-emerald-50/90 px-3 py-2 border border-emerald-100/90">
          내 위치 기준 예보 격자 <span className="font-bold">{weather.forecast_anchor_city}</span>
        </p>
      ) : null}
    </section>
  )
}

export default function ConsumerResultPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const { form, data, loading, error } = useRecommendFromRoute()
  const [detailStep, setDetailStep] = useState<ConsumerStep | null>(null)
  const [retryHint, setRetryHint] = useState<string | null>(null)
  const [weatherSnap, setWeatherSnap] = useState<Weather | null>(null)
  const mockMode = searchParams.get('mock') === '1'

  const qs = toResultQueryString(form)
  const hasRecommendWeather = !mockMode && weatherLooksUsable(data?.weather)

  useEffect(() => {
    document.title = `${CONSUMER_APP_NAME} · 오늘 코스`
  }, [])

  useEffect(() => {
    if (!data?.pass_quest?.enabled || !data.pass_quest.top_pass_quest) return
    logPassQuestEvent('pass_quest_view', {
      quest_id: data.pass_quest.top_pass_quest.quest_id,
      ticket_type: data.pass_quest.ticket_type,
    })
  }, [data])

  useEffect(() => {
    if (mockMode || hasRecommendWeather) {
      setWeatherSnap(null)
      return
    }
    let cancelled = false
    const p = new URLSearchParams()
    p.set('city', form.city || '전체')
    if (form.currentTime) p.set('current_time', form.currentTime)
    if (form.currentDate) p.set('current_date', form.currentDate)
    appendStoredUserGeo(p)
    void fetch(`/api/weather-snapshot?${p.toString()}`)
      .then(r => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((body: { weather?: Weather }) => {
        if (cancelled || !body?.weather || !weatherLooksUsable(body.weather)) return
        setWeatherSnap(body.weather)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [mockMode, hasRecommendWeather, form.city, form.currentTime, form.currentDate])

  const missingTopCourse = !mockMode && !!data && !data.top_course

  const course = useMemo(() => {
    if (mockMode || !data?.top_course) {
      return buildConsumerCourse(null, form)
    }
    return buildConsumerCourse(data, form)
  }, [data, form, mockMode])

  const payload: RecommendResponse | null = data

  const weatherForBand: Weather | null = useMemo(() => {
    if (mockMode) return null
    if (weatherLooksUsable(payload?.weather)) return payload!.weather
    if (weatherLooksUsable(weatherSnap)) return weatherSnap
    return null
  }, [mockMode, payload, weatherSnap])

  async function refreshRecommend() {
    setRetryHint(null)
    try {
      const f = tripFormFromSearchParams(new URLSearchParams(qs))
      const res = await window.fetch(`/api/recommend?${toRecommendQuery(f)}`)
      if (!res.ok) throw new Error(await readFetchErrorMessage(res, `오류 (${res.status})`))
      const next = (await res.json()) as RecommendResponse
      saveRecommendPayloadForResult(qs, next)
      navigate(`/result?${qs}`, { replace: true, state: { data: next } })
    } catch (e) {
      setRetryHint(e instanceof Error ? e.message : '다시 불러오지 못했어요.')
    }
  }

  if (loading && !data && !mockMode) {
    return (
      <div className="consumer-shell items-center justify-center px-6">
        <Loader2 className="w-8 h-8 animate-spin text-[#f28c6b] mb-3" />
        <p className="text-[15px] font-semibold text-stone-600">오늘 코스를 준비하고 있어요…</p>
        <p className="text-[13px] text-stone-500 mt-3 text-center leading-relaxed px-2">
          서버에서 장소·날씨를 묶는 데 시간이 걸릴 수 있어요. 새로고침하지 말고 이 화면을 유지해 주세요.
        </p>
      </div>
    )
  }

  if (error && !mockMode) {
    return (
      <div className="consumer-shell items-center justify-center px-6">
        <p className="text-[15px] text-destructive font-semibold text-center">{error}</p>
        <button
          type="button"
          onClick={() => void refreshRecommend()}
          className="mt-4 text-[15px] font-bold text-orange-600 underline"
        >
          다시 시도
        </button>
        {retryHint ? (
          <p className="mt-3 text-[13px] text-destructive font-medium text-center">{retryHint}</p>
        ) : null}
        <Link to={`/?${qs}`} className="mt-2 text-sm text-stone-500">
          처음으로
        </Link>
      </div>
    )
  }

  function confirmCourse() {
    if (payload) {
      saveConfirmedCourse({
        resultQueryString: qs,
        committedCourseAltId: null,
        recommendPayload: payload,
      })
    }
    navigate(`/?${qs}`)
  }

  return (
    <div className="consumer-shell pb-36">
      <header className="sticky top-0 z-30 border-b consumer-header-blur">
        <LiveStatusBar />
        <div className="flex items-center gap-2 px-2 pb-3">
        <Link
          to={`/?${qs}`}
          className="inline-flex h-11 w-11 items-center justify-center rounded-xl hover:bg-white/90 transition-colors"
          aria-label="뒤로"
        >
          <ArrowLeft className="w-5 h-5" />
        </Link>
        <div className="flex-1 text-center pr-10">
          <p className="text-[11px] font-bold text-[#c56642] tracking-wide">추천 코스</p>
          <p className="text-[16px] font-extrabold truncate">{CONSUMER_APP_NAME}</p>
        </div>
        </div>
      </header>

      <div className="px-5 pt-4 space-y-4">
        <p className="text-[12px] font-extrabold text-[#f28c6b] px-0.5">자연 속 힐링 여행</p>
        <h1 className="text-[25px] font-black leading-tight tracking-tight text-[#2b1b12] px-0.5 text-balance-safe">
          {course.title}
        </h1>

        {missingTopCourse ? (
          <div className="rounded-2xl border border-[#eadfce] bg-[#fffdf8] px-4 py-3.5 shadow-[0_8px_24px_-18px_rgba(80,48,28,0.22)]">
            <p className="text-[14px] font-bold text-stone-900 leading-snug">예시 동선을 보여 드려요</p>
            <p className="text-[13px] text-stone-600 mt-2 leading-relaxed">
              선택한 조건으로 받아온 장소가 부족할 때 나오는 화면이에요. 지역·일정을 바꾸거나 아래에서 다시 받아
              보세요.
            </p>
            <button
              type="button"
              className="mt-3 text-[14px] font-bold text-[#c56642] underline underline-offset-2"
              onClick={() => void refreshRecommend()}
            >
              추천 다시 받기
            </button>
            {retryHint ? (
              <p className="mt-2 text-[13px] text-destructive font-semibold">{retryHint}</p>
            ) : null}
          </div>
        ) : null}

        <div className="flex flex-wrap gap-2">
          {course.badges.map(b => (
            <Badge
              key={b}
              className={
                b === '투어패스 우선'
                  ? 'bg-[#fff0e9] text-[#9a4528] ring-[#f1d1bf] ring-1'
                  : undefined
              }
            >
              {b}
            </Badge>
          ))}
        </div>

        {weatherForBand ? <ResultWeatherBand weather={weatherForBand} /> : null}

        {payload?.main_scoring_model?.tourpass_merchant_filter_fallback ? (
          <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3">
            <p className="text-[13px] font-bold text-amber-950">투어패스 후보가 부족해 일반 추천 풀까지 넓혔어요.</p>
            <p className="text-[12px] text-amber-900/80 mt-1 leading-relaxed">
              표시된 혜택 가능성은 방문 전 운영처에서 한 번 더 확인해 주세요.
            </p>
          </div>
        ) : null}

        <button
          type="button"
          onClick={() => course.steps[0] && setDetailStep(course.steps[0])}
          className="w-full consumer-card-elevated overflow-hidden p-0 text-left active:scale-[0.99] transition-transform"
        >
          <CourseHeroCover primarySrc={course.heroImage?.trim() ?? ''} fallbackStep={course.steps[0] ?? null} />
          <div className="p-4">
            <p className="text-[15px] font-bold text-[#3a2a20] leading-snug">{course.subtitle}</p>
            <div className="flex flex-wrap gap-2 mt-3 text-[13px] font-semibold text-[#7b6a5c]">
              <span>{course.durationLabel}</span>
              <span>·</span>
              <span>{course.transportLabel}</span>
            </div>
            <p className="text-[13px] text-[#9a8170] mt-1">{course.mobilityLine}</p>
            {course.tourPassEnabled ? (
              <span className="inline-flex mt-3 text-[12px] font-bold text-[#9a4528] bg-[#fff0e9] px-3 py-1 rounded-full ring-1 ring-[#f1d1bf]">
                혜택 가능성 · {course.tourPassNote}
              </span>
            ) : null}
          </div>
        </button>

        <section>
          <p className="text-[11px] font-bold uppercase tracking-wide text-[#9a8170] px-0.5 mb-1">코스 상세</p>
          <h2 className="text-[17px] font-extrabold mb-3 px-0.5 tracking-tight text-[#2b1b12]">오늘 동선</h2>
          <div className="space-y-2.5">
            {course.steps.map(s => (
              <CourseStepCard key={s.id} step={s} onOpen={() => setDetailStep(s)} />
            ))}
          </div>
        </section>
      </div>

      <div className="fixed bottom-0 left-0 right-0 z-40 max-w-lg mx-auto consumer-dock border-t px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] space-y-2">
        <Button
          type="button"
          className="w-full h-14 rounded-xl text-[16px] font-extrabold app-primary-button border-0"
          onClick={confirmCourse}
        >
          이 코스로 진행할게요
        </Button>
        <Button
          type="button"
          variant="outline"
          className="w-full h-12 rounded-xl font-bold border border-[#eadfce] bg-[#fffdf8] text-[#7b4b32]"
          onClick={() => {
            if (payload) saveRecommendPayloadForResult(qs, payload)
            navigate(`/result/more?${qs}`)
          }}
        >
          다른 코스도 볼까요?
        </Button>
        <button
          type="button"
          className="w-full h-11 text-[15px] font-bold text-[#6f6257]"
          onClick={() => {
            if (!payload) return
            saveRecommendPayloadForResult(qs, payload)
            navigate(`/result/edit?${qs}`)
          }}
          disabled={!payload}
        >
          코스 수정하기
        </button>
      </div>

      <PlaceDetailModal
        step={detailStep}
        open={!!detailStep}
        onClose={() => setDetailStep(null)}
        recommendData={mockMode ? null : payload}
      />
    </div>
  )
}
