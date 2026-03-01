import logging
import requests
import config

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _send(text):
    """텔레그램 메시지 전송 (실패해도 예외 안 던짐)"""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return

    try:
        url = TELEGRAM_API.format(token=config.TELEGRAM_BOT_TOKEN)
        resp = requests.post(url, json={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
        }, timeout=10)

        if not resp.ok:
            logger.warning("텔레그램 전송 실패: %s", resp.text)
    except Exception:
        logger.exception("텔레그램 전송 중 오류")


# ── 알림 함수들 ──

def notify_order_success(order_id, insta_order_id, service_name, link, quantity, order_type="default"):
    """발주 성공 알림"""
    type_label = "구독" if order_type == "subscription" else "일반"
    _send(
        f"<b>[발주 성공]</b>\n"
        f"카페24: <code>{order_id}</code>\n"
        f"인스타몬스터: <code>{insta_order_id}</code>\n"
        f"서비스: {service_name}\n"
        f"타입: {type_label}\n"
        f"대상: {link}\n"
        f"수량: {quantity}"
    )


def notify_order_error(order_id, error_msg):
    """발주 에러 알림"""
    _send(
        f"<b>[발주 에러]</b>\n"
        f"카페24: <code>{order_id}</code>\n"
        f"에러: {error_msg}"
    )


def notify_needs_review(order_id, reason):
    """검토 필요 알림"""
    _send(
        f"<b>[검토 필요]</b>\n"
        f"카페24: <code>{order_id}</code>\n"
        f"사유: {reason}"
    )


def notify_delivered(order_id, insta_order_id):
    """배송완료 알림"""
    _send(
        f"<b>[배송완료]</b>\n"
        f"카페24: <code>{order_id}</code>\n"
        f"인스타몬스터: <code>{insta_order_id}</code>"
    )


def notify_partial(order_id, insta_order_id, remains):
    """부분완료 알림"""
    _send(
        f"<b>[부분완료]</b>\n"
        f"카페24: <code>{order_id}</code>\n"
        f"인스타몬스터: <code>{insta_order_id}</code>\n"
        f"남은 수량: {remains}"
    )


def notify_canceled(order_id, insta_order_id):
    """취소 알림"""
    _send(
        f"<b>[주문 취소]</b>\n"
        f"카페24: <code>{order_id}</code>\n"
        f"인스타몬스터: <code>{insta_order_id}</code>"
    )


def notify_needs_manual(order_id, insta_order_id, reason):
    """수동 확인 필요 알림 (취소/부분완료 등 이상 주문)"""
    _send(
        f"<b>[수동 확인 필요]</b>\n"
        f"카페24: <code>{order_id}</code>\n"
        f"인스타몬스터: <code>{insta_order_id}</code>\n"
        f"사유: {reason}\n"
        f"관리자 페이지에서 수동 처리해주세요."
    )


def notify_low_balance(balance):
    """잔액 부족 경고"""
    _send(
        f"<b>[잔액 부족 경고]</b>\n"
        f"현재 잔액: ₩{balance:,.0f}\n"
        f"충전이 필요합니다."
    )


# ── 환불 알림 (별도 봇) ──

def _send_refund(text):
    """환불 전용 텔레그램 봇으로 메시지 전송"""
    if not config.TELEGRAM_REFUND_BOT_TOKEN or not config.TELEGRAM_REFUND_CHAT_ID:
        return

    try:
        url = TELEGRAM_API.format(token=config.TELEGRAM_REFUND_BOT_TOKEN)
        resp = requests.post(url, json={
            "chat_id": config.TELEGRAM_REFUND_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
        }, timeout=10)

        if not resp.ok:
            logger.warning("환불 텔레그램 전송 실패: %s", resp.text)
    except Exception:
        logger.exception("환불 텔레그램 전송 중 오류")


def notify_partial_refund(order_id, username, quantity, action_count, remains):
    """캠페인 중단 부분이행 알림 (별도 봇) — 수동 환불 필요"""
    _send_refund(
        f"<b>[캠페인 중단 - 부분이행]</b>\n"
        f"인스타몬스터: <code>{order_id}</code>\n"
        f"대상: {username}\n"
        f"주문수량: {quantity}\n"
        f"실제수행: {action_count}\n"
        f"미수행: {remains}\n"
        f"→ 수동 환불 처리 필요"
    )
