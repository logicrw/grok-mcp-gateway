# Helper Scripts

이 디렉토리에는 grok-oauth-proxy의 편의용 스크립트들이 있습니다.

## export_xai_oauth.py

Hermes의 `~/.hermes/auth.json`에서 **xai-oauth** 정보만 추출합니다.

```bash
python scripts/export_xai_oauth.py > ~/xai-oauth.json
```

생성된 파일은 refresh token을 포함하므로 취급에 주의하세요.

## import_xai_oauth.py

헤드리스 서버에서 export된 파일을 Hermes에 import합니다.

```bash
python scripts/import_xai_oauth.py /tmp/xai-oauth.json
```

- 기존 다른 provider는 유지합니다.
- import 후 `~/.hermes/auth.json`에 `chmod 600`을 적용합니다.
- 기존 파일은 `.bak`으로 백업됩니다.

## 사용 예시 (헤드리스 환경)

**브라우저 있는 PC:**
```bash
python scripts/export_xai_oauth.py > ~/xai-oauth.json
scp ~/xai-oauth.json user@server:/tmp/
```

**헤드리스 서버:**
```bash
python scripts/import_xai_oauth.py /tmp/xai-oauth.json
rm /tmp/xai-oauth.json
```

이제 `python main.py`로 프록시를 시작하면 Hermes의 최신 xAI OAuth 토큰을 사용합니다.