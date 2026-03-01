import logging
import time
from datetime import datetime, timezone, timedelta

import requests

from cafe24.auth import get_valid_token
import config

logger = logging.getLogger(__name__)

MAX_RETRIES = 3


def _api_request(method, endpoint, **kwargs):
    """카페24 API 요청 (자동 재시도 + 토큰 갱신)"""
    url = f"{config.CAFE24_API_BASE}{endpoint}"

    for attempt in range(MAX_RETRIES):
        access_token = get_valid_token()
        if access_token is None:
            raise RuntimeError("유효한 카페24 토큰이 없습니다. OAuth 인증이 필요합니다.")

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Cafe24-Api-Version": "2025-12-01",
        }

        try:
            resp = requests.request(method, url, headers=headers, timeout=30, **kwargs)

            if resp.status_code == 401 and attempt < MAX_RETRIES - 1:
                logger.warning("카페24 401 응답 - 토큰 갱신 후 재시도 (%d/%d)", attempt + 1, MAX_RETRIES)
                time.sleep(1)
                continue

            resp.raise_for_status()
            return resp.json()

        except requests.RequestException:
            if attempt < MAX_RETRIES - 1:
                wait = 2 ** attempt
                logger.warning("카페24 API 요청 실패 - %d초 후 재시도 (%d/%d)", wait, attempt + 1, MAX_RETRIES)
                time.sleep(wait)
            else:
                raise

    return None


def get_paid_orders():
    """입금확인(paid) 상태 주문 조회 (최근 7일)"""
    start_date = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    params = {
        "start_date": start_date,
        "end_date": end_date,
        "order_status": "N20",  # 배송준비중
        "limit": 100,
    }

    result = _api_request("GET", "/admin/orders", params=params)
    return result.get("orders", []) if result else []


def get_order_items(order_id):
    """주문 상세 항목 조회"""
    result = _api_request("GET", f"/admin/orders/{order_id}/items")
    return result.get("items", []) if result else []


def get_order_detail(order_id):
    """주문 상세 정보 (메모 포함)"""
    result = _api_request("GET", f"/admin/orders/{order_id}")
    return result.get("order", {}) if result else {}


def get_products(limit=100, offset=0):
    """상품 목록 조회"""
    params = {
        "limit": limit,
        "offset": offset,
        "display": "T",  # 진열중인 상품만
    }
    result = _api_request("GET", "/admin/products", params=params)
    return result.get("products", []) if result else []


def get_all_products():
    """전체 상품 목록 조회 (페이징 처리)"""
    all_products = []
    offset = 0
    limit = 100

    while True:
        products = get_products(limit=limit, offset=offset)
        if not products:
            break
        all_products.extend(products)
        if len(products) < limit:
            break
        offset += limit

    return all_products


def get_product_options(product_no):
    """상품 옵션 조회"""
    result = _api_request("GET", f"/admin/products/{product_no}/options")
    return result.get("options", []) if result else []


# ── 주문 상태 변경 ──

def update_order_to_shipping(order_id, order_item_codes, tracking_no=""):
    """배송준비중(N20) → 배송중(N30) 처리

    shipping_company_code '0001' = 자체배송
    tracking_no = 인스타몬스터 주문번호
    """
    if isinstance(order_item_codes, str):
        order_item_codes = [order_item_codes]

    body = {
        "request": {
            "order_item_code": order_item_codes,
            "shipping_company_code": "0001",
            "tracking_no": str(tracking_no) if tracking_no else "",
            "status": "shipping",
        }
    }

    try:
        result = _api_request("POST", f"/admin/orders/{order_id}/shipments", json=body)
        logger.info("주문 %s: 배송중 처리 완료", order_id)
        return result
    except Exception:
        logger.exception("주문 %s: 배송중 처리 실패", order_id)
        return None


def get_order_shipments(order_id):
    """주문의 배송 목록 조회 (shipping_code 확인용)"""
    result = _api_request("GET", f"/admin/orders/{order_id}/shipments")
    return result.get("shipments", []) if result else []


def update_order_to_delivered(order_id, order_item_codes, shipping_code=None):
    """배송중(N30) → 배송완료(N40) 처리

    shipping_code가 없으면 자동으로 조회하여 찾는다.
    """
    if isinstance(order_item_codes, str):
        order_item_codes = [order_item_codes]

    # shipping_code가 없으면 조회
    if not shipping_code:
        shipments = get_order_shipments(order_id)
        if not shipments:
            logger.error("주문 %s: 배송 정보 없음 - 배송완료 처리 불가", order_id)
            return None
        shipping_code = shipments[0].get("shipping_code")
        if not shipping_code:
            logger.error("주문 %s: shipping_code 없음", order_id)
            return None

    body = {
        "request": {
            "order_item_code": order_item_codes,
            "status": "shipped",
        }
    }

    try:
        result = _api_request("PUT", f"/admin/orders/{order_id}/shipments/{shipping_code}", json=body)
        logger.info("주문 %s: 배송완료 처리 완료 (shipping_code=%s)", order_id, shipping_code)
        return result
    except Exception:
        logger.exception("주문 %s: 배송완료 처리 실패", order_id)
        return None
