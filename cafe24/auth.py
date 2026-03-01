import logging
import time
import base64
from datetime import datetime, timezone, timedelta

import requests
from flask import Blueprint, request, redirect, url_for, flash

from models import db, OAuthToken
import config
from services.telegram_notifier import notify_token_expiring, notify_token_expired

logger = logging.getLogger(__name__)

MAX_REFRESH_RETRIES = 3

# 토큰 만료 임박 알림 중복 방지 (날짜 기준)
_last_expiry_alert_date = None

oauth_bp = Blueprint("oauth", __name__)

TOKEN_URL = f"https://{config.CAFE24_MALL_ID}.cafe24api.com/api/v2/oauth/token"
AUTH_URL = f"https://{config.CAFE24_MALL_ID}.cafe24api.com/api/v2/oauth/authorize"


def _basic_auth_header():
    credentials = f"{config.CAFE24_CLIENT_ID}:{config.CAFE24_CLIENT_SECRET}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return {"Authorization": f"Basic {encoded}", "Content-Type": "application/x-www-form-urlencoded"}


def get_authorization_url():
    params = {
        "response_type": "code",
        "client_id": config.CAFE24_CLIENT_ID,
        "redirect_uri": config.CAFE24_REDIRECT_URI,
        "scope": "mall.read_order,mall.write_order,mall.read_product",
    }
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{AUTH_URL}?{qs}"


def exchange_code(code):
    """인증 코드를 access token으로 교환"""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.CAFE24_REDIRECT_URI,
    }
    resp = requests.post(TOKEN_URL, headers=_basic_auth_header(), data=data, timeout=30)
    resp.raise_for_status()
    return resp.json()


def refresh_access_token(refresh_token):
    """리프레시 토큰으로 새 액세스 토큰 발급"""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    resp = requests.post(TOKEN_URL, headers=_basic_auth_header(), data=data, timeout=30)
    resp.raise_for_status()
    return resp.json()


def save_token(token_data):
    """토큰 데이터를 DB에 저장"""
    now = datetime.now(timezone.utc)
    expires_in = token_data.get("expires_in", 7200)
    # 카페24: refresh token은 2주
    refresh_expires_in = token_data.get("refresh_token_expires_in", 1209600)

    token = OAuthToken.query.first()
    if token is None:
        token = OAuthToken(
            access_token=token_data["access_token"],
            refresh_token=token_data["refresh_token"],
            expires_at=now + timedelta(seconds=expires_in),
            refresh_expires_at=now + timedelta(seconds=refresh_expires_in),
        )
        db.session.add(token)
    else:
        token.access_token = token_data["access_token"]
        token.refresh_token = token_data["refresh_token"]
        token.expires_at = now + timedelta(seconds=expires_in)
        token.refresh_expires_at = now + timedelta(seconds=refresh_expires_in)

    db.session.commit()
    logger.info("토큰 저장 완료 (만료: %s)", token.expires_at)
    return token


def get_valid_token():
    """유효한 액세스 토큰을 반환 (필요 시 자동 갱신).

    - Refresh 토큰 만료 3일 전부터 텔레그램 경고 (1일 1회)
    - Refresh 토큰 만료 시 긴급 알림
    - Access 토큰 갱신 실패 시 최대 3회 재시도
    """
    global _last_expiry_alert_date

    token = OAuthToken.query.first()
    if token is None:
        logger.error("저장된 토큰 없음 - OAuth 인증 필요")
        notify_token_expired("카페24")
        return None

    now = datetime.now(timezone.utc)

    # naive datetime → aware datetime 변환 (SQLite 호환)
    expires_at = token.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    refresh_expires_at = token.refresh_expires_at
    if refresh_expires_at.tzinfo is None:
        refresh_expires_at = refresh_expires_at.replace(tzinfo=timezone.utc)

    # ── Refresh 토큰 만료 임박 경고 (3일 전부터, 1일 1회) ──
    days_left = (refresh_expires_at - now).days
    today_str = now.strftime("%Y-%m-%d")
    if 0 < days_left <= 3 and _last_expiry_alert_date != today_str:
        _last_expiry_alert_date = today_str
        logger.warning("카페24 Refresh 토큰 만료 %d일 전", days_left)
        notify_token_expiring("카페24", days_left)

    # 만료 10분 전에 갱신
    if expires_at <= now + timedelta(minutes=10):
        # 리프레시 토큰도 만료되었으면 재인증 필요
        if refresh_expires_at <= now:
            logger.error("리프레시 토큰 만료 - 재인증 필요")
            notify_token_expired("카페24")
            return None

        logger.info("액세스 토큰 갱신 중...")
        last_error = None
        for attempt in range(MAX_REFRESH_RETRIES):
            try:
                token_data = refresh_access_token(token.refresh_token)
                token = save_token(token_data)
                if attempt > 0:
                    logger.info("토큰 갱신 성공 (재시도 %d회)", attempt)
                return token.access_token
            except requests.RequestException as e:
                last_error = e
                logger.warning("토큰 갱신 실패 (%d/%d): %s",
                               attempt + 1, MAX_REFRESH_RETRIES, e)
                if attempt < MAX_REFRESH_RETRIES - 1:
                    time.sleep(2 ** attempt)

        # 모든 재시도 실패
        logger.error("토큰 갱신 최종 실패: %s", last_error)
        notify_token_expired("카페24")
        return None

    return token.access_token


def force_refresh_token():
    """토큰을 강제로 갱신한다. 401 응답 시 호출용.

    Returns:
        str: 새 access_token 또는 None
    """
    token = OAuthToken.query.first()
    if token is None:
        return None

    now = datetime.now(timezone.utc)
    refresh_expires_at = token.refresh_expires_at
    if refresh_expires_at.tzinfo is None:
        refresh_expires_at = refresh_expires_at.replace(tzinfo=timezone.utc)

    if refresh_expires_at <= now:
        logger.error("강제 갱신 실패 - 리프레시 토큰 만료")
        notify_token_expired("카페24")
        return None

    for attempt in range(MAX_REFRESH_RETRIES):
        try:
            token_data = refresh_access_token(token.refresh_token)
            token = save_token(token_data)
            logger.info("토큰 강제 갱신 성공")
            return token.access_token
        except requests.RequestException as e:
            logger.warning("토큰 강제 갱신 실패 (%d/%d): %s",
                           attempt + 1, MAX_REFRESH_RETRIES, e)
            if attempt < MAX_REFRESH_RETRIES - 1:
                time.sleep(2 ** attempt)

    notify_token_expired("카페24")
    return None


@oauth_bp.route("/oauth/callback")
def oauth_callback():
    """카페24 OAuth 콜백"""
    code = request.args.get("code")
    error = request.args.get("error")

    if error:
        flash(f"OAuth 인증 실패: {error}", "danger")
        return redirect(url_for("admin.setup"))

    if not code:
        flash("인증 코드가 없습니다.", "danger")
        return redirect(url_for("admin.setup"))

    try:
        token_data = exchange_code(code)
        save_token(token_data)
        flash("카페24 OAuth 인증 완료!", "success")
    except requests.RequestException as e:
        flash(f"토큰 교환 실패: {e}", "danger")

    return redirect(url_for("admin.setup"))
