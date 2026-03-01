import logging

from models import db, ProcessedOrder
from instamonster.client import get_order_status
from cafe24.orders import update_order_to_delivered
from services.telegram_notifier import notify_delivered, notify_needs_manual

logger = logging.getLogger(__name__)

# 인스타몬스터 완료 상태값
COMPLETED_STATUSES = {"Completed", "completed"}
PARTIAL_STATUSES = {"Partial", "partial"}
CANCELED_STATUSES = {"Canceled", "canceled", "Refunded", "refunded"}


def check_order_statuses():
    """배송중(shipping) 상태인 주문들의 인스타몬스터 진행 상태를 확인하고
    완료 시 카페24 배송완료 처리"""
    logger.info("주문 상태 체크 시작...")

    # 배송중인 일반 주문만 조회 (구독 주문은 ongoing이라 상태 체크 제외)
    orders = ProcessedOrder.query.filter(
        ProcessedOrder.status == "shipping",
        ProcessedOrder.order_type == "default",
        ProcessedOrder.insta_order_id.isnot(None),
    ).all()

    if not orders:
        logger.info("체크할 배송중 주문 없음")
        return

    logger.info("배송중 주문 %d건 상태 체크", len(orders))

    for record in orders:
        try:
            result = get_order_status(record.insta_order_id)
            if not result:
                continue

            insta_status = result.get("status", "")
            logger.info(
                "주문 %s (IM-%s): 인스타몬스터 상태 = %s",
                record.cafe24_order_id, record.insta_order_id, insta_status,
            )

            if insta_status in COMPLETED_STATUSES:
                _mark_delivered(record)
            elif insta_status in PARTIAL_STATUSES:
                _mark_partial(record, result)
            elif insta_status in CANCELED_STATUSES:
                _mark_canceled(record)

        except Exception:
            logger.exception(
                "주문 %s (IM-%s): 상태 체크 실패",
                record.cafe24_order_id, record.insta_order_id,
            )


def _mark_delivered(record):
    """인스타몬스터 완료 → 카페24 배송완료 + DB 상태 업데이트"""
    logger.info("주문 %s: 인스타몬스터 완료 → 배송완료 처리", record.cafe24_order_id)

    # 카페24 배송완료 처리
    if record.cafe24_order_item_id:
        update_order_to_delivered(
            record.cafe24_order_id,
            record.cafe24_order_item_id,
        )

    record.status = "delivered"
    db.session.commit()
    notify_delivered(record.cafe24_order_id, record.insta_order_id)


def _mark_partial(record, result):
    """부분 완료 → 수동 확인 필요 (취소 처리 안 함)"""
    remains = result.get("remains", "")
    logger.warning(
        "주문 %s: 부분 완료 (남은 수량: %s)", record.cafe24_order_id, remains,
    )
    record.status = "needs_review"
    record.error_message = f"부분 완료 (남은 수량: {remains})"
    db.session.commit()
    notify_needs_manual(
        record.cafe24_order_id, record.insta_order_id,
        f"부분 완료 - 남은 수량: {remains}",
    )


def _mark_canceled(record):
    """인스타몬스터 취소됨 → 수동 확인 필요 (카페24 취소 처리 안 함)"""
    logger.warning("주문 %s: 인스타몬스터 취소됨 → 수동 확인 필요", record.cafe24_order_id)
    record.status = "needs_review"
    record.error_message = "인스타몬스터에서 취소됨 - 수동 확인 필요"
    db.session.commit()
    notify_needs_manual(
        record.cafe24_order_id, record.insta_order_id,
        "인스타몬스터에서 취소됨",
    )
