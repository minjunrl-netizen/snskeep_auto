"""유튜브 채널 스크래핑 비즈니스 로직."""

import json
import logging
import os
import re
from datetime import datetime
from urllib.parse import urlparse, unquote

import requests
from apify_client import ApifyClient

import config

logger = logging.getLogger(__name__)

# Apify YouTube Scraper (streamers/youtube-scraper) — 내부 ID: h7sDV53CddomktSi5
ACTOR_ID = "h7sDV53CddomktSi5"
HISTORY_FILE = os.path.join(config.BASE_DIR, "data", "유튜브_이력.json")


# -- URL 정규화 --

def normalize_youtube_url(raw: str) -> str:
    """다양한 유튜브 URL 형식을 정규화.

    지원 형식:
    - @handle (맨 핸들)
    - youtube.com/@handle?si=...
    - m.youtube.com/@handle?fbclid=...
    - youtube.com/channel/UCxxxx?si=...
    - www 유무, http/https 유무
    - URL 인코딩된 한글 핸들
    """
    raw = raw.strip()
    if not raw:
        return ""

    # 맨 핸들: @handle 또는 handle (URL 구조 없음)
    if "/" not in raw and "." not in raw and not raw.startswith(("http://", "https://")):
        handle = raw if raw.startswith("@") else f"@{raw}"
        return f"https://www.youtube.com/{handle}"

    # 스킴 없으면 추가
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw

    parsed = urlparse(raw)
    path = parsed.path.rstrip("/")

    # /channel/UCxxxx 형식
    match = re.search(r"/channel/(UC[\w-]+)", path)
    if match:
        return f"https://www.youtube.com/channel/{match.group(1)}"

    # /@handle 형식
    if "/@" in path:
        handle_part = path.split("/@", 1)[1].split("/")[0]
        return f"https://www.youtube.com/@{handle_part}"

    # 기타 — 쿼리 파라미터만 제거하고 도메인 정규화
    return f"https://www.youtube.com{path}"


# -- 채널 스크래핑 --

def scrape_youtube_channels(channel_urls: list[str]) -> list[dict]:
    """Apify YouTube Scraper로 채널 정보 스크래핑.

    streamers/youtube-scraper 액터 사용.
    - startUrls: 채널 URL 리스트
    - maxResults/maxResultsShorts/maxResultStreams: 0 → 채널 정보만 (동영상 스크래핑 안 함)
    - 출력: channelName, numberOfSubscribers, channelUrl 등

    Returns:
        성공한 채널 결과 리스트. 없는 채널은 포함되지 않음 → 입력과 비교하여 누락 채널 감지 가능.
    """
    token = config.APIFY_API_TOKEN
    if not token:
        raise RuntimeError("APIFY_API_TOKEN이 설정되지 않았습니다.")

    # 정규화
    normalized = [normalize_youtube_url(u) for u in channel_urls]
    valid_urls = [u for u in normalized if u]
    if not valid_urls:
        return []

    client = ApifyClient(token)
    run_input = {
        "startUrls": [{"url": url} for url in valid_urls],
        "maxResults": 0,
        "maxResultsShorts": 0,
        "maxResultStreams": 0,
    }
    logger.info("Apify YouTube 스크래핑 시작: %d개 채널", len(valid_urls))
    run = client.actor(ACTOR_ID).call(run_input=run_input)

    # 입력 URL → 정규화 URL 매핑 (원본 보존용)
    norm_to_orig = {}
    for orig, norm in zip(channel_urls, normalized):
        if norm:
            norm_to_orig[norm.lower()] = orig.strip()

    # @handle 기반 매핑 (channelUrl이 /channel/UCxxx 형식으로 올 때 대비)
    handle_to_orig = {}
    for orig, norm in zip(channel_urls, normalized):
        if norm and "/@" in norm:
            handle = norm.split("/@", 1)[1].split("/")[0].lower()
            if handle:
                handle_to_orig[handle] = orig.strip()

    results = []
    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        channel_name = item.get("channelName", "")
        channel_url = item.get("channelUrl", "")
        input_channel_url = item.get("inputChannelUrl", "")
        subscriber_count = item.get("numberOfSubscribers", 0)

        if not channel_name:
            logger.warning("채널명 없음 (건너뜀): url=%s", channel_url)
            continue

        # 매칭 우선순위:
        # ① inputChannelUrl (Apify가 제공하는 원본 입력 URL) → 정규화 → 매칭
        # ② channelUrl → 정규화 → 매칭
        # ③ 부분 문자열 매칭
        # ④ @handle 기반 매칭 (channelUsername 또는 URL에서 추출)
        matched_input_url = None

        # ① inputChannelUrl로 매칭
        if input_channel_url:
            input_norm = normalize_youtube_url(input_channel_url).lower()
            matched_input_url = norm_to_orig.get(input_norm)

        # ② channelUrl로 매칭
        if not matched_input_url and channel_url:
            apify_norm = normalize_youtube_url(channel_url).lower()
            matched_input_url = norm_to_orig.get(apify_norm)

        # ③ 부분 문자열 매칭
        if not matched_input_url:
            apify_norm = normalize_youtube_url(channel_url).lower() if channel_url else ""
            for norm_key, orig_val in norm_to_orig.items():
                if norm_key in apify_norm or apify_norm in norm_key:
                    matched_input_url = orig_val
                    break

        # ④ @handle / channelUsername 기반 매칭
        if not matched_input_url:
            handle = item.get("channelUsername", "").lower()
            if not handle and channel_url and "/@" in channel_url:
                handle = channel_url.split("/@", 1)[1].split("/")[0].lower()
            if not handle and input_channel_url and "/@" in input_channel_url:
                handle = input_channel_url.split("/@", 1)[1].split("/")[0].lower()
            if handle and handle in handle_to_orig:
                matched_input_url = handle_to_orig[handle]

        # 최종 폴백: 원본 channelUrl 사용
        if not matched_input_url:
            matched_input_url = channel_url
            logger.warning("채널 URL 매칭 실패 (폴백): input=%s, apify=%s", input_channel_url, channel_url)

        answer = extract_youtube_answer(channel_name)
        results.append({
            "channel_url": matched_input_url,
            "channel_name": channel_name,
            "정답": answer,
            "subscriber_count": subscriber_count,
        })

    logger.info("Apify YouTube 스크래핑 완료: %d/%d개 결과", len(results), len(valid_urls))
    return results


def extract_youtube_answer(channel_name: str) -> str:
    """채널명에서 한글(2+자)/영문(2+자)만 추출. 특수문자/일본어/중국어 제거."""
    korean = re.findall(r"[가-힣]{2,}", channel_name)
    if korean:
        return korean[0][:4]

    eng = re.findall(r"[A-Za-z]{2,}", channel_name)
    if eng:
        return eng[0]

    return "구독자"


# -- 주문 조회 (Admin API v2) --

def fetch_youtube_pending_orders(service_id: str = "129", limit: int = 100) -> list[dict]:
    """인스타몬스터 Admin API에서 유튜브 구독자 대기 주문을 가져온다.

    URL을 정규화하여 반환. 잘못된 URL은 invalid로 표시.
    """
    key = config.INSTAMONSTER_ADMIN_API_KEY
    if not key:
        raise RuntimeError("INSTAMONSTER_ADMIN_API_KEY가 설정되지 않았습니다.")

    resp = requests.post(
        f"{config.INSTAMONSTER_ADMIN_API_URL}/orders/pull",
        headers={"X-Api-Key": key, "Content-Type": "application/json"},
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
        raw_link = order.get("link", "").strip()
        if not raw_link:
            continue
        normalized = normalize_youtube_url(raw_link)
        results.append({
            "order_id": order.get("id"),
            "channel_url": normalized,
            "raw_link": raw_link,
            "수량": str(order.get("quantity", "")),
        })
    return results


def cancel_youtube_orders(order_ids: list[int]) -> dict:
    """인스타몬스터 주문 취소."""
    if not order_ids:
        return {"ok": True, "cancelled": 0}

    key = config.INSTAMONSTER_ADMIN_API_KEY
    if not key:
        raise RuntimeError("INSTAMONSTER_ADMIN_API_KEY가 설정되지 않았습니다.")

    ids_str = ",".join(str(oid) for oid in order_ids)
    resp = requests.post(
        f"{config.INSTAMONSTER_ADMIN_API_URL}/orders/cancel",
        headers={"X-Api-Key": key, "Content-Type": "application/json"},
        json={"ids": ids_str},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    logger.info("유튜브 주문 취소: %s → %s", ids_str, data)
    return {"ok": True, "cancelled": len(order_ids), "response": data}


# -- 이력 관리 --

def load_youtube_history() -> dict:
    if os.path.isfile(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_youtube_history(history: dict):
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def check_and_update_youtube_history(results: list[dict]) -> list[dict]:
    """스크래핑 결과를 이력과 대조하여 신규/연장 구분."""
    history = load_youtube_history()
    today = datetime.now().strftime("%Y-%m-%d")

    for row in results:
        channel_url = row["channel_url"]
        if channel_url in history:
            prev = history[channel_url]
            row["구분"] = "캠페인 연장"
            row["최초등록일"] = prev["최초등록일"]
            row["조회횟수"] = prev["조회횟수"] + 1
            prev["조회횟수"] = row["조회횟수"]
            prev["최근조회일"] = today
            prev["이력"].append(today)
        else:
            row["구분"] = "신규"
            row["최초등록일"] = today
            row["조회횟수"] = 1
            history[channel_url] = {
                "최초등록일": today,
                "최근조회일": today,
                "조회횟수": 1,
                "이력": [today],
                "channel_name": row["channel_name"],
            }

    save_youtube_history(history)
    return results
