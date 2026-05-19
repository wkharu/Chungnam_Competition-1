/**
 * Node 게이트웨이: Vite 빌드 SPA + 기상청/에어코리아(Node fetch) + FastAPI 프록시·선택적 자식 프로세스.
 *
 * 실행: npm start → http://127.0.0.1:3080
 *
 * • 기본: Node가 FastAPI 자식을 띄움 → 프록시는 PYTHON_BACKEND_PORT 또는 기본 8001
 *   (터미널에서 따로 `python main.py`(PORT=8000)를 켜 두어도 게이트웨이는 8001 자식을 씁니다.)
 *
 * • 이미 `python main.py`만 쓰려면: NO_SPAWN_PYTHON=1
 *   + BACKEND_URL=http://127.0.0.1:8000 (또는 .env 의 PORT)
 */

import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import dotenv from 'dotenv'
import express from 'express'
import { createProxyMiddleware } from 'http-proxy-middleware'
import {
  applyClockDate,
  fetchWeatherFull,
  toWeatherSnapshotBody,
} from './lib/kmaWeather.mjs'

// ── In-memory cache (weather: 10min, tour: 2h) ──────────────────────
const _cache = new Map()
function cacheGet(key, ttlMs) {
  const e = _cache.get(key)
  if (!e) return undefined
  if (Date.now() - e.ts > ttlMs) { _cache.delete(key); return undefined }
  return e.data
}
function cacheSet(key, data) { _cache.set(key, { ts: Date.now(), data }) }

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.resolve(__dirname, '..')

dotenv.config({ path: path.join(repoRoot, '.env') })

const gatewayPort = Number(process.env.STACK_GATEWAY_PORT || 3080)

const noSpawn =
  process.env.NO_SPAWN_PYTHON === '1' || process.env.SPAWN_PYTHON_BACKEND === '0'

/** FastAPI가 바인딩할 포트. 스폰 모드 기본 8001 → 터미널의 main.py(8000)와 분리 */
const pythonBackendPort = Number(
  process.env.PYTHON_BACKEND_PORT || (noSpawn ? process.env.PORT || 8000 : 8001),
)

const backendBase = (
  noSpawn
    ? process.env.BACKEND_URL || `http://127.0.0.1:${pythonBackendPort}`
    : `http://127.0.0.1:${pythonBackendPort}`
).replace(/\/$/, '')

const distDir = path.join(repoRoot, 'frontend', 'dist')
const hasDist = existsSync(path.join(distDir, 'index.html'))

const BUILD_TAG = 'node-gateway-v1'
const tourApiBase = (
  process.env.TOUR_BASE_URL || 'https://apis.data.go.kr/B551011/KorService2'
).replace(/\/$/, '')

const app = express()
let pythonChild = null

function parseOptionalFloat(v) {
  if (v == null || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

app.get('/api/weather-snapshot', async (req, res) => {
  try {
    const city = req.query.city != null ? String(req.query.city) : '전체'
    const cityArg = city === '전체' ? '아산' : city
    const user_lat = parseOptionalFloat(req.query.user_lat)
    const user_lng = parseOptionalFloat(req.query.user_lng)
    const current_time = req.query.current_time != null ? String(req.query.current_time) : null
    const current_date = req.query.current_date != null ? String(req.query.current_date) : null

    // Cache key: city + rounded coords (weather doesn't change per-second)
    const ck = `ws_${cityArg}_${Math.round((user_lat ?? 0) * 100)}_${Math.round((user_lng ?? 0) * 100)}`
    let weather = cacheGet(ck, 10 * 60_000) // 10 min TTL
    if (!weather) {
      weather = await fetchWeatherFull(cityArg, user_lat, user_lng)
      cacheSet(ck, weather)
    }
    weather = applyClockDate(weather, current_time, current_date)
    weather.minute = weather.minute ?? 0
    res.json(toWeatherSnapshotBody(weather))
  } catch (e) {
    console.error('[weather-snapshot]', e)
    res.status(500).json({ detail: String(e?.message || e) })
  }
})

/** Python fetch_weather 브리지용 — 전체 날씨 dict(JSON) */
app.get('/__weather_raw__', async (req, res) => {
  try {
    const city = req.query.city != null ? String(req.query.city) : '아산'
    const user_lat = parseOptionalFloat(req.query.user_lat)
    const user_lng = parseOptionalFloat(req.query.user_lng)

    const ck = `wr_${city}_${Math.round((user_lat ?? 0) * 100)}_${Math.round((user_lng ?? 0) * 100)}`
    let weather = cacheGet(ck, 10 * 60_000)
    if (!weather) {
      weather = await fetchWeatherFull(city, user_lat, user_lng)
      cacheSet(ck, weather)
    }
    weather.minute = weather.minute ?? 0
    res.json(weather)
  } catch (e) {
    console.error('[__weather_raw__]', e)
    res.status(500).json({ detail: String(e?.message || e) })
  }
})

/** Python KorTour 브리지용 — corporate CA/TLS 환경에서는 Node --use-system-ca 쪽이 더 안정적입니다. */
app.get('/__tour_area__', async (req, res) => {
  try {
    const key = process.env.TOUR_API_KEY
    const sigunguCode = req.query.sigunguCode != null ? String(req.query.sigunguCode) : ''
    const num = Math.max(1, Math.min(200, Number(req.query.num || 100)))

    if (!key) {
      res.status(500).json({ detail: 'TOUR_API_KEY missing' })
      return
    }
    if (!sigunguCode) {
      res.status(400).json({ detail: 'sigunguCode is required' })
      return
    }

    // Cache tourism area data for 2 hours (rarely changes)
    const ck = `ta_${sigunguCode}_${num}`
    const cached = cacheGet(ck, 2 * 60 * 60_000)
    if (cached) {
      res.type('application/json; charset=utf-8').send(cached)
      return
    }

    const params = new URLSearchParams({
      serviceKey: key,
      numOfRows: String(num),
      pageNo: '1',
      MobileOS: 'ETC',
      MobileApp: 'ChungnamTour',
      _type: 'json',
      areaCode: '34',
      sigunguCode,
      arrange: 'C',
    })
    const upstream = await fetch(`${tourApiBase}/areaBasedList2?${params}`)
    const text = await upstream.text()
    if (upstream.ok) cacheSet(ck, text)
    res
      .status(upstream.status || 502)
      .type(upstream.headers.get('content-type') || 'application/json; charset=utf-8')
      .send(text)
  } catch (e) {
    console.error('[__tour_area__]', e)
    res.status(502).json({ detail: String(e?.message || e) })
  }
})

app.get('/api/place-photo', async (req, res) => {
  try {
    const key = process.env.GOOGLE_PLACES_KEY
    const name = req.query.name != null ? String(req.query.name) : ''
    const maxHeightPx = Math.max(160, Math.min(1200, Number(req.query.maxHeightPx || 720)))
    if (!key) {
      res.status(404).json({ detail: 'GOOGLE_PLACES_KEY missing' })
      return
    }
    if (!name.startsWith('places/') || !name.includes('/photos/')) {
      res.status(400).json({ detail: 'Invalid Google Places photo name' })
      return
    }
    const root = (process.env.GOOGLE_PLACES_V1_ROOT || 'https://places.googleapis.com/v1').replace(/\/$/, '')
    const url = `${root}/${encodeURI(name)}/media?maxHeightPx=${maxHeightPx}&key=${encodeURIComponent(key)}`
    const upstream = await fetch(url, { redirect: 'follow' })
    if (!upstream.ok || !upstream.body) {
      res.status(upstream.status || 502).json({ detail: 'Google Places photo unavailable' })
      return
    }
    const ct = upstream.headers.get('content-type') || 'image/jpeg'
    res.setHeader('Content-Type', ct)
    res.setHeader('Cache-Control', 'public, max-age=86400')
    const buf = Buffer.from(await upstream.arrayBuffer())
    res.send(buf)
  } catch (e) {
    console.error('[place-photo]', e)
    res.status(502).json({ detail: String(e?.message || e) })
  }
})

/** 관광공사 등 외부 이미지 — Referrer 차단 회피용(허용 호스트만). */
const EXTERNAL_IMAGE_HOST_ALLOWLIST = new Set([
  'tong.visitkorea.or.kr',
  'cdn.visitkorea.or.kr',
  'api.visitkorea.or.kr',
  'visitkorea.or.kr',
  'www.visitkorea.or.kr',
  'korean.visitkorea.or.kr',
])

app.get('/api/proxy-external-image', async (req, res) => {
  try {
    const raw = req.query.url != null ? String(req.query.url) : ''
    if (!raw.startsWith('http://') && !raw.startsWith('https://')) {
      res.status(400).json({ detail: 'url must be http(s)' })
      return
    }
    let u
    try {
      u = new URL(raw)
    } catch {
      res.status(400).json({ detail: 'bad url' })
      return
    }
    if (!EXTERNAL_IMAGE_HOST_ALLOWLIST.has(u.hostname)) {
      res.status(403).json({ detail: 'host not allowed' })
      return
    }
    const upstream = await fetch(raw, {
      redirect: 'follow',
      headers: {
        'User-Agent': 'Mozilla/5.0 (compatible; ChungnamTourGateway/1.0)',
        Referer: 'https://www.visitkorea.or.kr/',
        Accept: 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
      },
    })
    if (!upstream.ok || !upstream.body) {
      res.status(upstream.status || 502).json({ detail: 'upstream image failed' })
      return
    }
    const ct = upstream.headers.get('content-type') || 'application/octet-stream'
    if (!ct.startsWith('image/') && !ct.includes('octet-stream')) {
      res.status(502).json({ detail: 'not an image' })
      return
    }
    res.setHeader('Content-Type', ct.startsWith('image/') ? ct : 'image/jpeg')
    res.setHeader('Cache-Control', 'public, max-age=86400')
    const buf = Buffer.from(await upstream.arrayBuffer())
    res.send(buf)
  } catch (e) {
    console.error('[proxy-external-image]', e)
    res.status(502).json({ detail: String(e?.message || e) })
  }
})

/** Node-side place-reviews bridge — bypasses Python SSL issues with Google Places. */
app.get('/api/place-reviews', async (req, res) => {
  try {
    const key = process.env.GOOGLE_PLACES_KEY
    if (!key) {
      res.json({
        rating: 0, review_count: 0, reviews: [], reviews_shown: 0,
        photo_url: null, website: '', google_maps: '', open_now: null,
        places_status: 'missing_key',
        places_status_message: 'GOOGLE_PLACES_KEY가 설정되지 않았어요.',
      })
      return
    }
    const name = String(req.query.name || '')
    const lat = parseOptionalFloat(req.query.lat)
    const lng = parseOptionalFloat(req.query.lng)
    const address = String(req.query.address || '')
    const topReviews = Math.max(1, Math.min(5, Number(req.query.top_reviews || 3)))
    if (!name || lat == null || lng == null) {
      res.status(400).json({ detail: 'name, lat, lng required' })
      return
    }

    const ck = `pr_${name}_${Math.round(lat * 1000)}_${Math.round(lng * 1000)}_${topReviews}`
    const cached = cacheGet(ck, 30 * 60_000) // 30 min
    if (cached) { res.json(cached); return }

    const root = (process.env.GOOGLE_PLACES_V1_ROOT || 'https://places.googleapis.com/v1').replace(/\/$/, '')
    const cityHint = address ? ` ${address.replace('충청남도 ', '').split(' ')[0] || ''}` : ''
    const textQuery = `${name}${cityHint}`

    async function searchText(payload) {
      const r = await fetch(`${root}/places:searchText`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Goog-Api-Key': key,
          'X-Goog-FieldMask': 'places.id,places.displayName,places.rating,places.userRatingCount,places.reviews,places.photos,places.websiteUri,places.googleMapsUri,places.currentOpeningHours,places.location',
        },
        body: JSON.stringify(payload),
      })
      if (!r.ok) return []
      const j = await r.json()
      return j.places || []
    }

    const circle = (rm) => ({ circle: { center: { latitude: lat, longitude: lng }, radius: rm } })
    const rect = (rm) => {
      const dLat = rm / 111000
      const dLng = rm / (111000 * Math.max(0.01, Math.cos(lat * Math.PI / 180)))
      return { rectangle: { low: { latitude: lat - dLat, longitude: lng - dLng }, high: { latitude: lat + dLat, longitude: lng + dLng } } }
    }
    let places =
      await searchText({ textQuery, languageCode: 'ko', regionCode: 'KR', maxResultCount: 3, locationRestriction: rect(5000) }) ||
      await searchText({ textQuery, languageCode: 'ko', regionCode: 'KR', maxResultCount: 5, locationBias: circle(5000) }) ||
      await searchText({ textQuery, languageCode: 'ko', regionCode: 'KR', maxResultCount: 5, locationBias: circle(12000) }) ||
      await searchText({ textQuery, languageCode: 'ko', regionCode: 'KR', maxResultCount: 5 })
    if (!places || !places.length) {
      const empty = {
        rating: 0, review_count: 0, reviews: [], reviews_shown: topReviews,
        photo_url: null, website: '', google_maps: '', open_now: null,
        places_status: 'no_match', places_status_message: 'Google Places 검색 결과가 비어 있어요.',
      }
      res.json(empty)
      return
    }

    // Pick closest place
    let best = places[0], bestDist = Infinity
    for (const p of places) {
      const loc = p.location || {}
      const pLat = loc.latitude, pLng = loc.longitude
      if (pLat == null || pLng == null) continue
      const d = Math.sqrt((pLat - lat) ** 2 + (pLng - lng) ** 2)
      if (d < bestDist) { bestDist = d; best = p }
    }

    // Place Details for richer reviews
    const pid = best.id || ''
    let detail = null
    if (pid) {
      try {
        const dr = await fetch(`${root}/places/${encodeURIComponent(pid)}`, {
          headers: {
            'X-Goog-Api-Key': key,
            'X-Goog-FieldMask': 'displayName,rating,userRatingCount,reviews,photos,websiteUri,googleMapsUri,currentOpeningHours',
          },
        })
        if (dr.ok) detail = await dr.json()
      } catch { /* ignore */ }
    }

    const src = (detail?.reviews?.length || 0) > (best.reviews?.length || 0) ? detail : best
    const rawRevs = (src?.reviews || [])
      .slice()
      .sort((a, b) => String(b.publishTime || '').localeCompare(String(a.publishTime || '')))
      .slice(0, topReviews)
      .map(r => ({
        author: r.authorAttribution?.displayName || '익명',
        rating: r.rating || 0,
        text: r.text?.text || '',
        relative: r.relativePublishTimeDescription || '',
      }))
      .filter(r => r.text)

    const firstPhoto = (src?.photos || best?.photos || [])[0]?.name
    const photoUrl = firstPhoto?.startsWith('places/') ? `/api/place-photo?name=${encodeURIComponent(firstPhoto)}&maxHeightPx=720` : null
    const openNow = (src?.currentOpeningHours || best?.currentOpeningHours || {}).openNow ?? null

    const result = {
      rating: src?.rating || 0,
      review_count: src?.userRatingCount || 0,
      reviews: rawRevs,
      reviews_shown: topReviews,
      photo_url: photoUrl,
      website: src?.websiteUri || '',
      google_maps: src?.googleMapsUri || '',
      open_now: openNow,
      place_match_distance_m: bestDist < Infinity ? Math.round(bestDist * 111_000) : null,
      places_status: 'ok',
      places_status_message: '',
    }
    cacheSet(ck, result)
    res.json(result)
  } catch (e) {
    console.error('[place-reviews]', e)
    res.status(500).json({ detail: String(e?.message || e) })
  }
})

function backendProxy() {
  const msg = `백엔드(${backendBase})에 연결할 수 없습니다. Python FastAPI가 해당 포트에서 실행 중인지 확인하세요.`
  return createProxyMiddleware({
    target: backendBase,
    changeOrigin: true,
    timeout: 120_000,
    proxyTimeout: 120_000,
    pathFilter: (pathname) =>
      pathname.startsWith('/api') ||
      pathname.startsWith('/docs') ||
      pathname === '/openapi.json' ||
      pathname.startsWith('/redoc') ||
      pathname.startsWith('/legacy'),
    on: {
      error(_err, req, res) {
        const out = res
        if (!out || typeof out.writeHead !== 'function') {
          console.error('[proxy]', req?.method, req?.url, _err?.message || _err)
          return
        }
        if (out.headersSent) {
          console.error('[proxy]', req?.method, req?.url, _err?.message || _err)
          return
        }
        out.writeHead(503, { 'Content-Type': 'application/json; charset=utf-8' })
        out.end(JSON.stringify({ detail: msg }))
      },
    },
  })
}

app.use(backendProxy())

async function probeBackend() {
  try {
    const ac = new AbortController()
    const to = setTimeout(() => ac.abort(), 1500)
    const r = await fetch(`${backendBase}/openapi.json`, { signal: ac.signal })
    clearTimeout(to)
    return r.ok
  } catch {
    return false
  }
}

app.get('/health', async (_req, res) => {
  const backendReachable = await probeBackend()
  res.json({
    ok: true,
    build: BUILD_TAG,
    gatewayPort,
    backend: backendBase,
    backendReachable,
    viteDist: hasDist,
    spawnedPython: Boolean(pythonChild && !pythonChild.killed),
    noSpawn,
  })
})

if (hasDist) {
  app.use(
    express.static(distDir, {
      index: 'index.html',
      fallthrough: true,
    }),
  )
  app.get('*', (req, res, next) => {
    if (req.method !== 'GET') return next()
    res.sendFile(path.join(distDir, 'index.html'), (err) => {
      if (err) next(err)
    })
  })
} else {
  app.get('/', (_req, res) => {
    res.status(503).type('html').send(`<!DOCTYPE html><html lang="ko"><meta charset="utf-8"/>
<title>빌드 필요</title><body style="font-family:system-ui;padding:1.5rem">
<h1>frontend/dist 없음</h1>
<p>루트에서 <code>cd frontend && npm run build</code> 후 <code>npm start</code> 를 다시 실행하세요.</p>
<p>백엔드: FastAPI · 게이트웨이가 날씨 API(Node fetch)를 제공합니다.</p>
<p><a href="/health">/health</a></p>
</body></html>`)
  })
}

function spawnPythonBackend(bridgeUrl) {
  const py = process.env.PYTHON_EXECUTABLE || process.env.PYTHON || 'python'
  console.log(`[gateway] FastAPI 자식 실행: ${py} main.py (PORT=${pythonBackendPort})`)
  console.log(`[gateway] WEATHER_FETCH_URL=${bridgeUrl}`)
  console.log(`[gateway] TOUR_FETCH_URL=${bridgeUrl}`)
  console.log(
    '[gateway] 참고: 별도 터미널의 python main.py(8000)와 무관합니다. /api 는 위 PORT 로 프록시됩니다.',
  )
  const env = {
    ...process.env,
    PORT: String(pythonBackendPort),
    WEATHER_FETCH_URL: bridgeUrl,
    TOUR_FETCH_URL: bridgeUrl,
  }
  const winShell = process.platform === 'win32'
  pythonChild = spawn(py, ['main.py'], {
    cwd: repoRoot,
    env,
    stdio: 'inherit',
    shell: winShell,
  })
  pythonChild.on('error', (err) => {
    console.error('[gateway] FastAPI spawn 오류:', err.message)
    console.error(
      '[gateway] 해결: .env 에 PYTHON_EXECUTABLE=C:\\\\경로\\\\python.exe (Anaconda 등) 를 지정하세요.',
    )
  })
  pythonChild.on('exit', (code, sig) => {
    console.warn(`[gateway] FastAPI 프로세스 종료 code=${code} signal=${sig ?? ''}`)
  })
}

function shutdown() {
  if (pythonChild && !pythonChild.killed) {
    pythonChild.kill('SIGTERM')
  }
  process.exit(0)
}
process.on('SIGINT', shutdown)
process.on('SIGTERM', shutdown)

const gatewayHost = process.env.GATEWAY_HOST || '0.0.0.0'

app.listen(gatewayPort, gatewayHost, () => {
  const bridgeUrl = `http://127.0.0.1:${gatewayPort}`
  const urlHint =
    gatewayHost === '0.0.0.0'
      ? `http://127.0.0.1:${gatewayPort}/ (LAN: 이 PC IP:${gatewayPort})`
      : `http://${gatewayHost}:${gatewayPort}/`
  console.log(`[gateway] UI + API 프록시 ${urlHint}`)
  console.log(`[gateway] 백엔드 프록시 대상 ${backendBase}`)
  console.log(`[gateway] 날씨(Node): /api/weather-snapshot · 브리지 /__weather_raw__`)
  console.log(`[gateway] 관광(Node): KorTour 브리지 /__tour_area__`)

  if (!noSpawn) {
    spawnPythonBackend(bridgeUrl)
  } else {
    console.log('[gateway] NO_SPAWN_PYTHON=1 — FastAPI는 직접 띄워 두세요 (BACKEND_URL).')
  }

  if (!hasDist) console.warn('[gateway] frontend/dist 없음 — SPA 빌드 후 접속하세요.')

  const warnMs = noSpawn ? 800 : 2800
  setTimeout(async () => {
    const ok = await probeBackend()
    if (ok) return
    console.warn(
      `[gateway] 백엔드(${backendBase}) openapi 미응답. ` +
        (noSpawn
          ? 'NO_SPAWN_PYTHON=1 이면 터미널에서 python main.py 가 떠 있어야 합니다.'
          : 'npm start 만 썼다면 자식 FastAPI가 실패했을 수 있습니다. PYTHON_EXECUTABLE·터미널 오류를 확인하세요.'),
    )
  }, warnMs)
})

