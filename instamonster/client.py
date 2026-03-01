import logging
import time

import requests

import config

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


def _api_request(action, params=None):
    """인스타몬스터 API 요청"""
    data = {
        "key": config.INSTAMONSTER_API_KEY,
        "action": action,
    }
    if params:
        data.update(params)

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(config.INSTAMONSTER_API_URL, data=data, timeout=30)
            resp.raise_for_status()
            result = resp.json()

            if "error" in result:
                logger.error("인스타몬스터 API 에러: %s", result["error"])
                return result

            return result

        except requests.RequestException:
            if attempt < MAX_RETRIES - 1:
                wait = 2 ** attempt
                logger.warning(
                    "인스타몬스터 API 요청 실패 - %d초 후 재시도 (%d/%d)",
                    wait, attempt + 1, MAX_RETRIES,
                )
                time.sleep(wait)
            else:
                raise

    return None


def get_services():
    """서비스 목록 조회"""
    return _api_request("services")


def add_order(service_id, link, quantity):
    """주문 추가"""
    result = _api_request("add", {
        "service": service_id,
        "link": link,
        "quantity": quantity,
    })
    return result


def add_subscription_order(service_id, username, min_qty, max_qty, posts=0, old_posts=0, delay=0):
    """구독(자동) 주문 추가

    새 게시물이 올라올 때마다 자동으로 좋아요/팔로워 등을 추가
    posts: 앞으로 올릴 게시물 수 (0=무제한, 갯수 채워지면 종료)
    """
    result = _api_request("add", {
        "service": service_id,
        "username": username,
        "min": min_qty,
        "max": max_qty,
        "posts": posts,
        "old_posts": old_posts,
        "delay": delay,
    })
    return result


def get_order_status(order_id):
    """주문 상태 조회"""
    return _api_request("status", {"order": order_id})


def get_balance():
    """잔액 조회"""
    result = _api_request("balance")
    if result and "balance" in result:
        return float(result["balance"])
    return None
