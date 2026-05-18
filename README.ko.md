# Grok OAuth Proxy

[English](README.md) | [한국어](README.ko.md)

**Hermes Agent**의 OAuth 세션을 재사용하여 [xAI Grok API](https://docs.x.ai/)를 로컬에서 프록시하는 경량 리버스 프록시입니다. 별도의 API 키 없이, 이미 `~/.hermes/auth.json`에 저장된 브라우저 기반 OAuth 토큰을 그대로 활용합니다.

[FastAPI](https://fastapi.tiangolo.com/)로 작성되었으며, LiteLLM이나 OpenAI Python SDK 등 OpenAI-compatible 포맷을 사용하는 어떤 클라이언트와도 바로 연동됩니다.

---

## 주요 기능

- **Zero-config OAuth** — Hermes Agent의 xAI OAuth 토큰을 자동으로 복사 및 관리
- **독립적인 토큰 생명주기** — Hermes와의 race 없이 자체 refresh 루프를 운영
- **Token prewarm** — 토큰 만료 전 백그라운드에서 미리 갱신하여 API 호출 지연 제거
- **Hermes auth.json 감시** — Hermes에서 재인증(새 로그인) 시 변경을 감지하여 자동 재복사
- **Streaming 완벽 지원** — `/v1/chat/completions`의 SSE 스트리밍을 그대로 전달
- **Upstream retry** — `GET`, `HEAD`, `OPTIONS`, `TRACE` 같은 idempotent 요청만 502/503/429 및 일시적 연결 오류에서 재시도하고, 모델 생성 `POST`는 중복 과금/부작용 방지를 위해 재시도하지 않음
- **Prometheus metrics** — 요청 수, 지연 시간, 토큰 만료 시각을 `/metrics`에서 제공
- **Deep health check** — `/health?deep=1`에서 실제 `api.x.ai`를 핑하여 end-to-end 연결 상태 확인
- **안전한 파일 권한** — 로컬 토큰 복사본을 `0o600` 권한으로 저장

---

## 아키텍처

```
┌─────────────────┐     HTTP      ┌──────────────────────┐     HTTPS + Bearer    ┌─────────────┐
│   클라이언트     │ ─────────────>│  Grok OAuth Proxy    │ ─────────────────────>│  api.x.ai   │
│ (LiteLLM 등)    │  OpenAI 포맷  │  (127.0.0.1:9996)    │   OAuth 토큰 주입      │   (xAI)     │
└─────────────────┘               └──────────────────────┘                       └─────────────┘
                                           │
                                           │ 읽기 / 갱신
                                           ▼
                                    ┌──────────────┐
                                    │ auth_state   │
                                    │ .json        │  (Hermes로부터 복사, 0o600)
                                    └──────────────┘
```

1. 시작 시 먼저 Hermes CLI가 설치되어 있는지 확인합니다.
2. 이어서 `~/.hermes/auth.json`에 `xai-oauth` 인증 정보가 있는지 확인합니다.
3. Hermes 인증에서 OAuth 토큰과 공개 `client_id` claim을 복사하여 로컬 `auth_state.json`을 생성합니다.
4. 이후 모든 토큰 갱신은 가져온 client id를 사용해 `https://auth.x.ai/oauth2/token`에 대해 독립적으로 수행됩니다.
5. 들어오는 요청은 현재 Bearer 토큰을 주입하여 `https://api.x.ai/v1/*`로 전달합니다.

---

## 설치

### 빠른 설치

```bash
git clone https://github.com/logicrw/grok-oauth-proxy.git
cd grok-oauth-proxy

# 데스크톱
./install.sh

# 헤드리스 서버
./install.sh --headless

# 헤드리스 + systemd 서비스 자동 활성화
./install.sh --headless --enable-service
```

### 수동 설치

#### 사전 준비

- Python 3.9+
- [Hermes Agent](https://github.com/NousResearch/hermes-agent) 설치 및 설정 완료
- Hermes에서 xAI Grok OAuth 인증 완료 (`hermes model` → *xAI Grok OAuth* 선택)

```bash
git clone https://github.com/logicrw/grok-oauth-proxy.git
cd grok-oauth-proxy
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

---


## 헤드리스 서버 설정

**빠른 설치 (권장):**
```bash
git clone https://github.com/logicrw/grok-oauth-proxy.git
cd grok-oauth-proxy
./install.sh --headless --enable-service
```

이 프로젝트는 브라우저를 열 수 없는 헤드리스 서버(Oracle Cloud, VPS, 컨테이너 등)에서도 동작하도록 설계되었습니다.

### 권장 토큰 소유권 흐름

장시간 안정적으로 운용하려면 Hermes와 프록시가 같은 refresh token chain을
계속 공유하지 말고, 프록시에 별도 체인을 넘긴 뒤 Hermes를 다시 인증하는
방식을 권장합니다.

```text
Hermes 로컬 OAuth 인증
→ 생성된 xAI OAuth refresh-token chain을 grok-oauth-proxy로 전달
  (로컬 프록시든 헤드리스 서버든 실제 proxy가 도는 곳)
→ Hermes 로컬 재인증
→ Hermes와 grok-oauth-proxy가 서로 독립적으로 refresh
```

이유: xAI/Grok access token은 짧고, 실측상 refresh token은 refresh 시
rotation됩니다. 먼저 전달한 체인은 프록시가 계속 소유하고, Hermes를 다시
인증하면 데스크톱 Hermes는 별도 체인을 갖게 됩니다. 이후 xAI 정책이 바뀌어
동시 체인이 막히면, 단일 active owner 원칙으로 돌아가고 Hermes 재인증 때마다
`refresh_remote_xai_oauth.py`를 다시 실행하면 됩니다.

### 추천 흐름

1. **브라우저가 있는 PC**에서:
   - Hermes 설치
   - `hermes model` 실행 후 xAI Grok OAuth 로그인 완료
   - `xai-oauth`가 있는지 확인

2. **xAI OAuth 인증 정보만 서버로 복사** (권장)

   브라우저 있는 PC에서:
   ```bash
   cd grok-oauth-proxy
   python scripts/export_xai_oauth.py > ~/xai-oauth.json
   ```

   서버로 복사:
   ```bash
   scp ~/xai-oauth.json user@your-server:/tmp/xai-oauth.json
   ```

   서버에서 import:
   ```bash
   python scripts/import_xai_oauth.py /tmp/xai-oauth.json
   rm -f /tmp/xai-oauth.json
   chmod 700 ~/.hermes
   chmod 600 ~/.hermes/auth.json
   sudo systemctl restart grok-oauth-proxy
   ```

   기본적으로 `import_xai_oauth.py`는 프록시의 오래된 로컬
   `auth_state.json`도 제거합니다. 그래서 다음 재시작 때 새 Hermes 인증
   정보에서 다시 토큰 상태를 만듭니다. 실행 중인 프록시 토큰 상태를 일부러
   유지해야 할 때만 `--no-reset-proxy-state`를 사용하세요.

   브라우저가 있는 PC에서 원격 헤드리스 서버를 한 번에 갱신할 수도 있습니다:
   ```bash
   python scripts/refresh_remote_xai_oauth.py \
     --host user@example.com \
     --identity ~/.ssh/id_ed25519 \
     --print-reauth-command
   ```

   이 헬퍼는 `xai-oauth`만 export하고, SSH로 복사한 뒤 서버에서 import,
   stale proxy token state 제거, `grok-oauth-proxy` 재시작, deep health check까지
   한 번에 수행합니다. `--print-reauth-command`를 붙이면 권장 split-chain
   흐름의 마지막 단계인 Hermes 재인증 명령도 함께 출력합니다.

3. **헤드리스 서버에서 실행**

   ```bash
   # Hermes CLI 설치 (필수)
   curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash

   # 프록시 설치 및 실행
   git clone https://github.com/logicrw/grok-oauth-proxy.git
   cd grok-oauth-proxy
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   python main.py
   ```

---

## 빠른 시작

```bash
source .venv/bin/activate
python main.py
```

프록시가 `http://127.0.0.1:9996`에서 시작됩니다. 해당 포트가 사용 중이면 +1씩 스캔하여 자동으로 빈 포트를 찾습니다.

### 동작 확인

```bash
curl http://127.0.0.1:9996/health
```

```bash
curl http://127.0.0.1:9996/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "grok-4.3",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

---

## 설정

### 헤드리스 systemd 참고

systemd 서비스는 대화형 shell 환경을 그대로 상속하지 않을 수 있습니다. `./install.sh --enable-service`는 서비스 파일에 `HOME`, `HERMES_AUTH_PATH`, 그리고 프로젝트 venv와 `~/.local/bin`을 포함한 `PATH`를 명시하여 헤드리스 서버에서도 Hermes CLI를 찾을 수 있게 합니다.

예시:

```ini
Environment=HOME=/home/youruser
Environment=HERMES_AUTH_PATH=/home/youruser/.hermes/auth.json
Environment=PATH=/home/youruser/grok-oauth-proxy/.venv/bin:/home/youruser/.local/bin:/usr/local/bin:/usr/bin:/bin
```

---

모든 설정은 선택사항이며, 환경변수를 통해 조정할 수 있습니다.

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `PROXY_HOST` | `127.0.0.1` | 바인딩 주소. loopback이 아닌 주소는 `PROXY_API_KEY` 필요 |
| `PROXY_PORT` | `9996` | 기본 포트. 사용 중이면 +1씩 최대 20회 스캔 |
| `PROXY_API_KEY` | 미설정 | 선택적 로컬 프록시 인증 키. 외부 바인딩 시 필수. `Authorization: Bearer ***` 또는 `X-Proxy-Api-Key: <key>` 허용 |
| `GROK_PROXY_AUTH_STATE` | `~/.local/state/grok-oauth-proxy/auth_state.json` | 프록시가 소유하는 로컬 토큰 상태 파일 경로 |
| `LOG_LEVEL` | `INFO` | 로그 레벨: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `HERMES_AUTH_PATH` | `~/.hermes/auth.json` | Hermes 인증 파일 경로 |
| `TOKEN_REFRESH_WINDOW` | `300` | 만료 몇 초 전에 백그라운드에서 미리 갱신할지 |
| `HERMES_POLL_INTERVAL` | `60` | Hermes auth.json 변경 체크 주기 (초) |
| `UPSTREAM_RETRY_ATTEMPTS` | `2` | idempotent upstream 요청(`GET`, `HEAD`, `OPTIONS`, `TRACE`)의 최대 시도 횟수. 모델 생성 `POST`는 502/503/429에서 재시도하지 않아 중복 과금/부작용을 피합니다. 단, 401 token-refresh 재시도는 1회 수행됩니다. |
| `UPSTREAM_RETRY_DELAY` | `1.0` | 재시도 간 기본 대기 시간 (초) |

### 예시

```bash
PROXY_PORT=8080 LOG_LEVEL=DEBUG python main.py
```

---

## API 엔드포인트

이 프록시는 path-transparent 구조입니다. `/{path:path}`로 들어온 요청은 현재 Hermes `xai-oauth` Bearer 토큰을 주입해 `https://api.x.ai/{path}`로 그대로 전달합니다. 따라서 채팅뿐 아니라 Hermes xAI Grok OAuth 문서의 direct-to-xAI 기능들도 같은 토큰으로 활용할 수 있습니다.

대표 xAI surface:

| 기능 | 예시 path | 비고 |
|------|-----------|------|
| Chat / Responses 호환 클라이언트 | `/v1/chat/completions`, `/v1/responses` | 일반/스트리밍 요청 지원. 클라이언트 `Authorization`은 제거 후 프록시 토큰으로 교체됩니다. |
| Models | `/v1/models` | deep health 및 모델 조회에 사용 |
| TTS | `/v1/tts` | upstream 계정/엔드포인트가 허용하면 동일 OAuth 토큰으로 전달 |
| 이미지 생성 | `/v1/images/generations` 또는 xAI 이미지 엔드포인트 | path-transparent forwarding으로 비채팅 기능 유지 |
| 비디오 생성 | xAI Grok Imagine 비디오 엔드포인트 | 대형/스트리밍 응답도 그대로 스트리밍 |
| Transcription / audio | xAI audio 엔드포인트 | 그대로 전달 |
| X Search via Responses | xAI search tool을 포함한 `/v1/responses` 요청 | 계정/provider가 지원하는 경우 일반 Responses 요청처럼 동작 |

로컬 관리 엔드포인트:

| 엔드포인트 | 메소드 | 설명 |
|-----------|--------|------|
| `/{path:path}` | Any | `https://api.x.ai/{path}`로 프록시 |
| `/health` | `GET` | 프록시 상태 및 토큰 만료 시각 |
| `/health?deep=1` | `GET` | Deep health: 실제 `api.x.ai/v1/models`를 호출하여 검증 |
| `/metrics` | `GET` | Prometheus-compatible 메트릭스 |

### Health 응답 예시

```json
{
  "status": "ok",
  "provider": "xai-oauth",
  "api_base": "https://api.x.ai",
  "token_expires_at": "2026-05-17T11:46:33Z",
  "token_endpoint": "https://auth.x.ai/oauth2/token"
}
```

---

## LiteLLM 연동

```yaml
model_list:
  - model_name: grok-4.3
    litellm_params:
      model: openai/grok-4.3
      api_base: http://127.0.0.1:9996
      api_key: "dummy"  # 프록시가 실제 OAuth Bearer 토큰을 주입하므로 아무 값
```

---

## 작동 원리

### 토큰 격리

Hermes와 프록시는 같은 xAI 계정과 OAuth client identity를 사용하지만, **같은 토큰 상태 파일을 공유하지 않습니다**:

- Hermes가 관리하는 파일: `~/.hermes/auth.json`
- 프록시가 관리하는 파일: `~/.local/state/grok-oauth-proxy/auth_state.json` 기본값 (최초 시작 시 생성, `chmod 600`; `GROK_PROXY_AUTH_STATE`로 변경 가능)
- 프록시는 `XAI_CLIENT_ID` 상수를 배포하지 않습니다. 최초 시작 및 Hermes 재인증 후 재복사 시 Hermes token claim(`client_id`/`aud`)에서 공개 client id를 가져옵니다.

이 설계로 인해:
- Hermes가 토큰을 갱신할 때 프록시 세션이 무효화되지 않습니다.
- 프록시가 토큰을 갱신할 때 Hermes 세션이 망가지지 않습니다.
- Hermes에서 새로 로그인하면 백그라운드 감시 태스크가 변경을 감지하여 최신 토큰을 재복사합니다.

### 백그라운드 태스크

프록시 실행 중 두 개의 `asyncio` 태스크가 계속 동작합니다:

1. **Token Prewarm Watcher** — `TOKEN_REFRESH_WINDOW / 2` 초마다 토큰 만료를 체크합니다. 만료가 임박하면 백그라운드에서 먼저 갱신하여, 실제 API 호출 시 stale token으로 인한 지연/실패를 방지합니다.
2. **Hermes File Watcher** — `HERMES_POLL_INTERVAL` 초마다 `~/.hermes/auth.json`의 `mtime`을 체크합니다. 파일이 변경되면 최신 `xai-oauth` credential을 재가져와 로컬 파일을 덮어씁니다.

---

## 보안 참고사항

- 프록시는 기본적으로 `127.0.0.1`에만 바인딩됩니다. `PROXY_HOST=0.0.0.0`처럼 loopback이 아닌 주소로 바인딩하려면 `PROXY_API_KEY`가 반드시 필요하며, 없으면 시작을 거부합니다.
- `PROXY_API_KEY`가 설정된 경우 프록시 요청은 `Authorization: Bearer <key>` 또는 `X-Proxy-Api-Key: <key>`를 포함해야 합니다. 이 클라이언트 인증값은 upstream 전달 전에 제거되고, 프록시가 자체 xAI OAuth Bearer 토큰을 주입합니다.
- Hop-by-hop 헤더, 클라이언트 credential(`Authorization`, `Proxy-Authorization`, `Connection`, `TE` 등), 쿠키, spoof 가능한 forwarding 헤더(`Forwarded`, `X-Forwarded-*`, `X-Real-IP`)는 `api.x.ai`로 전달하기 전에 제거됩니다.
- 프록시가 로컬 토큰 상태 디렉터리를 새로 만들 때는 `0o700` 권한으로 생성하고, `auth_state.json`은 atomic write와 `0o600` 권한으로 저장합니다. 기존 token-state 파일도 읽기 전 가능한 경우 권한을 보정합니다.
- query string 로그 유출을 줄이기 위해 Uvicorn access log는 기본 비활성화하며, 앱 로그는 method/path/status만 기록합니다.
- 프록시는 Hermes가 xAI Grok OAuth 로그인 중 획득한 OAuth `client_id`를 사용합니다. 이 client id는 배포용 소스에 하드코딩하지 않고, 실행 시 로컬 Hermes 인증 상태에서 가져옵니다. 이는 기술적으로 third-party 클라이언트 재사용이며, xAI의 이용약관(ToS)과의 관계에서 사용자의 책임 하에 사용하시기 바랍니다.

---

## 개발

```bash
source .venv/bin/activate
python main.py
```

### 테스트

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

### 백그라운드 실행

```bash
nohup python main.py > proxy.log 2>&1 &
```

### 프로젝트 구조

```
grok-oauth-proxy/
├── main.py           # FastAPI 앱, 프록시 로직, 백그라운드 감시
├── token_manager.py  # Async-safe OAuth 토큰 읽기/갱신
├── config.py         # 환경변수 설정
├── requirements.txt
└── README.ko.md
```

---

## 라이선스

MIT
