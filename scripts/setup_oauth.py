"""
카페24 OAuth 최초 인증 스크립트

사용법:
    python scripts/setup_oauth.py

1. 브라우저에서 카페24 인증 페이지가 열립니다.
2. 카페24에 로그인하고 권한을 승인합니다.
3. 리다이렉트된 URL에서 code 파라미터를 복사합니다.
4. 터미널에 붙여넣으면 토큰이 발급되어 DB에 저장됩니다.

또는 Flask 서버를 실행한 상태에서 /admin/setup 페이지에서 인증할 수도 있습니다.
"""

import sys
import os
import webbrowser

# 프로젝트 루트를 path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from cafe24.auth import get_authorization_url, exchange_code, save_token


def main():
    app = create_app()

    auth_url = get_authorization_url()
    print("=" * 60)
    print("카페24 OAuth 인증")
    print("=" * 60)
    print()
    print("브라우저에서 아래 URL로 이동하여 인증하세요:")
    print(auth_url)
    print()

    # 브라우저 자동 열기 시도
    try:
        webbrowser.open(auth_url)
        print("(브라우저가 자동으로 열렸습니다)")
    except Exception:
        print("(브라우저를 수동으로 열어주세요)")

    print()
    print("인증 완료 후 리다이렉트된 URL에서 'code=' 파라미터 값을 복사하세요.")
    print("예: http://localhost:5000/oauth/callback?code=XXXXXX")
    print()

    code = input("인증 코드를 입력하세요: ").strip()

    if not code:
        print("인증 코드가 입력되지 않았습니다.")
        sys.exit(1)

    # URL 전체가 입력된 경우 code 파라미터 추출
    if "code=" in code:
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(code)
        params = parse_qs(parsed.query)
        code = params.get("code", [code])[0]

    with app.app_context():
        try:
            token_data = exchange_code(code)
            save_token(token_data)
            print()
            print("인증 성공! 토큰이 DB에 저장되었습니다.")
            print(f"  Access Token 만료: {token_data.get('expires_in', 7200)}초")
            print(f"  Refresh Token 만료: {token_data.get('refresh_token_expires_in', 1209600)}초")
        except Exception as e:
            print(f"인증 실패: {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
