# Render 배포 메모

이 프로젝트는 Docker 배포 기준으로 준비되어 있습니다.

## Render 설정

1. GitHub에 `Chungnam_Competition` 폴더 내용을 저장소 루트로 올립니다.
   - 상위 `Chungnam_Weather-Tour` 전체를 올릴 경우 Render의 Root Directory를 `Chungnam_Competition`으로 설정합니다.
2. Render에서 **New > Blueprint** 또는 **New > Web Service**를 선택합니다.
3. `render.yaml` 또는 `Dockerfile`을 사용해 배포합니다.
4. 환경 변수에 실제 키를 추가합니다.

필수/권장 환경 변수:

- `TOUR_API_KEY`
- `WEATHER_API_KEY`
- `AIR_KOREA_API_KEY`
- `GOOGLE_PLACES_KEY`
- `PYTHON=python3`

배포 후 Render URL 예시:

```text
https://tteonago.onrender.com
```

해당 URL이 열리고 `/health`가 `ok: true`를 반환하면 APK 빌드 시 `VITE_API_BASE_URL`에 그 URL을 넣으면 됩니다.
