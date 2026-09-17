# AI OnAd Rawdata Collector (Vercel)

로그인해서 들어가는 웹페이지 하나로 다음을 처리한다.

- 매일 자동으로 D-1 원시 로그 zip을 CMS에서 받아 Vercel Blob(private)에 저장 (Cron)
- 최근 45일 수집 현황을 표로 확인
- 비어 있는 날짜는 버튼으로 그 자리에서 재수집
- 완료된 날짜는 브라우저에서 바로 zip 다운로드

## 요금제

SKT 업무용이므로 Vercel Pro(팀) 플랜 기준. Hobby는 정책상 개인/비상업 용도이고
함수 실행시간도 60초로 짧아 zip 다운로드+업로드에 빠듯하다.

## 구조 (2026-09 Vercel Python 런타임 기준)

Vercel의 Python 런타임은 `api/` 폴더에 파일을 여러 개 두고 각각 별도 함수로 자동 인식하던
예전 방식을 더 이상 지원하지 않는다. 대신 `app.py`/`index.py`/`main.py` 같은 정해진 파일명
하나에 Flask(또는 FastAPI) 앱을 두고 그 안에서 라우팅하는 걸 표준으로 요구하며, 배포되면
**하나의 Vercel Function**이 된다. 그래서 이 프로젝트는 `api/index.py` 하나에 모든 라우트가
들어있다.

```
public/index.html    로그인 + 수집 현황 대시보드 (정적 파일 — public/ 밑에 둬야 인식됨)
api/index.py          Flask 앱 하나 — /api/login, /api/status, /api/download, /api/collect
vercel.json            함수 maxDuration + cron 스케줄
requirements.txt       flask, requests, vercel_blob
.env.example           필요한 환경변수 목록
pipeline/pull_from_blob.py  (선택) 로컬에서 한꺼번에 받고 싶을 때만
```

## 1) 배포

```bash
npm i -g vercel        # 최초 1회
cd vercel-aionad-collector
vercel link             # Vercel 팀/프로젝트에 연결
```

Vercel 대시보드 > 해당 프로젝트 > Storage 탭에서 **Blob 스토어를 Private로 생성**하고
이 프로젝트에 연결한다. (`BLOB_READ_WRITE_TOKEN`이 자동으로 환경변수에 추가됨)

환경변수 등록 (`.env.example` 참고):

```bash
vercel env add CMS_USERNAME production
vercel env add CMS_PASSWORD production
vercel env add CRON_SECRET production      # 아무 긴 랜덤 문자열
vercel env add ADMIN_PASSWORD production   # 웹페이지 로그인 비밀번호
vercel env add SESSION_SECRET production   # 세션 서명용 — CRON_SECRET과 다른 값으로
```

배포:

```bash
vercel --prod
```

GitHub 연동으로 배포한 경우엔 위 `vercel env add` 대신 Vercel 대시보드의
Project Settings > Environment Variables 에서 값 5개(CMS_USERNAME/CMS_PASSWORD/
CRON_SECRET/ADMIN_PASSWORD/SESSION_SECRET)를 입력하면 된다. Blob 스토어를 연결한
뒤에는 새 환경변수가 적용되도록 **한 번 Redeploy**가 필요하다.

## 2) 사용

`https://<프로젝트>.vercel.app` 접속 → `ADMIN_PASSWORD`로 로그인 → 대시보드.

- **완료**: 초록색, 다운로드 링크 활성화
- **없음**: 회색, "수집" 버튼으로 그 날짜만 즉시 재수집
- **지금 수집(D-1)**: 어제 날짜를 즉시 수집 (크론을 기다리지 않고 바로 테스트하고 싶을 때)

## 3) Cron

`vercel.json`에 `0 22 * * *` (UTC) = KST 07:00 로 등록해 두었다.
Vercel 대시보드 > 프로젝트 > Cron Jobs 탭에서 다음 실행 예정 시각과 최근 실행 로그를 볼 수 있다.

**주의** — Vercel Cron은 실패해도 재시도나 알림이 없다. 당분간은 이 웹페이지를 가끔 열어
빈 날짜(회색 "없음")가 쌓여 있지 않은지 확인하는 걸 권한다.

## 인증 구조

- **Cron 호출**: `Authorization: Bearer <CRON_SECRET>` — Vercel이 자동으로 붙여준다.
- **웹페이지**: `ADMIN_PASSWORD`로 로그인하면 HMAC 서명된 세션 쿠키(7일 유효)가 발급되고,
  이후 모든 보호된 라우트는 이 쿠키로 인증한다. DB 없이 `SESSION_SECRET`으로 서명·검증만
  하는 방식이라, 내부 소수 인원용 도구 수준의 보안이다. 더 강한 보안이 필요하면 Vercel의
  Deployment Protection(SSO)을 프로젝트 앞단에 추가로 걸 수 있다.

## 알려진 제약

- `vercel_blob`은 Vercel 공식 SDK가 아니라 커뮤니티 패키지다.
- `maxDuration`을 300초로 잡았다. 멀티사이트 확장으로 하루치 zip이 더 커지면(현재 이마트
  단일 사이트 기준 약 60MB) 이 값을 늘리거나 Fluid Compute를 켜야 할 수 있다.
- CMS 로그인 계정/비밀번호가 Vercel 프로젝트 환경변수로 들어가므로, 지금 로컬 PC의
  Windows DPAPI 암호화보다는 접근 범위가 넓어진다(해당 프로젝트 권한이 있는 팀원 전체).
  가능하면 조회 전용 서브계정을 쓰는 걸 권한다.
