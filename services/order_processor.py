import json
import logging
from datetime import datetime, timezone

from models import db, ProductMapping, ProcessedOrder
from cafe24.orders import get_paid_orders, get_order_items, get_order_detail, update_order_to_shipping
from instamonster.client import add_order as insta_add_order, add_subscription_order
from services.link_extractor import (
    extract_link, extract_quantity_from_option, extract_username_from_option,
    extract_likes_quantity, extract_posts_quantity, _get_option_value,
)
from services.telegram_notifier import notify_order_success, notify_order_error, notify_needs_review

logger = logging.getLogger(__name__)


def _resolve_service(item, mapping):
    """조건부 매핑이 있으면 옵션값에 따라 서비스 ID를 결정한다.

    Returns: (service_id, service_name) 또는 (None, error_msg)
    """
    smap = mapping.get_service_map()
    if not smap:
        return mapping.insta_service_id, mapping.insta_service_name

    condition_name = smap["option_name"]
    value_map = smap["map"]

    # 주문 항목 옵션에서 조건 옵션 찾기
    for option in item.get("options", []):
        name = option.get("option_name", "") or option.get("name", "")
        if condition_name not in name:
            continue
        value = _get_option_value(option)
        if value in value_map:
            entry = value_map[value]
            return entry["service_id"], entry.get("service_name", "")
        return None, f"옵션 '{condition_name}'의 값 '{value}'에 매핑된 서비스 없음"

    # additional_option 에서도 확인
    for opt in item.get("additional_option", []) or []:
        name = opt.get("name", "")
        if condition_name not in name:
            continue
        value = opt.get("value", "")
        if value in value_map:
            entry = value_map[value]
            return entry["service_id"], entry.get("service_name", "")
        return None, f"옵션 '{condition_name}'의 값 '{value}'에 매핑된 서비스 없음"

    return None, f"조건 옵션 '{condition_name}'을 주문에서 찾을 수 없음"


def process_new_orders():
    """새 주문을 조회하고 처리하는 메인 폴링 함수"""
    logger.info("주문 폴링 시작...")

    try:
        orders = get_paid_orders()
    except Exception:
        logger.exception("카페24 주문 조회 실패")
        return

    if not orders:
        logger.info("처리할 새 주문 없음")
        return

    logger.info("조회된 주문: %d건", len(orders))

    for order in orders:
        order_id = order.get("order_id")
        if not order_id:
            continue

        try:
            _process_single_order(order_id)
        except Exception:
            logger.exception("주문 %s 처리 중 오류", order_id)
            _save_error(order_id, "", "처리 중 예외 발생")
            notify_order_error(order_id, "처리 중 예외 발생")


def _process_single_order(order_id):
    """개별 주문 처리"""
    logger.info("주문 처리 시작: %s", order_id)

    items = get_order_items(order_id)
    order_detail = get_order_detail(order_id)

    if not items:
        logger.warning("주문 %s: 항목 없음", order_id)
        _save_error(order_id, "", "주문 항목 없음")
        notify_order_error(order_id, "주문 항목 없음")
        return

    for item in items:
        product_no = item.get("product_no")
        item_id = item.get("order_item_code", "")

        if not product_no:
            continue

        # 이미 처리된 항목 확인 (주문ID + 항목ID 복합 체크)
        existing = ProcessedOrder.query.filter_by(
            cafe24_order_id=order_id,
            cafe24_order_item_id=item_id,
        ).first()
        if existing:
            continue
        # 패키지 서브 항목도 확인 (item_id#pkg1 등)
        pkg_existing = ProcessedOrder.query.filter(
            ProcessedOrder.cafe24_order_id == order_id,
            ProcessedOrder.cafe24_order_item_id.like(item_id + "#pkg%"),
        ).first()
        if pkg_existing:
            continue

        mapping = ProductMapping.query.filter_by(
            cafe24_product_no=product_no,
            is_active=True,
        ).first()

        if not mapping:
            logger.warning("주문 %s: 상품 %s 매핑 없음", order_id, product_no)
            _save_error(order_id, item_id, f"상품 {product_no} 매핑 없음")
            notify_order_error(order_id, f"상품 {product_no} 매핑 없음")
            continue

        if mapping.order_type == "package":
            _process_package_order(order_id, item_id, item, order_detail, mapping)
        elif mapping.order_type == "subscription":
            _process_subscription_order(order_id, item_id, item, order_detail, mapping)
        else:
            _process_default_order(order_id, item_id, item, order_detail, mapping)


def _process_default_order(order_id, item_id, item, order_detail, mapping):
    """일반 주문 처리"""
    # ── 서비스 ID 결정 (조건부 매핑 지원) ──
    service_id, service_name = _resolve_service(item, mapping)
    if service_id is None:
        error_msg = service_name
        logger.warning("주문 %s: 서비스 매핑 실패 - %s", order_id, error_msg)
        _save_needs_review(order_id, item_id, mapping.insta_service_id, error_msg)
        notify_needs_review(order_id, error_msg)
        return

    # 인스타그램 링크 추출
    link = extract_link(
        [item], order_detail,
        link_source=mapping.link_source,
        option_name=mapping.option_name or "",
    )

    if not link:
        logger.warning("주문 %s: 인스타그램 링크 추출 실패", order_id)
        _save_needs_review(order_id, item_id, service_id, "링크 추출 실패")
        notify_needs_review(order_id, "링크 추출 실패")
        return

    # 수량 결정
    quantity = extract_quantity_from_option(
        [item], quantity_option_name=mapping.quantity_option_name or "",
    )
    if not quantity:
        logger.warning("주문 %s: 수량 추출 실패", order_id)
        _save_needs_review(order_id, item_id, service_id, "수량 추출 실패")
        notify_needs_review(order_id, "수량 추출 실패 - 수동 확인 필요")
        return

    # 카페24 주문 수량 반영
    item_qty = item.get("quantity", 1) or 1
    if item_qty > 1:
        quantity = quantity * item_qty

    # 인스타몬스터 발주
    try:
        result = insta_add_order(
            service_id=service_id,
            link=link,
            quantity=quantity,
        )

        if result and "order" in result:
            insta_order_id = result["order"]
            logger.info("주문 %s: 발주 성공 (서비스: %s, 주문ID: %s)", order_id, service_name, insta_order_id)
            _save_success(order_id, item_id, insta_order_id, service_id,
                          link, quantity, "default")
            update_order_to_shipping(order_id, item_id, tracking_no=str(insta_order_id))
            notify_order_success(order_id, insta_order_id, service_name, link, quantity)
        elif result and "error" in result:
            error_msg = f"인스타몬스터 에러: {result['error']}"
            logger.error("주문 %s: 발주 실패 - %s", order_id, result["error"])
            _save_error(order_id, item_id, error_msg)
            notify_order_error(order_id, error_msg)
        else:
            _save_error(order_id, item_id, "인스타몬스터 응답 없음")
            notify_order_error(order_id, "인스타몬스터 응답 없음")
    except Exception:
        logger.exception("주문 %s: API 호출 실패", order_id)
        _save_error(order_id, item_id, "인스타몬스터 API 호출 실패")
        notify_order_error(order_id, "인스타몬스터 API 호출 실패")


def _process_subscription_order(order_id, item_id, item, order_detail, mapping):
    """구독(자동) 주문 처리

    고객 입력: 인스타 아이디, 게시물 수량(참고용), 좋아요 수량
    → 인스타몬스터 구독 API로 발주
    """
    # ── 서비스 ID 결정 (조건부 매핑 지원) ──
    service_id, service_name = _resolve_service(item, mapping)
    if service_id is None:
        error_msg = service_name
        logger.warning("주문 %s: 서비스 매핑 실패 - %s", order_id, error_msg)
        _save_needs_review(order_id, item_id, mapping.insta_service_id, error_msg)
        notify_needs_review(order_id, error_msg)
        return

    # 1. 인스타 아이디 추출
    username = extract_username_from_option(
        [item], username_option_name=mapping.sub_username_option or "",
    )
    if not username:
        logger.warning("주문 %s: 인스타 아이디 추출 실패", order_id)
        _save_needs_review(order_id, item_id, service_id,
                           "구독: 인스타 아이디 추출 실패")
        notify_needs_review(order_id, "구독: 인스타 아이디 추출 실패")
        return

    # 2. 좋아요 수량 추출 - 키워드 자동 감지 지원
    likes_qty = extract_likes_quantity(
        [item], likes_option_name=mapping.sub_likes_option or "",
    )
    if not likes_qty:
        logger.warning("주문 %s: 좋아요 수량 추출 실패", order_id)
        _save_needs_review(order_id, item_id, service_id,
                           "구독: 좋아요 수량 추출 실패")
        notify_needs_review(order_id, "구독: 좋아요 수량 추출 실패")
        return

    # 3. 게시물 수량 추출 → 인스타몬스터 posts 파라미터로 전달 (갯수 채워지면 종료)
    posts_qty = extract_posts_quantity(
        [item], posts_option_name=mapping.sub_posts_option or "",
    ) or 0

    # 4. 인스타몬스터 구독 발주
    #    min = max (동일 수량), old_posts = 0 (이전 게시물 적용 안 함)
    #    만료일 없이 게시물 갯수 기준으로 종료
    min_qty = likes_qty
    max_qty = likes_qty

    try:
        result = add_subscription_order(
            service_id=service_id,
            username=username,
            min_qty=min_qty,
            max_qty=max_qty,
            posts=posts_qty,
            old_posts=0,
            delay=mapping.sub_delay,
        )

        extra = json.dumps({
            "username": username,
            "min": min_qty,
            "max": max_qty,
            "posts_qty": posts_qty or 0,
            "old_posts": 0,
            "delay": mapping.sub_delay,
        }, ensure_ascii=False)

        if result and "order" in result:
            insta_order_id = result["order"]
            logger.info("주문 %s: 구독 발주 성공 (주문ID: %s, 유저: %s, 좋아요: %d~%d, 게시물: %d)",
                         order_id, insta_order_id, username, min_qty, max_qty, posts_qty)
            _save_success(order_id, item_id, insta_order_id, service_id,
                          f"@{username}", likes_qty, "subscription", extra)
            update_order_to_shipping(order_id, item_id, tracking_no=str(insta_order_id))
            notify_order_success(order_id, insta_order_id, service_name,
                                 f"@{username}", likes_qty, "subscription")
        elif result and "error" in result:
            error_msg = f"구독 에러: {result['error']}"
            logger.error("주문 %s: 구독 발주 실패 - %s", order_id, result["error"])
            _save_error(order_id, item_id, error_msg)
            notify_order_error(order_id, error_msg)
        else:
            _save_error(order_id, item_id, "구독: 인스타몬스터 응답 없음")
            notify_order_error(order_id, "구독: 인스타몬스터 응답 없음")
    except Exception:
        logger.exception("주문 %s: 구독 API 호출 실패", order_id)
        _save_error(order_id, item_id, "구독: API 호출 실패")
        notify_order_error(order_id, "구독: API 호출 실패")


def _process_package_order(order_id, item_id, item, order_detail, mapping):
    """패키지 주문 처리 - 1건 주문에서 복수 서비스 동시 발주

    package_config 예시:
    [
      {"type": "subscription", "service_id": 267, "service_name": "...",
       "min": 100, "max": 100, "posts": 10, "delay": 0},
      {"type": "default", "service_id": 32, "service_name": "...",
       "quantity": 500}
    ]
    """
    pkg = mapping.get_package_config()
    if not pkg:
        logger.warning("주문 %s: 패키지 설정 없음", order_id)
        _save_error(order_id, item_id, "패키지 설정(package_config) 없음")
        notify_order_error(order_id, "패키지 설정 없음")
        return

    # 인스타 아이디 추출 (패키지 상품 공통)
    username = extract_username_from_option(
        [item], username_option_name=mapping.sub_username_option or "",
    )
    if not username:
        logger.warning("주문 %s: 패키지 - 인스타 아이디 추출 실패", order_id)
        _save_needs_review(order_id, item_id, 0, "패키지: 인스타 아이디 추출 실패")
        notify_needs_review(order_id, "패키지: 인스타 아이디 추출 실패")
        return

    profile_link = f"https://www.instagram.com/{username}/"
    results = []

    for idx, svc in enumerate(pkg):
        svc_type = svc.get("type", "default")
        svc_id = svc.get("service_id")
        svc_name = svc.get("service_name", "")
        sub_item_id = item_id if idx == 0 else f"{item_id}#pkg{idx}"

        try:
            if svc_type == "subscription":
                min_qty = svc.get("min", 100)
                max_qty = svc.get("max", min_qty)
                posts = svc.get("posts", 0)
                delay = svc.get("delay", 0)

                result = add_subscription_order(
                    service_id=svc_id,
                    username=username,
                    min_qty=min_qty,
                    max_qty=max_qty,
                    posts=posts,
                    old_posts=0,
                    delay=delay,
                )
            else:
                qty = svc.get("quantity", 0)
                result = insta_add_order(
                    service_id=svc_id,
                    link=profile_link,
                    quantity=qty,
                )

            if result and "order" in result:
                insta_order_id = result["order"]
                logger.info("주문 %s: 패키지[%d] 발주 성공 (서비스: %s, 주문ID: %s)",
                            order_id, idx, svc_name, insta_order_id)
                _save_success(order_id, sub_item_id, insta_order_id, svc_id,
                              f"@{username}", svc.get("quantity", 0),
                              "package", json.dumps(svc, ensure_ascii=False))
                results.append({"idx": idx, "ok": True, "order_id": insta_order_id})
            elif result and "error" in result:
                error_msg = f"패키지[{idx}] {svc_name}: {result['error']}"
                logger.error("주문 %s: %s", order_id, error_msg)
                _save_error(order_id, sub_item_id, error_msg)
                notify_order_error(order_id, error_msg)
                results.append({"idx": idx, "ok": False})
            else:
                error_msg = f"패키지[{idx}] {svc_name}: 응답 없음"
                _save_error(order_id, sub_item_id, error_msg)
                notify_order_error(order_id, error_msg)
                results.append({"idx": idx, "ok": False})

        except Exception:
            logger.exception("주문 %s: 패키지[%d] API 호출 실패", order_id, idx)
            error_msg = f"패키지[{idx}] {svc_name}: API 호출 실패"
            _save_error(order_id, sub_item_id, error_msg)
            notify_order_error(order_id, error_msg)
            results.append({"idx": idx, "ok": False})

    # 모든 서비스 성공 시에만 배송처리
    success_count = sum(1 for r in results if r["ok"])
    if success_count == len(pkg):
        first_order_id = results[0]["order_id"]
        update_order_to_shipping(order_id, item_id, tracking_no=str(first_order_id))
        svc_names = ", ".join(s.get("service_name", "") for s in pkg)
        notify_order_success(order_id, first_order_id, f"패키지({svc_names})",
                             f"@{username}", 0)
    elif success_count > 0:
        notify_needs_review(order_id, f"패키지 부분 성공 ({success_count}/{len(pkg)})")


def _save_success(order_id, item_id, insta_order_id, service_id, link, quantity,
                  order_type="default", extra_info=""):
    record = ProcessedOrder(
        cafe24_order_id=order_id,
        cafe24_order_item_id=item_id,
        insta_order_id=insta_order_id,
        service_id=service_id,
        order_type=order_type,
        link=link,
        quantity=quantity,
        status="shipping",
        extra_info=extra_info,
    )
    db.session.add(record)
    db.session.commit()


def _save_error(order_id, item_id, error_msg):
    record = ProcessedOrder(
        cafe24_order_id=order_id,
        cafe24_order_item_id=item_id,
        status="error",
        error_message=error_msg,
    )
    db.session.add(record)
    db.session.commit()


def _save_needs_review(order_id, item_id, service_id, error_msg):
    record = ProcessedOrder(
        cafe24_order_id=order_id,
        cafe24_order_item_id=item_id,
        service_id=service_id,
        status="needs_review",
        error_message=error_msg,
    )
    db.session.add(record)
    db.session.commit()


def retry_order(order_record_id):
    """에러 주문 수동 재처리"""
    record = ProcessedOrder.query.get(order_record_id)
    if not record:
        return False, "주문 기록을 찾을 수 없습니다."

    if record.status not in ("error", "needs_review", "partial", "canceled"):
        return False, "재처리 대상이 아닙니다."

    order_id = record.cafe24_order_id
    db.session.delete(record)
    db.session.commit()

    try:
        _process_single_order(order_id)
        return True, "재처리 완료"
    except Exception as e:
        return False, f"재처리 실패: {e}"
