"""인스타몬스터 Admin API — 포인트 충전 (payments/add)."""

import logging
import requests
import config

logger = logging.getLogger(__name__)

ADMIN_API_URL = "https://instamonster.co.kr/adminapi/v2"


def add_payment(username, amount, memo="무통장입금 자동충전"):
    """인스타몬스터 유저에게 포인트 충전.

    Args:
        username: 인스타몬스터 username
        amount: 충전 금액 (부가세 제외, 원 단위)
        memo: 결제 메모

    Returns:
        dict: {"ok": True, "payment_id": ..., "balance": ...} 또는 {"ok": False, "error": ...}
    """
    api_key = config.INSTAMONSTER_ADMIN_API_KEY
    if not api_key:
        return {"ok": False, "error": "INSTAMONSTER_ADMIN_API_KEY 미설정"}

    try:
        resp = requests.post(
            f"{ADMIN_API_URL}/payments/add",
            headers={"X-Api-Key": api_key},
            json={
                "username": username,
                "amount": amount,
                "method": "Bonus",
                "memo": memo,
            },
            timeout=30,
        )

        data = resp.json()

        if data.get("error_code") in (200, 0):
            payment_data = data.get("data", {})
            return {
                "ok": True,
                "payment_id": payment_data.get("payment_id"),
                "balance": payment_data.get("user", {}).get("balance"),
            }
        else:
            error_msg = data.get("error_message", f"error_code={data.get('error_code')}")
            logger.error("인스타몬스터 충전 실패: %s (username=%s, amount=%s)", error_msg, username, amount)
            return {"ok": False, "error": error_msg}

    except requests.RequestException as e:
        logger.exception("인스타몬스터 충전 API 요청 실패")
        return {"ok": False, "error": str(e)}


def get_user_info(username):
    """인스타몬스터 유저 정보 조회."""
    api_key = config.INSTAMONSTER_ADMIN_API_KEY
    if not api_key:
        return None

    try:
        resp = requests.get(
            f"{ADMIN_API_URL}/users",
            headers={"X-Api-Key": api_key},
            params={"username": username, "limit": 1},
            timeout=30,
        )
        data = resp.json()
        if data.get("error_code") in (200, 0):
            users = data.get("data", {}).get("list", [])
            return users[0] if users else None
        return None
    except Exception:
        logger.exception("인스타몬스터 유저 조회 실패")
        return None
