"""superap.io 캠페인 자동 세팅/완료 스케줄러.

3분마다 실행:
1. auto_campaign_job: 대기 주문 → 프로필 추출 → 캠페인 등록/연장 → in_progress
2. check_campaign_completion_job: in_progress 주문 → TotalOff 확인 → completed
3. auto_youtube_campaign_job: 유튜브 대기 주문 → 채널 스크래핑 → 캠페인 등록/연장
4. check_youtube_campaign_completion_job: 유튜브 in_progress → TotalOff → completed
5. sync_youtube_remains_job: 유튜브 리메인 동기화
"""

import json
import logging
import os
import time
from datetime import datetime

import requests

import config
from services.superap_client import SuperapClient, _load_campaign_map
from services.profile_extractor import (
    extract_username_from_link, scrape_profiles, extract_answer,
)
from services.youtube_scraper import (
    fetch_youtube_pending_orders,
    scrape_youtube_channels,
    cancel_youtube_orders,
    normalize_youtube_url,
)

logger = logging.getLogger(__name__)

ADMIN_API_URL = config.INSTAMONSTER_ADMIN_API_URL
ADMIN_API_KEY = config.INSTAMONSTER_ADMIN_API_KEY
SERVICE_ID = "32"  # 인스타 팔로우 서비스

CAMPAIGN_LOG_FILE = os.path.join(config.BASE_DIR, "data", "campaign_log.json")
CAMPAIGN_RETRY_FILE = os.path.join(config.BASE_DIR, "data", "campaign_retry.json")


def _api_headers():
    return {"X-Api-Key": ADMIN_API_KEY, "Content-Type": "application/json"}


def _save_campaign_log(username: str, setting_type: str):
    """캠페인 세팅 구분(신규/연장) 기록."""
    try:
        data = {}
        if os.path.isfile(CAMPAIGN_LOG_FILE):
            with open(CAMPAIGN_LOG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        data[username] = {
            "type": setting_type,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        with open(CAMPAIGN_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        logger.exception("캠페인 로그 저장 실패: %s", username)


def load_campaign_log() -> dict:
    """캠페인 세팅 로그 로드 (username → {type, time})."""
    if os.path.isfile(CAMPAIGN_LOG_FILE):
        try:
            with open(CAMPAIGN_LOG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


# ── 캠페인 재시도 추적 ─────────────────────────────────

def _load_campaign_retry() -> dict:
    """캠페인 재시도 상태 로드. {username: {retry_count, used_fallback, last_retry}}"""
    if os.path.isfile(CAMPAIGN_RETRY_FILE):
        try:
            with open(CAMPAIGN_RETRY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_campaign_retry(data: dict):
    try:
        with open(CAMPAIGN_RETRY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        logger.exception("캠페인 재시도 상태 저장 실패")


def _clear_campaign_retry(username: str):
    """캠페인이 완료되거나 최종 처리된 후 재시도 기록 삭제."""
    retry_data = _load_campaign_retry()
    if username in retry_data:
        del retry_data[username]
        _save_campaign_retry(retry_data)


# ── 인스타몬스터 Admin API 헬퍼 ───────────────────────

def change_order_status(order_ids: list[int], status: str) -> dict:
    """인스타몬스터 주문 상태 변경.

    canceled 요청은 별도 /orders/cancel 엔드포인트 사용 (ids를 string으로 전달).
    """
    if status == "canceled":
        ids_str = ",".join(str(i) for i in order_ids)
        resp = requests.post(
            f"{ADMIN_API_URL}/orders/cancel",
            headers=_api_headers(),
            json={"ids": ids_str},
            timeout=30,
        )
    else:
        resp = requests.post(
            f"{ADMIN_API_URL}/orders/change-status",
            headers=_api_headers(),
            json={"ids": order_ids, "status": status},
            timeout=30,
        )
    resp.raise_for_status()
    return resp.json()


def get_orders_by_status(status: str, service_id: str = SERVICE_ID, limit: int = 500) -> list[dict]:
    """특정 상태의 주문 목록 조회 (API가 status 필터 미지원 → 클라이언트측 필터링)."""
    resp = requests.get(
        f"{ADMIN_API_URL}/orders",
        headers=_api_headers(),
        params={"service_ids": service_id, "limit": limit},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    all_orders = data.get("data", {}).get("list", [])
    return [o for o in all_orders if o.get("status") == status]


def pull_pending_orders(service_id: str = SERVICE_ID, limit: int = 100) -> list[dict]:
    """대기 주문을 가져오면서 processing으로 변경."""
    resp = requests.post(
        f"{ADMIN_API_URL}/orders/pull",
        headers=_api_headers(),
        json={"service_ids": service_id, "limit": limit},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("error_code", 0) != 0:
        raise RuntimeError(data.get("error_message", "API 오류"))

    orders = data.get("data", {}).get("list", [])
    results = []
    for order in orders:
        link = order.get("link", "")
        username = extract_username_from_link(link)
        if not username:
            continue
        results.append({
            "order_id": order.get("id"),
            "username": username,
            "quantity": int(order.get("quantity", 0)),
            "link": link,
        })
    return results


# ── 자동 캠페인 세팅 ─────────────────────────────────

def auto_campaign_job():
    """대기 주문 → 프로필 추출 → 캠페인 등록/연장 → in_progress.

    비공개 계정 → canceled 처리.
    """
    logger.info("[캠페인 스케줄러] 자동 캠페인 세팅 시작...")

    # 1. 대기 주문 pull (pending → processing)
    try:
        orders = pull_pending_orders()
    except Exception:
        logger.exception("[캠페인 스케줄러] 대기 주문 조회 실패")
        return

    if not orders:
        logger.info("[캠페인 스케줄러] 대기 주문 없음")
        return

    logger.info("[캠페인 스케줄러] %d건 주문 조회됨", len(orders))

    # 2. 프로필 스크래핑 → 정답 + 비공개 여부 확인
    usernames = list({o["username"] for o in orders})
    try:
        profiles = scrape_profiles(usernames)
    except Exception:
        logger.exception("[캠페인 스케줄러] 프로필 스크래핑 실패")
        # 실패 시 주문을 다시 pending으로 되돌리기
        order_ids = [o["order_id"] for o in orders]
        try:
            change_order_status(order_ids, "pending")
            logger.info("[캠페인 스케줄러] %d건 주문을 pending으로 복원", len(order_ids))
        except Exception:
            logger.exception("[캠페인 스케줄러] 주문 상태 복원 실패")
        return

    # username → profile 매핑
    profile_map: dict[str, dict] = {}
    for p in profiles:
        profile_map[p["username"]] = p

    logger.info("[캠페인 스케줄러] 프로필 결과 %d건 / 요청 %d건", len(profiles), len(usernames))

    # 3. 존재하지 않는 계정 / 비공개 계정 → canceled 처리
    cancel_ids = []
    campaign_orders = []

    # username → order_ids 매핑
    username_to_order_ids: dict[str, list[int]] = {}
    for o in orders:
        username_to_order_ids.setdefault(o["username"], []).append(o["order_id"])

    skip_usernames = set()
    for username, oids in username_to_order_ids.items():
        profile = profile_map.get(username)
        if not profile:
            # 프로필이 없음 → 존재하지 않는 계정
            cancel_ids.extend(oids)
            skip_usernames.add(username)
            logger.info("[캠페인 스케줄러] %s: 존재하지 않는 계정 → canceled", username)
        elif profile.get("비공개") == "비공개":
            cancel_ids.extend(oids)
            skip_usernames.add(username)
            logger.info("[캠페인 스케줄러] %s: 비공개 계정 → canceled", username)

    if cancel_ids:
        try:
            change_order_status(cancel_ids, "canceled")
            logger.info("[캠페인 스케줄러] 존재하지 않는/비공개 %d건 → canceled", len(cancel_ids))
        except Exception:
            logger.exception("[캠페인 스케줄러] canceled 처리 실패")

    # 존재하지 않는/비공개 계정 제외한 주문만 캠페인 등록
    campaign_orders = [o for o in orders if o["username"] not in skip_usernames]

    if not campaign_orders:
        logger.info("[캠페인 스케줄러] 캠페인 등록할 주문 없음 (전부 존재하지 않거나 비공개)")
        return

    # 4. superap.io 캠페인 등록/연장 (정답 포함)
    try:
        client = SuperapClient()
        superap_orders = []
        for o in campaign_orders:
            profile = profile_map.get(o["username"], {})
            answer = profile.get("정답", "")
            superap_orders.append({
                "username": o["username"],
                "quantity": o["quantity"],
                "link": o["link"],
                "answer": answer,
            })
        results = client.process_orders_bulk(superap_orders)
    except Exception:
        logger.exception("[캠페인 스케줄러] superap.io 캠페인 처리 실패")
        # 실패 시 비공개 제외 주문을 다시 pending으로 되돌리기
        fail_order_ids = [o["order_id"] for o in campaign_orders]
        try:
            change_order_status(fail_order_ids, "pending")
            logger.info("[캠페인 스케줄러] %d건 주문을 pending으로 복원", len(fail_order_ids))
        except Exception:
            logger.exception("[캠페인 스케줄러] 주문 상태 복원 실패")
        return

    # 5. 결과에 따라 주문 상태 변경
    success_ids = []
    fail_ids = []

    new_count = 0
    extend_count = 0
    for r in results:
        username = r.get("username", "")
        oids = username_to_order_ids.get(username, [])
        gubun = r.get("구분", "")
        if r.get("ok"):
            success_ids.extend(oids)
            if gubun == "신규":
                new_count += 1
                _save_campaign_log(username, "신규")
                logger.info("[캠페인 스케줄러] %s: 신규 캠페인 등록 (%d건)", username, len(oids))
            elif gubun == "캠페인 연장":
                extend_count += 1
                _save_campaign_log(username, "연장")
                logger.info("[캠페인 스케줄러] %s: 캠페인 연장 (%d건, %s)", username, len(oids), r.get("message", ""))
        else:
            fail_ids.extend(oids)
            logger.warning("[캠페인 스케줄러] %s: %s 실패 — %s", username, gubun, r.get("message", ""))

    # 성공한 주문 → in_progress
    if success_ids:
        try:
            change_order_status(success_ids, "in_progress")
            logger.info("[캠페인 스케줄러] %d건 → in_progress", len(success_ids))
        except Exception:
            logger.exception("[캠페인 스케줄러] in_progress 상태 변경 실패")

    # 실패한 주문 → pending으로 되돌리기
    if fail_ids:
        try:
            change_order_status(fail_ids, "pending")
            logger.info("[캠페인 스케줄러] %d건 실패 → pending 복원", len(fail_ids))
        except Exception:
            logger.exception("[캠페인 스케줄러] pending 복원 실패")

    logger.info(
        "[캠페인 스케줄러] 완료 — 성공 %d건 (신규 %d, 연장 %d), 실패 %d건, 취소 %d건",
        len(success_ids), new_count, extend_count, len(fail_ids), len(cancel_ids),
    )


# ── 캠페인 완료 체크 ─────────────────────────────────

def check_campaign_completion_job():
    """in_progress 주문의 superap.io 캠페인 완료(TotalOff) 여부 확인 → completed."""
    logger.info("[캠페인 완료 체크] 시작...")

    # 1. in_progress 주문 조회
    try:
        orders = get_orders_by_status("in_progress")
    except Exception:
        logger.exception("[캠페인 완료 체크] in_progress 주문 조회 실패")
        return

    if not orders:
        logger.info("[캠페인 완료 체크] in_progress 주문 없음")
        return

    logger.info("[캠페인 완료 체크] in_progress %d건 확인 중", len(orders))

    # 2. campaign_map.json (로컬 매핑) + superap.io 캠페인 목록 조회
    local_map = _load_campaign_map()

    try:
        client = SuperapClient()
        all_campaigns = client.get_all_campaigns()
    except Exception:
        logger.exception("[캠페인 완료 체크] superap.io 캠페인 조회 실패")
        return

    # ad_idx → 캠페인 데이터
    campaigns_by_idx: dict[str, dict] = {}
    for c in all_campaigns:
        campaigns_by_idx[str(c.get("ad_idx", ""))] = c

    # 이름 기반 매핑 (폴백)
    name_map: dict[str, dict] = {}
    for c in all_campaigns:
        ad_name = c.get("ad_name", "")
        parts = ad_name.rsplit(" ", 1)
        if len(parts) == 2:
            uname = parts[1]
            if uname not in name_map or c["ad_idx"] > name_map[uname]["ad_idx"]:
                name_map[uname] = c

    # 3. TotalOff/Deactive 캠페인의 주문 분류: 완전이행 vs 부분이행
    completed_ids = []
    partial_refund_orders = []

    for order in orders:
        link = order.get("link", "")
        username = extract_username_from_link(link)
        if not username:
            continue

        # 캠페인 찾기: ① campaign_map.json → ② 이름 매칭 → ③ 언더바 제거 이름 매칭
        campaign = None
        ad_idx = local_map.get(username)
        if ad_idx:
            campaign = campaigns_by_idx.get(str(ad_idx))
        if not campaign:
            campaign = name_map.get(username)
        if not campaign:
            campaign = name_map.get(username.replace("_", ""))

        if not campaign:
            continue
        c_status = campaign.get("status", "")
        if c_status not in ("TotalOff", "Deactive"):
            continue

        quantity = int(order.get("quantity", 0))
        action_count = int(campaign.get("action_count", 0) or 0)

        if action_count >= quantity:
            completed_ids.append(order["id"])
            _clear_campaign_retry(username)
            logger.info(
                "[캠페인 완료 체크] %s: %s 완전이행 → completed (action=%d, qty=%d, ad_idx=%s)",
                username, c_status, action_count, quantity, campaign.get("ad_idx"),
            )
        else:
            remains = max(0, quantity - action_count)
            partial_refund_orders.append({
                "order_id": order["id"],
                "username": username,
                "quantity": quantity,
                "action_count": action_count,
                "remains": remains,
                "ad_idx": campaign.get("ad_idx"),
            })
            logger.info(
                "[캠페인 완료 체크] %s: %s 부분이행 (action=%d, qty=%d, remains=%d, ad_idx=%s)",
                username, c_status, action_count, quantity, remains, campaign.get("ad_idx"),
            )

    if completed_ids:
        try:
            change_order_status(completed_ids, "completed")
            logger.info("[캠페인 완료 체크] %d건 → completed", len(completed_ids))
        except Exception:
            logger.exception("[캠페인 완료 체크] completed 상태 변경 실패")

    if partial_refund_orders:
        _retry_or_refund(partial_refund_orders, client, platform="instagram")

    if not completed_ids and not partial_refund_orders:
        logger.info("[캠페인 완료 체크] 완료 처리할 주문 없음")


<<<<<<< HEAD
MAX_ANSWER_RETRIES = 3  # 정답 바꿔서 재시도 횟수


def _retry_or_refund(partial_orders: list[dict], client: SuperapClient, platform: str = "instagram"):
    """부분이행 캠페인: 정답 변경 → 수정(update) 시도, 최종 실패 시 환불 알림.

    1) retry_count < MAX_ANSWER_RETRIES: 정답을 다시 스크래핑해서 캠페인 수정
    2) retry_count == MAX_ANSWER_RETRIES (폴백): 정답을 "게시물"로 고정해서 수정
    3) 폴백까지 실패: 텔레그램 알림 (수동 환불)
    """
    label = "[캠페인 완료 체크]" if platform == "instagram" else "[유튜브 완료 체크]"
    retry_data = _load_campaign_retry()
    final_refund_orders = []

    for po in partial_orders:
        username = po["username"]
        remains = po["remains"]
        ad_idx = po.get("ad_idx")

        if not ad_idx:
            logger.warning("%s %s: ad_idx 없음 → 수정 불가, 환불 처리", label, username)
            final_refund_orders.append(po)
            continue

        state = retry_data.get(username, {"retry_count": 0, "used_fallback": False})

        # ── 이미 폴백("게시물")까지 시도한 경우 → 최종 환불 처리
        if state.get("used_fallback"):
            logger.info(
                "%s %s: 폴백(게시물)까지 실패 → 텔레그램 알림", label, username,
            )
            _clear_campaign_retry(username)
            final_refund_orders.append(po)
            continue

        retry_count = state.get("retry_count", 0)

        # ── 재시도 횟수 초과 → "게시물"로 폴백 시도
        if retry_count >= MAX_ANSWER_RETRIES:
            answer = "게시물"
            state["used_fallback"] = True
            logger.info(
                "%s %s: %d회 재시도 실패 → 폴백 정답 '게시물'로 수정 (ad_idx=%s, remains=%d)",
                label, username, retry_count, ad_idx, remains,
            )
        else:
            # ── 정답 다시 스크래핑해서 수정
            answer = client._scrape_answer(username)
            state["retry_count"] = retry_count + 1
            logger.info(
                "%s %s: 정답 변경 재시도 %d/%d → '%s' (ad_idx=%s, remains=%d)",
                label, username, state["retry_count"], MAX_ANSWER_RETRIES,
                answer, ad_idx, remains,
            )

        state["last_retry"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        retry_data[username] = state
        _save_campaign_retry(retry_data)

        # 기존 캠페인 수정 (정답 변경)
        try:
            result = client.update_campaign(
                ad_idx=str(ad_idx),
                username=username,
                answer=answer,
            )
            if result.get("ok"):
                logger.info(
                    "%s %s: 캠페인 수정 성공 (정답='%s', ad_idx=%s)",
                    label, username, answer, ad_idx,
                )
            else:
                logger.warning(
                    "%s %s: 캠페인 수정 실패 — %s",
                    label, username, result.get("message"),
                )
                _clear_campaign_retry(username)
                final_refund_orders.append(po)
        except Exception:
            logger.exception("%s %s: 캠페인 수정 중 오류", label, username)
            _clear_campaign_retry(username)
            final_refund_orders.append(po)

    if final_refund_orders:
        _process_partial_refund(final_refund_orders, platform=platform)

# 부분이행 알림 쿨다운 (order_id → 마지막 알림 시각)
_partial_refund_notified: dict[str, float] = {}
_PARTIAL_REFUND_COOLDOWN = 3600  # 1시간


def _process_partial_refund(partial_orders: list[dict], platform: str = "instagram"):
    """캠페인 중단(TotalOff/Deactive) 부분이행 → 알림만 전송 (수동 환불 처리)."""
    from services.telegram_notifier import notify_partial_refund
    from models import db, ProcessedOrder

    label = "[캠페인 완료 체크]" if platform == "instagram" else "[유튜브 완료 체크]"

    for po in partial_orders:
        try:
            record = ProcessedOrder.query.filter_by(
                insta_order_id=po["order_id"]
            ).first()
            if record:
                record.status = "partial_refund"
                record.error_message = (
                    f"캠페인 중단 부분이행: "
                    f"주문 {po['quantity']}건 중 {po['action_count']}건 수행, "
                    f"잔여 {po['remains']}건 (수동 환불 필요)"
                )
                db.session.commit()
        except Exception:
            db.session.rollback()
            logger.exception("%s ProcessedOrder 업데이트 실패: IM-%s", label, po["order_id"])

        # 알림 1시간 쿨다운
        now = time.time()
        last_notified = _partial_refund_notified.get(po["order_id"], 0)
        if now - last_notified < _PARTIAL_REFUND_COOLDOWN:
            logger.debug("%s 알림 쿨다운 중 (IM-%s), 건너뜀", label, po["order_id"])
            continue

        try:
            notify_partial_refund(
                order_id=po["order_id"],
                username=po["username"],
                quantity=po["quantity"],
                action_count=po["action_count"],
                remains=po["remains"],
            )
            _partial_refund_notified[po["order_id"]] = now
        except Exception:
            logger.exception("%s 텔레그램 알림 실패: IM-%s", label, po["order_id"])

    logger.info("%s 부분이행 %d건 알림 전송 (수동 환불 필요)", label, len(partial_orders))


# ── 리메인 동기화 ─────────────────────────────────────

def update_order_remains(updates: list[dict]) -> dict:
    """인스타몬스터 주문의 remains를 일괄 업데이트.

    updates: [{"id": order_id, "remains": new_remains}, ...]
    """
    resp = requests.post(
        f"{ADMIN_API_URL}/orders/update",
        headers=_api_headers(),
        json={"orders": updates},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def sync_remains_job():
    """in_progress 주문의 remains를 superap.io action_count 기반으로 동기화.

    remains = quantity - action_count
    """
    logger.info("[리메인 동기화] 시작...")

    # 1. in_progress 주문 조회
    try:
        orders = get_orders_by_status("in_progress")
    except Exception:
        logger.exception("[리메인 동기화] in_progress 주문 조회 실패")
        return

    if not orders:
        logger.info("[리메인 동기화] in_progress 주문 없음")
        return

    # 2. campaign_map.json 로드 (username → ad_idx)
    campaign_map = _load_campaign_map()

    # 3. superap.io 캠페인 목록 조회
    try:
        client = SuperapClient()
        all_campaigns = client.get_all_campaigns()
    except Exception:
        logger.exception("[리메인 동기화] superap.io 캠페인 조회 실패")
        return

    # ad_idx → campaign 매핑
    campaigns_by_idx: dict[str, dict] = {}
    for c in all_campaigns:
        campaigns_by_idx[str(c.get("ad_idx", ""))] = c

    # 4. 각 주문의 remains 계산
    updates = []
    for order in orders:
        link = order.get("link", "")
        username = extract_username_from_link(link)
        if not username:
            continue

        ad_idx = campaign_map.get(username)
        if not ad_idx:
            continue

        campaign = campaigns_by_idx.get(str(ad_idx))
        if not campaign:
            continue

        quantity = int(order.get("quantity", 0))
        action_count = int(campaign.get("action_count", 0) or 0)
        new_remains = max(0, quantity - action_count)
        current_remains = int(order.get("remains", 0))

        if new_remains != current_remains:
            updates.append({"id": order["id"], "remains": new_remains})
            logger.info(
                "[리메인 동기화] %s: remains %d → %d (quantity=%d, action=%d)",
                username, current_remains, new_remains, quantity, action_count,
            )

    # 5. 일괄 업데이트
    if updates:
        try:
            update_order_remains(updates)
            logger.info("[리메인 동기화] %d건 업데이트 완료", len(updates))
        except Exception:
            logger.exception("[리메인 동기화] remains 업데이트 실패")
    else:
        logger.info("[리메인 동기화] 업데이트 필요 없음")


# ══════════════════════════════════════════════════════
# 유튜브 구독자 캠페인 자동 스케줄러
# ══════════════════════════════════════════════════════

YOUTUBE_SERVICE_ID = "129"


def _youtube_pull_pending(limit: int = 100) -> list[dict]:
    """유튜브 대기 주문 pull (pending → processing)."""
    resp = requests.post(
        f"{ADMIN_API_URL}/orders/pull",
        headers=_api_headers(),
        json={"service_ids": YOUTUBE_SERVICE_ID, "limit": limit},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("error_code", 0) != 0:
        raise RuntimeError(data.get("error_message", "API 오류"))

    orders = data.get("data", {}).get("list", [])
    results = []
    for order in orders:
        raw_link = order.get("link", "").strip()
        if not raw_link:
            continue
        normalized = normalize_youtube_url(raw_link)
        results.append({
            "order_id": order.get("id"),
            "channel_url": normalized,
            "raw_link": raw_link,
            "quantity": int(order.get("quantity", 0)),
        })
    return results


def auto_youtube_campaign_job():
    """유튜브 대기 주문 → 채널 스크래핑 → 캠페인 등록/연장 → in_progress.

    존재하지 않는 채널 → canceled 처리.
    """
    logger.info("[유튜브 캠페인] 자동 캠페인 세팅 시작...")

    # 1. 대기 주문 pull (pending → processing)
    try:
        orders = _youtube_pull_pending()
    except Exception:
        logger.exception("[유튜브 캠페인] 대기 주문 조회 실패")
        return

    if not orders:
        logger.info("[유튜브 캠페인] 대기 주문 없음")
        return

    logger.info("[유튜브 캠페인] %d건 주문 조회됨", len(orders))

    # 2. 채널 URL 중복 제거 후 스크래핑
    url_to_order_ids: dict[str, list[int]] = {}
    for o in orders:
        url_to_order_ids.setdefault(o["channel_url"], []).append(o["order_id"])

    unique_urls = list(url_to_order_ids.keys())
    try:
        profiles = scrape_youtube_channels(unique_urls)
    except Exception:
        logger.exception("[유튜브 캠페인] 채널 스크래핑 실패")
        # 실패 시 주문을 다시 pending으로 되돌리기
        all_ids = [o["order_id"] for o in orders]
        try:
            change_order_status(all_ids, "pending")
            logger.info("[유튜브 캠페인] %d건 주문을 pending으로 복원", len(all_ids))
        except Exception:
            logger.exception("[유튜브 캠페인] 주문 상태 복원 실패")
        return

    # channel_url → profile 매핑
    profile_map: dict[str, dict] = {}
    for p in profiles:
        profile_map[p["channel_url"].lower()] = p

    logger.info("[유튜브 캠페인] 스크래핑 결과 %d건 / 요청 %d건", len(profiles), len(unique_urls))

    # 3. 존재하지 않는 채널 → canceled 처리
    cancel_ids = []
    skip_urls = set()

    for url, oids in url_to_order_ids.items():
        if url.lower() not in profile_map:
            cancel_ids.extend(oids)
            skip_urls.add(url)
            logger.info("[유튜브 캠페인] %s: 존재하지 않는 채널 → canceled", url)

    if cancel_ids:
        try:
            change_order_status(cancel_ids, "canceled")
            logger.info("[유튜브 캠페인] 존재하지 않는 채널 %d건 → canceled", len(cancel_ids))
        except Exception:
            logger.exception("[유튜브 캠페인] canceled 처리 실패")

    # 4. 유효한 주문만 캠페인 등록/연장
    campaign_orders = [o for o in orders if o["channel_url"] not in skip_urls]

    if not campaign_orders:
        logger.info("[유튜브 캠페인] 캠페인 등록할 주문 없음")
        return

    try:
        client = SuperapClient("youtube")
        superap_orders = []
        for o in campaign_orders:
            profile = profile_map.get(o["channel_url"].lower(), {})
            answer = profile.get("정답", "")
            superap_orders.append({
                "username": o["channel_url"],
                "quantity": o["quantity"],
                "link": o["channel_url"],
                "answer": answer,
                "channel_name": profile.get("channel_name", ""),
            })
        results = client.process_orders_bulk(superap_orders)
    except Exception:
        logger.exception("[유튜브 캠페인] superap.io 캠페인 처리 실패")
        fail_ids = [o["order_id"] for o in campaign_orders]
        try:
            change_order_status(fail_ids, "pending")
            logger.info("[유튜브 캠페인] %d건 주문을 pending으로 복원", len(fail_ids))
        except Exception:
            logger.exception("[유튜브 캠페인] 주문 상태 복원 실패")
        return

    # 5. 결과에 따라 주문 상태 변경
    success_ids = []
    fail_ids = []

    new_count = 0
    extend_count = 0
    for r in results:
        username = r.get("username", "")
        oids = url_to_order_ids.get(username, [])
        gubun = r.get("구분", "")
        if r.get("ok"):
            success_ids.extend(oids)
            if gubun == "신규":
                new_count += 1
                _save_campaign_log(username, "신규")
                logger.info("[유튜브 캠페인] %s: 신규 캠페인 등록 (%d건)", username, len(oids))
            elif gubun == "캠페인 연장":
                extend_count += 1
                _save_campaign_log(username, "연장")
                logger.info("[유튜브 캠페인] %s: 캠페인 연장 (%d건, %s)", username, len(oids), r.get("message", ""))
        else:
            fail_ids.extend(oids)
            logger.warning("[유튜브 캠페인] %s: %s 실패 — %s", username, gubun, r.get("message", ""))

    if success_ids:
        try:
            change_order_status(success_ids, "in_progress")
            logger.info("[유튜브 캠페인] %d건 → in_progress", len(success_ids))
        except Exception:
            logger.exception("[유튜브 캠페인] in_progress 상태 변경 실패")

    if fail_ids:
        try:
            change_order_status(fail_ids, "pending")
            logger.info("[유튜브 캠페인] %d건 실패 → pending 복원", len(fail_ids))
        except Exception:
            logger.exception("[유튜브 캠페인] pending 복원 실패")

    logger.info(
        "[유튜브 캠페인] 완료 — 성공 %d건 (신규 %d, 연장 %d), 실패 %d건, 취소 %d건",
        len(success_ids), new_count, extend_count, len(fail_ids), len(cancel_ids),
    )


def check_youtube_campaign_completion_job():
    """유튜브 in_progress 주문의 superap.io 캠페인 완료(TotalOff) 여부 확인 → completed."""
    logger.info("[유튜브 완료 체크] 시작...")

    try:
        orders = get_orders_by_status("in_progress", service_id=YOUTUBE_SERVICE_ID)
    except Exception:
        logger.exception("[유튜브 완료 체크] in_progress 주문 조회 실패")
        return

    if not orders:
        logger.info("[유튜브 완료 체크] in_progress 주문 없음")
        return

    logger.info("[유튜브 완료 체크] in_progress %d건 확인 중", len(orders))

    local_map = _load_campaign_map("youtube")

    try:
        client = SuperapClient("youtube")
        all_campaigns = client.get_all_campaigns()
    except Exception:
        logger.exception("[유튜브 완료 체크] superap.io 캠페인 조회 실패")
        return

    campaigns_by_idx: dict[str, dict] = {}
    for c in all_campaigns:
        campaigns_by_idx[str(c.get("ad_idx", ""))] = c

    # 이름 기반 매핑 (폴백)
    name_map: dict[str, dict] = {}
    for c in all_campaigns:
        ad_name = c.get("ad_name", "")
        parts = ad_name.rsplit(" ", 1)
        if len(parts) == 2:
            uname = parts[1]
            if uname not in name_map or c["ad_idx"] > name_map[uname]["ad_idx"]:
                name_map[uname] = c

    completed_ids = []
    partial_refund_orders = []

    for order in orders:
        raw_link = order.get("link", "").strip()
        if not raw_link:
            continue
        channel_url = normalize_youtube_url(raw_link)

        # 캠페인 찾기: ① campaign_map → ② 이름 매칭 → ③ 언더바 제거 이름 매칭
        campaign = None
        ad_idx = local_map.get(channel_url)
        if ad_idx:
            campaign = campaigns_by_idx.get(str(ad_idx))
        if not campaign:
            campaign = name_map.get(channel_url)
        if not campaign:
            campaign = name_map.get(channel_url.replace("_", ""))

        if not campaign:
            continue
        c_status = campaign.get("status", "")
        if c_status not in ("TotalOff", "Deactive"):
            continue

        quantity = int(order.get("quantity", 0))
        action_count = int(campaign.get("action_count", 0) or 0)

        if action_count >= quantity:
            completed_ids.append(order["id"])
            _clear_campaign_retry(channel_url)
            logger.info(
                "[유튜브 완료 체크] %s: %s 완전이행 → completed (action=%d, qty=%d, ad_idx=%s)",
                channel_url, c_status, action_count, quantity, campaign.get("ad_idx"),
            )
        else:
            remains = max(0, quantity - action_count)
            partial_refund_orders.append({
                "order_id": order["id"],
                "username": channel_url,
                "quantity": quantity,
                "action_count": action_count,
                "remains": remains,
                "ad_idx": campaign.get("ad_idx"),
            })
            logger.info(
                "[유튜브 완료 체크] %s: %s 부분이행 (action=%d, qty=%d, remains=%d, ad_idx=%s)",
                channel_url, c_status, action_count, quantity, remains, campaign.get("ad_idx"),
            )

    if completed_ids:
        try:
            change_order_status(completed_ids, "completed")
            logger.info("[유튜브 완료 체크] %d건 → completed", len(completed_ids))
        except Exception:
            logger.exception("[유튜브 완료 체크] completed 상태 변경 실패")

    if partial_refund_orders:
        _retry_or_refund(partial_refund_orders, client, platform="youtube")

    if not completed_ids and not partial_refund_orders:
        logger.info("[유튜브 완료 체크] 완료 처리할 주문 없음")


def sync_youtube_remains_job():
    """유튜브 in_progress 주문의 remains를 superap.io action_count 기반으로 동기화."""
    logger.info("[유튜브 리메인] 시작...")

    try:
        orders = get_orders_by_status("in_progress", service_id=YOUTUBE_SERVICE_ID)
    except Exception:
        logger.exception("[유튜브 리메인] in_progress 주문 조회 실패")
        return

    if not orders:
        logger.info("[유튜브 리메인] in_progress 주문 없음")
        return

    campaign_map = _load_campaign_map("youtube")

    try:
        client = SuperapClient("youtube")
        all_campaigns = client.get_all_campaigns()
    except Exception:
        logger.exception("[유튜브 리메인] superap.io 캠페인 조회 실패")
        return

    campaigns_by_idx: dict[str, dict] = {}
    for c in all_campaigns:
        campaigns_by_idx[str(c.get("ad_idx", ""))] = c

    updates = []
    for order in orders:
        raw_link = order.get("link", "").strip()
        if not raw_link:
            continue
        channel_url = normalize_youtube_url(raw_link)

        ad_idx = campaign_map.get(channel_url)
        if not ad_idx:
            continue

        campaign = campaigns_by_idx.get(str(ad_idx))
        if not campaign:
            continue

        quantity = int(order.get("quantity", 0))
        action_count = int(campaign.get("action_count", 0) or 0)
        new_remains = max(0, quantity - action_count)
        current_remains = int(order.get("remains", 0))

        if new_remains != current_remains:
            updates.append({"id": order["id"], "remains": new_remains})
            logger.info(
                "[유튜브 리메인] %s: remains %d → %d (quantity=%d, action=%d)",
                channel_url, current_remains, new_remains, quantity, action_count,
            )

    if updates:
        try:
            update_order_remains(updates)
            logger.info("[유튜브 리메인] %d건 업데이트 완료", len(updates))
        except Exception:
            logger.exception("[유튜브 리메인] remains 업데이트 실패")
    else:
        logger.info("[유튜브 리메인] 업데이트 필요 없음")
