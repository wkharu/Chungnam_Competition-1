import { useState, useEffect, useRef } from 'react'
import { useNavigate, useSearchParams, Link } from 'react-router-dom'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { loadConfirmedCourse } from '@/lib/confirmedCourseStorage'
import type { ConfirmedCourseState } from '@/lib/confirmedCourseStorage'
import ConfirmedCourseHomeCard from '@/components/ConfirmedCourseHomeCard'
import { appendStoredUserGeo, toRecommendQuery, toResultQueryString, tripFormFromSearchParams } from '@/lib/tripParams'
import type { TripFormState } from '@/lib/tripParams'
import type { RecommendResponse, Weather } from '@/types'
import type { TripDuration } from '@/hooks/useRecommend'
import {
  Bell,
  BriefcaseBusiness,
  ChevronRight,
  CloudSun,
  Compass,
  Loader2,
  MapPin,
  Minus,
  Plus,
  RefreshCw,
  Route,
  Sparkles,
  TicketCheck,
  UserRound,
  UsersRound,
} from 'lucide-react'
import { CONSUMER_APP_NAME } from '@/config/app'
import { readFetchErrorMessage } from '@/lib/apiErrorMessage'
import { InputCard } from '@/components/consumer/InputCard'
import { TourPassToggle } from '@/components/consumer/TourPassToggle'
import { BottomCTA } from '@/components/consumer/BottomCTA'
import { LiveStatusBar } from '@/components/consumer/LiveStatusBar'
import { saveRecommendPayloadForResult } from '@/lib/recommendSessionCache'
import { writeStoredUserGeo } from '@/lib/userGeoStorage'
import {
  createParticipantId,
  getActiveParticipantId,
  setActiveParticipantId,
} from '@/lib/appUserSession'

const CITIES = [
  '천안', '아산', '공주', '보령', '논산', '부여', '당진', '태안', '홍성',
  '금산', '서산', '서천', '예산', '청양', '전체',
] as const

export default function HomePage() {
  const [searchParams] = useSearchParams()
  const spKey = searchParams.toString()
  const initialActiveParticipant = getActiveParticipantId()
  const [form, setForm] = useState<TripFormState>(() => tripFormFromSearchParams(searchParams))
  const [pending, setPending] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [participantId, setParticipantIdInput] = useState(() => initialActiveParticipant || createParticipantId())
  const [activeParticipantId, setActiveParticipantIdState] = useState<string | null>(() => initialActiveParticipant)
  const [confirmed, setConfirmed] = useState<ConfirmedCourseState | null>(() =>
    initialActiveParticipant ? loadConfirmedCourse() : null,
  )
  const [weatherSnap, setWeatherSnap] = useState<Weather | null>(null)
  const [weatherLoading, setWeatherLoading] = useState(true)
  const [weatherError, setWeatherError] = useState(false)
  const [showCover, setShowCover] = useState(() => {
    const hasQuery = searchParams.toString().length > 0
    return !hasQuery
  })
  const navigate = useNavigate()
  const urlSynced = useRef(false)

  useEffect(() => {
    setForm(tripFormFromSearchParams(searchParams))
    urlSynced.current = true
  }, [spKey])

  useEffect(() => {
    const c = loadConfirmedCourse()
    if (activeParticipantId && !showCover && c?.resultQueryString && !searchParams.toString()) {
      navigate(`/?${c.resultQueryString}`, { replace: true })
    }
  }, [activeParticipantId, showCover, searchParams, navigate])

  useEffect(() => {
    document.title = `${CONSUMER_APP_NAME}`
  }, [])

  useEffect(() => {
    const sync = () => setConfirmed(getActiveParticipantId() ? loadConfirmedCourse() : null)
    window.addEventListener('chungnam-confirmed-course-changed', sync)
    window.addEventListener('chungnam-participant-changed', sync)
    return () => {
      window.removeEventListener('chungnam-confirmed-course-changed', sync)
      window.removeEventListener('chungnam-participant-changed', sync)
    }
  }, [])

  useEffect(() => {
    if (!navigator.geolocation) return
    navigator.geolocation.getCurrentPosition(
      pos => {
        writeStoredUserGeo(pos.coords.latitude, pos.coords.longitude)
      },
      () => {},
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 120000 },
    )
  }, [])

  useEffect(() => {
    let cancelled = false
    async function loadSnap() {
      setWeatherLoading(true)
      setWeatherError(false)
      try {
        const q = new URLSearchParams({
          city: form.city === '전체' ? '아산' : form.city,
          current_time: form.currentTime,
          current_date: form.currentDate,
        })
        appendStoredUserGeo(q)
        const res = await window.fetch(`/api/weather-snapshot?${q}`)
        if (!res.ok) throw new Error('weather snapshot failed')
        const body = (await res.json()) as { weather: Weather }
        if (!cancelled) setWeatherSnap(body.weather)
      } catch {
        if (!cancelled) {
          setWeatherSnap(null)
          setWeatherError(true)
        }
      } finally {
        if (!cancelled) setWeatherLoading(false)
      }
    }
    void loadSnap()
    const onGeo = () => void loadSnap()
    window.addEventListener('chungnam-user-geo-changed', onGeo)
    return () => {
      cancelled = true
      window.removeEventListener('chungnam-user-geo-changed', onGeo)
    }
  }, [form.city, form.currentTime, form.currentDate, spKey])

  const patch = (p: Partial<TripFormState>) => setForm(f => ({ ...f, ...p }))

  function chipClass(on: boolean) {
    return `min-h-[2.75rem] rounded-xl px-4 text-[14px] font-extrabold border transition active:scale-[0.98] ${
      on ? 'app-chip-selected' : 'app-chip-idle'
    }`
  }

  async function onSubmit() {
    setErr(null)
    setPending(true)
    try {
      const q = toRecommendQuery(form)
      const res = await window.fetch(`/api/recommend?${q}`)
      if (!res.ok) {
        throw new Error(await readFetchErrorMessage(res, `잠시 후 다시 시도해 주세요`))
      }
      const data = (await res.json()) as RecommendResponse
      const back = toResultQueryString(form)
      saveRecommendPayloadForResult(back, data)
      navigate(`/result?${back}`, { state: { data } })
    } catch (e) {
      setErr(e instanceof Error ? e.message : '잠시 후 다시 시도해 주세요')
    } finally {
      setPending(false)
    }
  }

  const solo = form.companion === 'solo'
  const withKids = form.companion === 'family' && Number(form.childCount || 0) > 0

  const snapLine = weatherSnap
    ? [
        weatherSnap.sky_text?.trim() || null,
        typeof weatherSnap.temp === 'number' && !Number.isNaN(weatherSnap.temp)
          ? `${Math.round(weatherSnap.temp)}°`
          : null,
        typeof weatherSnap.precip_prob === 'number' && !Number.isNaN(weatherSnap.precip_prob)
          ? `강수 확률 ${Math.round(weatherSnap.precip_prob)}%`
          : null,
      ]
        .filter(Boolean)
        .join(' · ')
    : ''

  function activateParticipant(): string {
    const id = setActiveParticipantId(participantId)
    setParticipantIdInput(id)
    setActiveParticipantIdState(id)
    setConfirmed(loadConfirmedCourse())
    setShowCover(false)
    return id
  }

  async function startWithRecommendation() {
    activateParticipant()
    await onSubmit()
  }

  if (showCover) {
    return (
      <div className="consumer-shell min-h-[100dvh] max-w-none overflow-hidden bg-[#fffaf3]">
        <section className="relative mx-auto flex min-h-[100dvh] w-full max-w-4xl flex-col px-5 py-5 sm:px-8 lg:justify-center lg:py-10">
          <div className="relative h-[18rem] overflow-hidden rounded-[1.75rem] border border-[#eadfce] shadow-[0_24px_80px_-58px_rgba(80,48,28,0.52)] sm:h-[22rem] lg:h-[26rem]">
            <img
              src="/hero-course-placeholder.svg"
              alt=""
              className="absolute inset-0 h-full w-full object-cover scale-105"
            />
            <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(255,250,243,0)_0%,rgba(255,250,243,0.08)_52%,rgba(255,250,243,0.5)_100%)]" />
            <div className="absolute left-5 top-5 inline-flex items-center gap-2 rounded-full bg-white/88 px-3 py-1.5 text-[12px] font-black text-[#9a4528] shadow-sm ring-1 ring-white/70">
              <Sparkles className="h-3.5 w-3.5" />
              충남 여행 AI
            </div>
            <div className="absolute right-5 top-5 grid h-11 w-11 place-items-center rounded-2xl bg-white/88 shadow-sm ring-1 ring-white/70">
              <Compass className="h-5 w-5 text-[#b45b37]" />
            </div>
          </div>

          <div className="relative z-10 -mt-10 flex flex-1 flex-col rounded-[1.75rem] border border-[#eadfce] bg-[#fffdf8]/92 p-5 shadow-[0_24px_70px_-54px_rgba(80,48,28,0.42)] sm:p-7 lg:-mt-16 lg:p-9">
            <p className="text-[13px] font-black tracking-wide text-[#f28c6b] sm:text-[15px]">떠나GO</p>
            <h1 className="mt-2 text-[34px] font-black leading-[1.05] tracking-tight text-[#2b1b12] text-balance-safe sm:text-[46px] lg:text-[54px]">
              날씨가 고른
              <br />
              오늘의 충남 코스
            </h1>
            <p className="mt-3 max-w-2xl text-[15px] font-semibold leading-relaxed text-[#6f6257] sm:text-[17px] lg:mt-5 lg:text-[18px]">
              지금 날씨, 이동 시간, 투어패스 활용 가능성을 함께 보고 오늘 움직일 코스를 제안합니다.
            </p>

            <div className="mt-5 grid grid-cols-3 gap-2 sm:gap-3 lg:mt-8">
              <div className="rounded-2xl border border-[#eadfce] bg-white px-3 py-3 shadow-[0_10px_24px_-22px_rgba(80,48,28,0.55)] sm:p-4">
                <CloudSun className="h-5 w-5 text-[#e89c31] sm:h-6 sm:w-6" />
                <p className="mt-2 text-[11px] font-black text-[#3a2a20] sm:text-[13px]">날씨 맞춤</p>
              </div>
              <div className="rounded-2xl border border-[#eadfce] bg-white px-3 py-3 shadow-[0_10px_24px_-22px_rgba(80,48,28,0.55)] sm:p-4">
                <Route className="h-5 w-5 text-[#2f8f83] sm:h-6 sm:w-6" />
                <p className="mt-2 text-[11px] font-black text-[#3a2a20] sm:text-[13px]">동선 추천</p>
              </div>
              <div className="rounded-2xl border border-[#eadfce] bg-white px-3 py-3 shadow-[0_10px_24px_-22px_rgba(80,48,28,0.55)] sm:p-4">
                <TicketCheck className="h-5 w-5 text-[#c56642] sm:h-6 sm:w-6" />
                <p className="mt-2 text-[11px] font-black text-[#3a2a20] sm:text-[13px]">패스 후보</p>
              </div>
            </div>

            <div className="mt-auto grid gap-3 pt-6 sm:grid-cols-[1fr_auto] sm:items-end">
              <div className="rounded-2xl border border-[#eadfce] bg-white/85 px-4 py-3 sm:col-span-2">
                <label className="text-[11px] font-black text-[#9a8170]" htmlFor="participant-id">
                  참가자 ID
                </label>
                <div className="mt-2 flex gap-2">
                  <div className="flex min-w-0 flex-1 items-center gap-2 rounded-xl border border-[#eadfce] bg-[#fffdf8] px-3">
                    <UserRound className="h-4 w-4 shrink-0 text-[#8b5f40]" />
                    <input
                      id="participant-id"
                      value={participantId}
                      onChange={e => setParticipantIdInput(e.target.value)}
                      className="h-11 min-w-0 flex-1 bg-transparent text-[15px] font-extrabold text-[#2b1b12] outline-none"
                      placeholder="예: team-01"
                    />
                  </div>
                  <button
                    type="button"
                    onClick={() => setParticipantIdInput(createParticipantId())}
                    className="grid h-11 w-11 shrink-0 place-items-center rounded-xl border border-[#eadfce] bg-[#fffdf8] text-[#7b4b32]"
                    aria-label="참가자 ID 새로 만들기"
                  >
                    <RefreshCw className="h-4 w-4" />
                  </button>
                </div>
                <p className="mt-2 text-[12px] font-semibold text-[#7b6a5c]">
                  이 ID별로 추천 기록과 확정 코스가 따로 저장됩니다.
                </p>
              </div>

              {weatherSnap ? (
                <div className="rounded-2xl border border-[#eadfce] bg-white/80 px-4 py-3 sm:col-span-2 lg:px-5 lg:py-4">
                  <p className="text-[11px] font-black text-[#9a8170]">지금 기준</p>
                  <p className="mt-1 text-[14px] font-black text-[#2b1b12] lg:text-[16px]">
                    {form.city === '전체' ? '아산' : form.city} · {snapLine || '날씨 요약 없음'}
                  </p>
                </div>
              ) : null}

              <button
                type="button"
                onClick={() => activateParticipant()}
                className="flex h-14 w-full items-center justify-center gap-2 rounded-2xl bg-[#f28c6b] px-8 text-[16px] font-black text-white shadow-[0_14px_28px_-18px_rgba(194,83,42,0.8)] active:scale-[0.99] sm:w-auto lg:h-16 lg:text-[18px]"
              >
                시작하기
                <ChevronRight className="h-5 w-5" />
              </button>
              <button
                type="button"
                onClick={() => void startWithRecommendation()}
                disabled={pending}
                className="h-12 w-full rounded-2xl border border-[#eadfce] bg-[#fffdf8] px-6 text-[14px] font-extrabold text-[#7b4b32] disabled:opacity-60 sm:w-auto"
              >
                {pending ? '추천 준비 중...' : '기본 조건으로 바로 추천'}
              </button>
            </div>
          </div>
        </section>
      </div>
    )
  }

  return (
    <div className="consumer-shell pb-28 lg:max-w-6xl lg:px-8 lg:pb-10">
      <header className="app-hero-band px-5 pt-[max(0.55rem,env(safe-area-inset-top))] pb-4 lg:px-0 lg:pt-8 lg:pb-6">
        <LiveStatusBar className="-mx-1 lg:hidden" />
        <div className="mt-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-[#fff0e9] text-[#b45b37] ring-1 ring-[#f1d1bf]">
              <BriefcaseBusiness className="h-5 w-5" />
            </span>
            <h1 className="text-[26px] font-black tracking-tight text-balance-safe lg:text-[38px]">
              여행 코스 추천
            </h1>
          </div>
          <button type="button" className="relative grid h-10 w-10 place-items-center rounded-full bg-[#fffdf8] border border-[#eadfce]">
            <Bell className="h-5 w-5" />
            <span className="absolute right-2.5 top-2 h-2 w-2 rounded-full bg-[#f28c6b]" />
          </button>
        </div>
        <p className="text-[14px] text-[#7b6a5c] font-medium mt-2 leading-snug lg:max-w-2xl lg:text-[17px]">
          {activeParticipantId ? `${activeParticipantId}님의 취향에 딱 맞는 충남 여행을 제안해드려요.` : '당신의 취향에 딱 맞는 충남 여행을 제안해드려요.'}
        </p>
        <div className="mt-4 rounded-2xl border border-[#eadfce] bg-[#fffdf8] px-4 py-3 shadow-[0_10px_28px_-24px_rgba(80,48,28,0.45)] lg:max-w-2xl lg:px-5 lg:py-4">
          {weatherSnap ? (
            <div className="flex items-center gap-3">
              <CloudSun className="h-12 w-12 text-[#f0a33b]" strokeWidth={1.5} />
              <div className="min-w-0 flex-1">
                <p className="text-[15px] font-black text-[#2b1b12] leading-snug">
                  {form.city === '전체' ? '아산' : form.city} · {snapLine || '요약 없음'}
                </p>
                <p className="text-[12px] text-[#7b6a5c] mt-0.5">
                  {weatherSnap.weather_source === 'vilagefcst' ? '기상청 단기예보 기준이에요.' : '대체 날씨 기준이에요.'}
                </p>
              </div>
            </div>
          ) : (
            <div className="flex items-center gap-3">
              <CloudSun className="h-12 w-12 text-[#d5a46e]" strokeWidth={1.5} />
              <div className="min-w-0 flex-1">
                <p className="text-[15px] font-black text-[#2b1b12] leading-snug">
                  {weatherLoading ? '실시간 날씨 확인 중…' : weatherError ? '날씨 연결 확인 필요' : '날씨 요약 대기 중'}
                </p>
                <p className="text-[12px] text-[#7b6a5c] mt-0.5">추천은 서버 예보를 다시 확인해서 계산해요.</p>
              </div>
            </div>
          )}
          {weatherSnap?.forecast_anchor_reason === 'gps_nearest' && weatherSnap.forecast_anchor_city ? (
              <p className="text-[11px] text-[#7b6a5c] font-semibold mt-2">
                내 위치에 가까운 격자{' '}
                <span className="font-extrabold">{weatherSnap.forecast_anchor_city}</span>
              </p>
            ) : null}
        </div>
      </header>

      <main className="flex-1 px-5 pt-1 space-y-5 overflow-y-auto lg:grid lg:grid-cols-2 lg:gap-x-5 lg:gap-y-5 lg:space-y-0 lg:overflow-visible lg:px-0">
        {confirmed ? (
          <div className="lg:col-span-2">
            <ConfirmedCourseHomeCard confirmed={confirmed} />
          </div>
        ) : null}
        <InputCard title="여행 지역">
          <Select value={form.city} onValueChange={v => patch({ city: v ?? '전체' })}>
            <SelectTrigger className="travel-field">
              <MapPin className="mr-2 h-4 w-4 text-[#8b5f40]" />
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {CITIES.map(c => (
                <SelectItem key={c} value={c}>
                  {c}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </InputCard>

        <InputCard title="여행 일정">
          <div className="flex flex-wrap gap-2">
            {(
              [
                ['2h', '2시간'],
                ['half-day', '반나절'],
                ['full-day', '하루'],
              ] as const
            ).map(([v, label]) => (
              <button
                key={v}
                type="button"
                className={chipClass(form.tripDuration === v)}
                onClick={() =>
                  patch({
                    tripDuration: v as TripDuration,
                    durationFullKind: '1d',
                  })
                }
              >
                {label}
              </button>
            ))}
          </div>
        </InputCard>

        <InputCard title="동행 유형">
          <div className="flex flex-wrap gap-2">
            {(
              [
                ['solo', '혼자'],
                ['couple', '연인'],
                ['family', '가족'],
                ['friends', '친구'],
                ['family_kids', '아이 동반'],
              ] as const
            ).map(([k, label]) => {
              const selected =
                k === 'family_kids'
                  ? form.companion === 'family' && form.childCount !== '0'
                  : form.companion === k
              return (
                <button
                  key={k}
                  type="button"
                  className={chipClass(selected)}
                  onClick={() => {
                    if (k === 'family_kids') {
                      patch({ companion: 'family', childCount: '1' })
                    } else {
                      patch({
                        companion: k,
                        adultCount: k === 'solo' ? '1' : form.adultCount,
                        childCount: k === 'solo' ? '0' : k === 'family' ? form.childCount : form.childCount,
                      })
                    }
                  }}
                >
                  {label}
                </button>
              )
            })}
          </div>
        </InputCard>

        <InputCard title="여행 목적 / 테마">
          <div className="flex flex-wrap gap-2">
            {(
              [
                ['healing', '자연 힐링'],
                ['photo', '사진'],
                ['culture', '축제'],
                ['food', '맛집'],
                ['indoor', '실내'],
                ['walking', '가볍게 산책'],
              ] as const
            ).map(([k, label]) => {
              const foodOn = form.mealPreference === '한식'
              const on =
                k === 'food'
                  ? foodOn
                  : k === 'healing'
                    ? form.tripGoal === 'healing' && !foodOn
                    : form.tripGoal === k
              return (
                <button
                  key={k}
                  type="button"
                  className={chipClass(on)}
                  onClick={() => {
                    if (k === 'food') {
                      patch({ tripGoal: 'healing', mealPreference: '한식' })
                    } else {
                      patch({ tripGoal: k, mealPreference: 'none' })
                    }
                  }}
                >
                  {label}
                </button>
              )
            })}
          </div>
        </InputCard>

        <InputCard title="이동 수단">
          <div className="flex flex-wrap gap-2">
            {(
              [
                ['car', '자차'],
                ['public', '대중교통'],
                ['walk', '도보 위주'],
              ] as const
            ).map(([v, label]) => (
              <button
                key={v}
                type="button"
                className={chipClass(form.transport === v)}
                onClick={() => patch({ transport: v })}
              >
                {label}
              </button>
            ))}
          </div>
        </InputCard>

        {!solo ? (
          <InputCard title="인원">
            <div className="space-y-2.5">
              <div className="travel-field">
                <button type="button" className="grid h-8 w-8 place-items-center rounded-lg border border-[#eadfce] bg-white" onClick={() => patch({ adultCount: String(Math.max(1, Number(form.adultCount || 1) - 1)) })}>
                  <Minus className="h-4 w-4" />
                </button>
                <div className="flex items-center gap-2">
                  <UsersRound className="h-4 w-4 text-[#8b5f40]" />
                  <span>성인 {form.adultCount}명</span>
                </div>
                <button type="button" className="grid h-8 w-8 place-items-center rounded-lg border border-[#eadfce] bg-white" onClick={() => patch({ adultCount: String(Math.min(10, Number(form.adultCount || 1) + 1)) })}>
                  <Plus className="h-4 w-4" />
                </button>
              </div>
              {withKids ? (
                <div className="travel-field">
                  <button type="button" className="grid h-8 w-8 place-items-center rounded-lg border border-[#eadfce] bg-white" onClick={() => patch({ childCount: String(Math.max(1, Number(form.childCount || 1) - 1)) })}>
                    <Minus className="h-4 w-4" />
                  </button>
                  <div className="flex items-center gap-2">
                    <UsersRound className="h-4 w-4 text-[#8b5f40]" />
                    <span>아이 {form.childCount}명</span>
                  </div>
                  <button type="button" className="grid h-8 w-8 place-items-center rounded-lg border border-[#eadfce] bg-white" onClick={() => patch({ childCount: String(Math.min(8, Number(form.childCount || 1) + 1)) })}>
                    <Plus className="h-4 w-4" />
                  </button>
                </div>
              ) : null}
            </div>
          </InputCard>
        ) : null}

        <div className="lg:col-span-2">
          <TourPassToggle enabled={form.tourpassMode} onChange={v => patch({ tourpassMode: v })} />
        </div>

        {err ? (
          <p className="text-sm text-destructive text-center font-medium lg:col-span-2" role="alert">
            {err}
          </p>
        ) : null}

        <p className="text-center pb-2 lg:col-span-2">
          <Link to="/admin/pass-quest-mock" className="text-[12px] font-medium text-stone-400 underline">
            운영
          </Link>
        </p>
      </main>

      <BottomCTA
        disabled={pending}
        onClick={e => {
          e.preventDefault()
          void onSubmit()
        }}
      >
        {pending ? (
          <span className="inline-flex flex-col items-center justify-center gap-1">
            <span className="inline-flex items-center justify-center gap-2">
              <Loader2 className="w-5 h-5 animate-spin" />
              불러오는 중…
            </span>
            <span className="text-[11px] font-medium text-white/85">첫 요청은 30초~1분 걸릴 수 있어요</span>
          </span>
        ) : (
          '오늘 코스 추천받기'
        )}
      </BottomCTA>
    </div>
  )
}
