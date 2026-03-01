"""인스타그램 프로필 추출 비즈니스 로직."""

import csv
import json
import logging
import os
import re
from datetime import datetime
from urllib.parse import urlparse

import requests
from apify_client import ApifyClient

import config

logger = logging.getLogger(__name__)

ACTOR_ID = "apify/instagram-profile-scraper"
HISTORY_FILE = os.path.join(config.BASE_DIR, "data", "이력.json")


# ── 프로필 스크래핑 ────────────────────────────────────

def scrape_profiles(usernames: list[str]) -> list[dict]:
    token = config.APIFY_API_TOKEN
    if not token:
        raise RuntimeError("APIFY_API_TOKEN이 설정되지 않았습니다.")

    client = ApifyClient(token)
    run = client.actor(ACTOR_ID).call(run_input={"usernames": usernames})

    results = []
    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        uname = item.get("username", "")
        error = item.get("error", "")

        # "Restricted profile"은 연령 제한 등으로 Apify가 접근 제한된 경우.
        # id가 있고 데이터가 정상 반환되면 유효한 계정으로 처리.
        if error == "Restricted profile" and item.get("id"):
            logger.info("연령 제한 계정 (유효): %s — id=%s, reason=%s",
                        uname, item.get("id"), item.get("restrictionReason", ""))
        elif error or not item.get("id"):
            logger.warning("프로필 무효 (건너뜀): %s — error=%s, id=%s",
                           uname, error, item.get("id", ""))
            continue

        full_name = item.get("fullName", "")
        biography = item.get("biography", "")

        results.append({
            "username": uname,
            "fullName": full_name,
            "정답": extract_answer(full_name, biography),
            "비공개": "비공개" if item.get("private", False) else "공개",
            "biography": biography,
        })

    return results


def extract_answer(full_name: str, biography: str) -> str:
    """fullName과 biography에서 정답을 추출한다 (한글 우선, 영문 차선)."""
    korean = re.findall(r"[가-힣]{2,}", full_name)
    if korean:
        return korean[0][:4]

    eng = re.findall(r"[A-Za-z]{2,}", full_name)
    if eng:
        return eng[0]

    korean = re.findall(r"[가-힣]{2,}", biography)
    if korean:
        return korean[0][:4]

    eng = re.findall(r"[A-Za-z]{2,}", biography)
    if eng:
        return eng[0]

    fallback = ["게시물", "팔로워", "팔로우"]
    return fallback[hash(full_name + biography) % len(fallback)]


# ── 주문 조회 (Admin API v2) ───────────────────────────

def fetch_pending_orders(service_id: str = "32", limit: int = 100) -> list[dict]:
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
        link = order.get("link", "")
        username = extract_username_from_link(link)
        if not username:
            continue
        results.append({
            "order_id": order.get("id"),
            "username": username,
            "수량": str(order.get("quantity", "")),
        })
    return results


# ── 이력 관리 ──────────────────────────────────────────

def load_history() -> dict:
    if os.path.isfile(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_history(history: dict):
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def check_and_update_history(results: list[dict]) -> list[dict]:
    history = load_history()
    today = datetime.now().strftime("%Y-%m-%d")

    for row in results:
        username = row["username"]
        if username in history:
            prev = history[username]
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
            history[username] = {
                "최초등록일": today,
                "최근조회일": today,
                "조회횟수": 1,
                "이력": [today],
                "fullName": row["fullName"],
            }

    save_history(history)
    return results


# ── 유틸리티 ───────────────────────────────────────────

def extract_username_from_link(link: str) -> str:
    link = link.strip()
    if not link:
        return ""
    if "instagram.com" in link.lower():
        link = link.replace("&amp;", "&")
        # 스킴 없는 URL 처리 (instagram.com/user → https://instagram.com/user)
        if not link.startswith(("http://", "https://")):
            link = "https://" + link
        parsed = urlparse(link)
        path = parsed.path.strip("/")
        if path:
            return path.split("/")[0].lower()
        return ""
    return link.lower()


def import_history_from_csv(filepath: str) -> tuple[int, int]:
    rows = []
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            with open(filepath, "r", encoding=enc) as f:
                rows = list(csv.DictReader(f))
            break
        except (UnicodeDecodeError, UnicodeError):
            continue

    if not rows:
        raise RuntimeError("CSV 파일을 읽을 수 없습니다.")
    if "Link" not in rows[0]:
        raise RuntimeError("CSV에 'Link' 컬럼이 없습니다.")

    history = load_history()
    new_count = 0
    update_count = 0

    for row in rows:
        username = extract_username_from_link(row.get("Link", ""))
        if not username:
            continue

        created_raw = row.get("Created", "").strip()
        date_str = created_raw.split(" ")[0] if created_raw else datetime.now().strftime("%Y-%m-%d")

        if username in history:
            prev = history[username]
            if date_str not in prev["이력"]:
                prev["이력"].append(date_str)
                prev["조회횟수"] = len(prev["이력"])
                prev["최근조회일"] = max(prev["이력"])
                update_count += 1
        else:
            history[username] = {
                "최초등록일": date_str,
                "최근조회일": date_str,
                "조회횟수": 1,
                "이력": [date_str],
                "fullName": "",
            }
            new_count += 1

    save_history(history)
    return new_count, update_count
