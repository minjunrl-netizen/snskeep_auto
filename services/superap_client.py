"""superap.io 캠페인 자동 등록/연장 클라이언트."""

import json
import logging
import math
import os
from datetime import datetime, timedelta

import requests

import config

logger = logging.getLogger(__name__)

BASE_URL = "https://superap.io"
LOGIN_URL = f"{BASE_URL}/j_spring_security_check"
CAMPAIGN_LIST_URL = f"{BASE_URL}/service/reward/adver/report/csv"
CAMPAIGN_ADD_URL = f"{BASE_URL}/service/reward/adver/add/post"
CAMPAIGN_MODIFY_URL = f"{BASE_URL}/service/reward/adver/modify/post"
TYPE_LIST_URL = f"{BASE_URL}/service/reward/adver/type/list"
PRICE_LIST_URL = f"{BASE_URL}/service/reward/adver/price/list"

DETAIL_TYPE = "sns_instagram_follow"
IMG_BASE_URL = "https://webapp.superap.io/res/"

# username → ad_idx 매핑 파일 (superap.io가 캠페인명의 _를 잘라서 이름 매칭 불가)
CAMPAIGN_MAP_FILE = os.path.join(config.BASE_DIR, "data", "campaign_map.json")

# 캠페인 설정 파일
CAMPAIGN_SETTINGS_FILE = os.path.join(config.BASE_DIR, "data", "campaign_settings.json")

DEFAULT_CAMPAIGN_SETTINGS = {
    "title_template": "인스타 팔로우 하고 포인트 적립받기 (언팔x) {username}",
    "description": "",
    "budget_multiplier": 1.2,
    "duration_days": 30,
    "geo": "kr",
    "event_limit": "1",
    "img1_url": "",
    "img2_url": "",
    "adsome_type": "RCPA",
    "target_media_ids": [],
}

# ── 유튜브 캠페인 설정 ──
YOUTUBE_CAMPAIGN_MAP_FILE = os.path.join(config.BASE_DIR, "data", "youtube_campaign_map.json")
YOUTUBE_CAMPAIGN_SETTINGS_FILE = os.path.join(config.BASE_DIR, "data", "youtube_campaign_settings.json")
DEFAULT_YOUTUBE_CAMPAIGN_SETTINGS = {
    "title_template": "유튜브 구독하고 포인트 적립받기 {username}",
    "detail_type": "",
    "description": "",
    "budget_multiplier": 1.2,
    "duration_days": 30,
    "geo": "kr",
    "event_limit": "1",
    "img1_url": "",
    "img2_url": "",
    "adsome_type": "RCPA",
    "target_media_ids": [],
}


def _get_settings_paths(campaign_type: str = "instagram") -> tuple[str, dict]:
    """campaign_type에 따라 설정 파일 경로와 기본값 반환."""
    if campaign_type == "youtube":
        return YOUTUBE_CAMPAIGN_SETTINGS_FILE, DEFAULT_YOUTUBE_CAMPAIGN_SETTINGS
    return CAMPAIGN_SETTINGS_FILE, DEFAULT_CAMPAIGN_SETTINGS


def _get_map_path(campaign_type: str = "instagram") -> str:
    """campaign_type에 따라 매핑 파일 경로 반환."""
    if campaign_type == "youtube":
        return YOUTUBE_CAMPAIGN_MAP_FILE
    return CAMPAIGN_MAP_FILE


def load_campaign_settings(campaign_type: str = "instagram") -> dict:
    """캠페인 설정 로드. 파일 없으면 기본값 반환."""
    settings_file, defaults = _get_settings_paths(campaign_type)
    if os.path.isfile(settings_file):
        try:
            with open(settings_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
            # 기본값과 병합 (키 누락 방지)
            merged = {**defaults, **saved}
            return merged
        except Exception:
            logger.exception("캠페인 설정 로드 실패, 기본값 사용")
    return dict(defaults)


def save_campaign_settings(settings: dict, campaign_type: str = "instagram"):
    """캠페인 설정 저장."""
    settings_file, _ = _get_settings_paths(campaign_type)
    os.makedirs(os.path.dirname(settings_file), exist_ok=True)
    with open(settings_file, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def _load_campaign_map(campaign_type: str = "instagram") -> dict:
    """로컬 username→ad_idx 매핑 로드."""
    map_file = _get_map_path(campaign_type)
    if os.path.isfile(map_file):
        with open(map_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_campaign_map(mapping: dict, campaign_type: str = "instagram"):
    """로컬 username→ad_idx 매핑 저장."""
    map_file = _get_map_path(campaign_type)
    os.makedirs(os.path.dirname(map_file), exist_ok=True)
    with open(map_file, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)


class SuperapClient:
    """superap.io 세션 관리 및 캠페인 자동화."""

    def __init__(self, campaign_type: str = "instagram"):
        self.session = requests.Session()
        self._logged_in = False
        self._type_data = None
        self._price_data = None
        self.campaign_type = campaign_type

    @property
    def detail_type(self) -> str:
        """인스타그램이면 고정값, 유튜브면 설정 파일에서 로드."""
        if self.campaign_type == "youtube":
            settings = load_campaign_settings("youtube")
            dt = settings.get("detail_type", "")
            if not dt:
                raise RuntimeError("유튜브 캠페인 detail_type이 설정되지 않았습니다. 캠페인 설정에서 입력해주세요.")
            return dt
        return DETAIL_TYPE

    def login(self) -> bool:
        username = config.SUPERAP_USERNAME
        password = config.SUPERAP_PASSWORD
        if not username or not password:
            raise RuntimeError("SUPERAP_USERNAME / SUPERAP_PASSWORD가 설정되지 않았습니다.")

        resp = self.session.post(LOGIN_URL, data={
            "j_username": username,
            "j_password": password,
        }, allow_redirects=True, timeout=30)

        if resp.status_code == 200 and "/login" not in resp.url:
            self._logged_in = True
            return True
        raise RuntimeError("superap.io 로그인 실패")

    def _ensure_login(self):
        if not self._logged_in:
            self.login()

    def _build_image_fields(self) -> list[tuple]:
        """image_url 필드 3개 (icon, img1, img2).

        설정에 커스텀 이미지 URL이 있으면 사용, 없으면 type_data 기본값.
        """
        td = self.get_type_data()
        settings = load_campaign_settings(self.campaign_type)

        icon = IMG_BASE_URL + td.get("icon_url", "sns_icon.png")
        img1 = settings.get("img1_url") or (IMG_BASE_URL + td.get("img1_url", ""))
        img2 = settings.get("img2_url") or (IMG_BASE_URL + td.get("img2_url", ""))

        return [
            ("image_url", icon),
            ("image_url", img1),
            ("image_url", img2),
        ]

    def _build_media_fields(self) -> list[tuple]:
        """targetMediaIds 필드 (매체 타겟팅).

        설정에 target_media_ids가 있으면 사용, 없으면 전체 선택(필드 안 보냄).
        """
        settings = load_campaign_settings(self.campaign_type)
        media_ids = settings.get("target_media_ids", [])
        if not media_ids:
            return []
        return [("targetMediaIds", str(mid)) for mid in media_ids]

    # ── 타입/가격 정보 ───────────────────────────────────

    def get_type_data(self) -> dict:
        """detail_type에 해당하는 타입 정보."""
        if self._type_data:
            return self._type_data

        self._ensure_login()
        dt = self.detail_type
        resp = self.session.get(TYPE_LIST_URL, timeout=30)
        data = resp.json()
        if data.get("result") != 200:
            raise RuntimeError("캠페인 타입 조회 실패")

        for item in data["data"]:
            if item["detail_type"] == dt:
                self._type_data = item
                return item

        raise RuntimeError(f"{dt} 타입을 찾을 수 없습니다.")

    def get_publishers(self) -> list[dict]:
        """매체 타겟팅 목록 조회."""
        self._ensure_login()
        resp = self.session.get(
            f"{BASE_URL}/service/reward/adver/publishers",
            params={"mode": "add"},
            timeout=30,
        )
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError("매체 타겟팅 목록 조회 실패")
        return data.get("data", [])

    def get_price(self) -> int:
        """detail_type에 해당하는 단가."""
        if self._price_data is not None:
            return self._price_data

        self._ensure_login()
        dt = self.detail_type
        resp = self.session.get(PRICE_LIST_URL, timeout=30)
        data = resp.json()
        if data.get("result") != 200:
            raise RuntimeError("가격 정보 조회 실패")

        for item in data["data"]:
            if item["detail_type"] == dt:
                self._price_data = int(item["price"])
                return self._price_data

        raise RuntimeError(f"{dt} 가격을 찾을 수 없습니다.")

    # ── 기존 캠페인 조회 ─────────────────────────────────

    def get_all_campaigns(self) -> list[dict]:
        """전체 캠페인 목록."""
        self._ensure_login()
        resp = self.session.get(CAMPAIGN_LIST_URL, timeout=60)
        data = resp.json()
        return data.get("data", [])

    def find_campaigns_by_username(self, username: str, campaigns: list[dict] = None) -> list[dict]:
        """username에 해당하는 모든 캠페인 (최신순)."""
        if campaigns is None:
            campaigns = self.get_all_campaigns()
        matches = [
            c for c in campaigns
            if c.get("ad_name", "").endswith(f" {username}")
        ]
        matches.sort(key=lambda c: c["ad_idx"], reverse=True)
        return matches

    def get_campaign_url_username(self, ad_idx: str) -> str | None:
        """수정 페이지에서 캠페인의 실제 URL을 조회하여 username 추출.

        CSV 목록 API에서는 url이 항상 null이므로,
        수정 페이지(/service/reward/adver/mod)에서 URL을 가져온다.
        언더스코어(_)로 인해 이름이 잘린 캠페인도 URL로 정확한 아이디 파악 가능.
        """
        self._ensure_login()
        import re
        try:
            resp = self.session.get(
                f"{BASE_URL}/service/reward/adver/mod?ad_idx={ad_idx}",
                timeout=15,
            )
            if resp.status_code != 200:
                return None
            # input name="url" value="https://www.instagram.com/USERNAME/#..." 추출
            url_match = re.findall(r'name="url"[^>]*value="([^"]*)"', resp.text)
            if not url_match:
                return None
            url_val = url_match[0]  # e.g. https://www.instagram.com/maven_jihye/#sns_instagram_follow
            # URL에서 username 추출
            from services.profile_extractor import extract_username_from_link
            # # 앞까지만 사용
            clean_url = url_val.split("#")[0]
            return extract_username_from_link(clean_url) or None
        except Exception:
            logger.exception("캠페인 URL 조회 실패: ad_idx=%s", ad_idx)
            return None

    def _scrape_answer(self, username: str) -> str:
        """Apify로 프로필 스크래핑 후 정답 추출. 실패 시 폴백 반환."""
        if self.campaign_type == "youtube":
            from services.youtube_scraper import scrape_youtube_channels, extract_youtube_answer
            try:
                results = scrape_youtube_channels([username])
                if results:
                    answer = results[0].get("정답", "")
                    if answer:
                        logger.info("Apify 유튜브 정답 추출: %s → %s", username, answer)
                        return answer
            except Exception:
                logger.exception("Apify 유튜브 정답 추출 실패: %s", username)
            logger.info("유튜브 정답 폴백 사용: %s → 구독자", username)
            return "구독자"

        from services.profile_extractor import scrape_profiles, extract_answer
        try:
            profiles = scrape_profiles([username])
            if profiles:
                answer = profiles[0].get("정답", "")
                if answer:
                    logger.info("Apify 정답 추출: %s → %s", username, answer)
                    return answer
        except Exception:
            logger.exception("Apify 정답 추출 실패: %s", username)
        fallback = ["게시물", "팔로워"]
        result = fallback[hash(username) % len(fallback)]
        logger.info("정답 폴백 사용: %s → %s", username, result)
        return result

    def _get_existing_event_name(self, ad_idx: str) -> str | None:
        """수정 페이지에서 기존 ad_event_name(전환인식기준) 값을 읽어온다."""
        import re
        try:
            resp = self.session.get(
                f"{BASE_URL}/service/reward/adver/mod?ad_idx={ad_idx}",
                timeout=15,
            )
            if resp.status_code != 200:
                return None
            match = re.findall(r'name="ad_event_name"[^>]*value="([^"]*)"', resp.text)
            return match[0] if match else None
        except Exception:
            logger.exception("기존 ad_event_name 조회 실패: ad_idx=%s", ad_idx)
            return None

    # ── 신규 캠페인 등록 ─────────────────────────────────

    def create_campaign(self, username: str, quantity: int, link: str = None,
                        answer: str = "", channel_name: str = "") -> dict:
        """새 캠페인 등록.

        Args:
            username: 인스타그램 아이디 또는 유튜브 채널 URL
            quantity: 주문 수량
            link: 프로필/채널 링크
            answer: 전환인식기준 (정답). 비어있으면 기본값 사용.
            channel_name: 유튜브 채널명 (스크래핑 결과). 캠페인 제목에 사용.
        """
        self._ensure_login()
        type_data = self.get_type_data()
        price = self.get_price()
        dt = self.detail_type
        settings = load_campaign_settings(self.campaign_type)

        # 유튜브: link를 직접 URL로 사용 / 인스타: username에서 URL 생성
        if self.campaign_type == "youtube":
            clean_link = link or username
            url = f"{clean_link}#{dt}"
        else:
            clean_link = f"https://www.instagram.com/{username}/"
            url = f"{clean_link}#{dt}"

        total_budget = math.ceil(quantity * settings["budget_multiplier"])
        today = datetime.now()
        begin_date = today.strftime("%Y-%m-%d 00:00:00")
        end_date = (today + timedelta(days=settings["duration_days"])).strftime("%Y-%m-%d 23:59:59")

        # 전환인식기준: 정답이 있으면 사용, 없으면 Apify 스크래핑 → 폴백
        event_name = answer if answer else self._scrape_answer(username)

        # description: 설정값이 있으면 사용, 비어있으면 API 기본값
        description = settings["description"] if settings["description"] else type_data["description"]

        # 캠페인 제목용 이름: 유튜브는 채널명 우선, 없으면 핸들 추출
        if self.campaign_type == "youtube" and channel_name:
            display_name = channel_name
        else:
            display_name = username
            if self.campaign_type == "youtube":
                if "/@" in username:
                    display_name = username.split("/@", 1)[1].split("/")[0]
                elif "/channel/" in username:
                    display_name = username.split("/channel/", 1)[1].split("/")[0]
                elif username.startswith("https://"):
                    display_name = username.rsplit("/", 1)[-1]
            display_name = display_name.lstrip("@").replace("_", "")

        form_data = {
            "ad_title": settings["title_template"].format(username=display_name),
            "detail_type": dt,
            "total_budget": str(total_budget),
            "day_budget": str(total_budget),
            "description": description,
            "target_package": "",
            "search_keyword": "",
            "begin_date": begin_date,
            "end_date": end_date,
            "url": url,
            "geo": settings["geo"],
            "adsomeType": settings.get("adsome_type", "RCPA"),
            "ad_event_name": event_name,
            "ad_event_limit": settings["event_limit"],
            "conversion": type_data["conversion"],
            "ad_charge_price": str(price),
        }

        files = [(k, (None, v)) for k, v in form_data.items()]
        extra_data = self._build_image_fields() + self._build_media_fields()

        resp = self.session.post(CAMPAIGN_ADD_URL, data=extra_data, files=files,
                                 timeout=30, allow_redirects=False)

        # /add/post 엔드포인트: 성공 시 302 리다이렉트, 실패 시 200(폼 재표시) 또는 에러
        if resp.status_code == 302:
            ad_idx = self._save_username_mapping(username)
            self._apply_media_targeting_after_create(ad_idx, username)
            return {"ok": True, "message": "캠페인 등록 성공", "username": username}

        # JSON 응답 시도 (이전 /csv 엔드포인트 호환)
        try:
            result = resp.json()
            if result.get("result") == 200:
                ad_idx = self._save_username_mapping(username)
                self._apply_media_targeting_after_create(ad_idx, username)
                return {"ok": True, "message": "캠페인 등록 성공", "username": username}
            return {"ok": False, "message": f"등록 실패: {result}", "username": username}
        except Exception:
            # 500 응답이지만 실제로 등록된 경우 검증
            if resp.status_code == 500:
                logger.warning("캠페인 등록 500 응답 — %s 검증 중...", username)
                try:
                    verify_campaigns = self.get_all_campaigns()
                    clean_username = username.replace("_", "")
                    matched = [
                        c for c in verify_campaigns
                        if c.get("ad_name", "").endswith(f" {username}")
                        or c.get("ad_name", "").endswith(f" {clean_username}")
                    ]
                    best = max(matched, key=lambda c: c["ad_idx"], default=None) if matched else None
                    if best:
                        ad_idx = self._save_username_mapping(username)
                        self._apply_media_targeting_after_create(ad_idx, username)
                        return {"ok": True, "message": "캠페인 등록 성공 (검증됨)", "username": username}
                except Exception:
                    pass
            return {"ok": False, "message": f"응답 파싱 실패 (status={resp.status_code})", "username": username}

    def _apply_media_targeting_after_create(self, ad_idx: str | None, username: str):
        """신규 캠페인 생성 후 매체 타겟팅 적용.

        /add/post는 data= 파라미터의 targetMediaIds를 처리하지 않으므로,
        생성 직후 /modify/post로 매체 타겟팅을 재적용한다.
        """
        if not ad_idx:
            return
        settings = load_campaign_settings(self.campaign_type)
        media_ids = settings.get("target_media_ids", [])
        if not media_ids:
            return
        try:
            result = self.update_campaign(ad_idx=ad_idx, username=username)
            if result.get("ok"):
                logger.info("신규 캠페인 매체 타겟팅 적용: %s → ad_idx=%s", username, ad_idx)
            else:
                logger.warning("신규 캠페인 매체 타겟팅 적용 실패: %s → %s", username, result.get("message"))
        except Exception:
            logger.exception("신규 캠페인 매체 타겟팅 적용 중 오류: %s", username)

    def _save_username_mapping(self, username: str) -> str | None:
        """캠페인 생성 후 username→ad_idx 매핑 저장. ad_idx 반환."""
        try:
            all_campaigns = self.get_all_campaigns()
            # username으로 캠페인 필터링 (superap이 _를 제거하므로 양쪽 비교)
            clean_username = username.replace("_", "")
            best = None
            for c in all_campaigns:
                ad_name = c.get("ad_name", "")
                if ad_name.endswith(f" {username}") or ad_name.endswith(f" {clean_username}"):
                    if not best or c["ad_idx"] > best["ad_idx"]:
                        best = c
            if best:
                ad_idx = str(best["ad_idx"])
                mapping = _load_campaign_map(self.campaign_type)
                mapping[username] = ad_idx
                _save_campaign_map(mapping, self.campaign_type)
                logger.info("캠페인 매핑 저장: %s → %s", username, best["ad_idx"])
                return ad_idx
        except Exception:
            logger.exception("캠페인 매핑 저장 실패: %s", username)
        return None

    # ── 기존 캠페인 수정 (연장) ───────────────────────────

    def modify_campaign(self, ad_idx: str, username: str, add_quantity: int,
                        existing: dict, answer: str = "") -> dict:
        """기존 캠페인의 총한도/일일한도를 증가시키고 종료일을 연장.

        Args:
            ad_idx: 기존 캠페인 ad_idx
            username: 인스타그램 아이디 또는 유튜브 채널 URL
            add_quantity: 추가할 수량 (×1.2 적용)
            existing: 기존 캠페인 데이터 (CSV에서 가져온 것)
            answer: 전환인식기준 (정답). 비어있으면 기본값 사용.
        """
        self._ensure_login()
        type_data = self.get_type_data()
        price = self.get_price()
        dt = self.detail_type
        settings = load_campaign_settings(self.campaign_type)

        # 실제 전환수(action_count) 기준으로 예산 재계산
        # total_budget 기준이면 미소진분이 새 주문과 합쳐지는 문제 발생
        # 예: 100명 주문(한도120) → 70명만 수행 → 새 100명 주문 시
        #     기존: 120 + 120 = 240 (미소진 50명분 합쳐짐)
        #     수정: 70 + 120 = 190 (새 주문 100명분만 추가)
        action_count = int(existing.get("action_count", 0) or 0)
        current_budget = int(existing.get("total_budget", 0))
        add_budget = math.ceil(add_quantity * settings["budget_multiplier"])
        new_budget = action_count + add_budget

        # 날짜: 항상 오늘 ~ +duration_days로 재설정
        today = datetime.now()
        begin_date = today.strftime("%Y-%m-%d 00:00:00")
        new_end = (today + timedelta(days=settings["duration_days"])).strftime("%Y-%m-%d 23:59:59")

        # 유튜브: link를 직접 URL로 사용 / 인스타: username에서 URL 생성
        if self.campaign_type == "youtube":
            link = username  # username이 실제로는 채널 URL
            url = f"{link}#{dt}"
        else:
            link = f"https://www.instagram.com/{username}/"
            url = f"{link}#{dt}"

        # 전환인식기준: 정답이 있으면 사용, 없으면 Apify 스크래핑 → 폴백
        event_name = answer if answer else self._scrape_answer(username)

        # description: 설정값이 있으면 사용, 비어있으면 API 기본값
        description = settings["description"] if settings["description"] else type_data["description"]

        # 캠페인 제목용 이름 (폴백)
        display_name = username
        if self.campaign_type == "youtube":
            if "/@" in username:
                display_name = username.split("/@", 1)[1].split("/")[0]
            elif "/channel/" in username:
                display_name = username.split("/channel/", 1)[1].split("/")[0]
            elif username.startswith("https://"):
                display_name = username.rsplit("/", 1)[-1]
        display_name = display_name.lstrip("@").replace("_", "")

        form_data = {
            "ad_idx": str(ad_idx),
            "ad_title": existing.get("ad_name", settings["title_template"].format(username=display_name)),
            "detail_type": dt,
            "total_budget": str(new_budget),
            "day_budget": str(new_budget),
            "description": description,
            "target_package": "",
            "search_keyword": "",
            "begin_date": begin_date,
            "end_date": new_end,
            "url": url,
            "geo": settings["geo"],
            "adsomeType": settings.get("adsome_type", "RCPA"),
            "ad_event_name": event_name,
            "ad_event_limit": settings["event_limit"],
            "conversion": type_data["conversion"],
            "ad_charge_price": str(price),
        }

        files = [(k, (None, v)) for k, v in form_data.items()]
        extra_data = self._build_image_fields() + self._build_media_fields()

        resp = self.session.post(CAMPAIGN_MODIFY_URL, data=extra_data, files=files,
                                 timeout=30, allow_redirects=False)

        if resp.status_code == 302:
            return {
                "ok": True,
                "message": f"캠페인 수정 완료 (전환수 {action_count}, 한도 {current_budget} → {new_budget}, +{add_budget})",
                "username": username,
                "ad_idx": ad_idx,
                "old_budget": current_budget,
                "new_budget": new_budget,
                "action_count": action_count,
            }

        # 서버가 500을 반환해도 실제로 수정이 적용되는 경우가 있음 → 검증
        if resp.status_code == 500:
            logger.warning("캠페인 수정 500 응답 — %s 검증 중...", username)
            verify_resp = self.session.get(CAMPAIGN_LIST_URL, timeout=60)
            verify_data = verify_resp.json()
            for c in verify_data.get("data", []):
                if str(c.get("ad_idx")) == str(ad_idx):
                    actual_budget = int(c.get("total_budget", 0))
                    if actual_budget >= new_budget:
                        logger.info("캠페인 수정 검증 성공 — %s (한도 %d, 전환수 %d)", username, actual_budget, action_count)
                        return {
                            "ok": True,
                            "message": f"캠페인 수정 완료 (전환수 {action_count}, 한도 {current_budget} → {actual_budget}, +{add_budget})",
                            "username": username,
                            "ad_idx": ad_idx,
                            "old_budget": current_budget,
                            "new_budget": actual_budget,
                            "action_count": action_count,
                        }
                    break

        return {
            "ok": False,
            "message": f"캠페인 수정 실패 (status={resp.status_code})",
            "username": username,
        }

    # ── 캠페인 수정 (예산/정답만 변경) ────────────────────

    def update_campaign(self, ad_idx: str, username: str, total_budget: int = None,
                        answer: str = None) -> dict:
        """기존 캠페인의 특정 필드만 수정 (예산 보정, 정답 변경 등).

        Args:
            ad_idx: 캠페인 ad_idx
            username: 인스타그램 아이디 또는 유튜브 채널 URL
            total_budget: 새 total_budget (None이면 기존 유지)
            answer: 새 전환인식기준 (None이면 기존 유지)
        """
        self._ensure_login()
        type_data = self.get_type_data()
        price = self.get_price()
        dt = self.detail_type
        settings = load_campaign_settings(self.campaign_type)

        # 기존 캠페인 데이터 조회
        all_campaigns = self.get_all_campaigns()
        existing = None
        for c in all_campaigns:
            if str(c.get("ad_idx")) == str(ad_idx):
                existing = c
                break

        if not existing:
            return {"ok": False, "message": f"캠페인 {ad_idx} 없음", "username": username}

        budget = total_budget if total_budget is not None else int(existing.get("total_budget", 0))

        # answer=None이면 수정 페이지에서 기존 ad_event_name을 읽어서 보존
        if answer is not None:
            event_name = answer
        else:
            event_name = self._get_existing_event_name(ad_idx)
            if not event_name:
                event_name = self._scrape_answer(username)

        today = datetime.now()
        begin_date = today.strftime("%Y-%m-%d 00:00:00")
        new_end = (today + timedelta(days=settings["duration_days"])).strftime("%Y-%m-%d 23:59:59")

        # 유튜브: link를 직접 URL로 사용 / 인스타: username에서 URL 생성
        if self.campaign_type == "youtube":
            link = username
            url = f"{link}#{dt}"
        else:
            link = f"https://www.instagram.com/{username}/"
            url = f"{link}#{dt}"

        # description: 설정값이 있으면 사용, 비어있으면 API 기본값
        description = settings["description"] if settings["description"] else type_data["description"]

        # 캠페인 제목용 이름 (폴백)
        display_name = username
        if self.campaign_type == "youtube":
            if "/@" in username:
                display_name = username.split("/@", 1)[1].split("/")[0]
            elif "/channel/" in username:
                display_name = username.split("/channel/", 1)[1].split("/")[0]
            elif username.startswith("https://"):
                display_name = username.rsplit("/", 1)[-1]
        display_name = display_name.lstrip("@").replace("_", "")

        form_data = {
            "ad_idx": str(ad_idx),
            "ad_title": existing.get("ad_name", settings["title_template"].format(username=display_name)),
            "detail_type": dt,
            "total_budget": str(budget),
            "day_budget": str(budget),
            "description": description,
            "target_package": "",
            "search_keyword": "",
            "begin_date": begin_date,
            "end_date": new_end,
            "url": url,
            "geo": settings["geo"],
            "adsomeType": settings.get("adsome_type", "RCPA"),
            "ad_event_name": event_name,
            "ad_event_limit": settings["event_limit"],
            "conversion": type_data["conversion"],
            "ad_charge_price": str(price),
        }

        files = [(k, (None, v)) for k, v in form_data.items()]
        extra_data = self._build_image_fields() + self._build_media_fields()

        resp = self.session.post(CAMPAIGN_MODIFY_URL, data=extra_data, files=files,
                                 timeout=30, allow_redirects=False)

        if resp.status_code == 302:
            return {"ok": True, "message": f"캠페인 업데이트 완료 (budget={budget}, answer={event_name})", "username": username}

        # 500 검증
        if resp.status_code == 500:
            verify_resp = self.session.get(CAMPAIGN_LIST_URL, timeout=60)
            verify_data = verify_resp.json()
            for c in verify_data.get("data", []):
                if str(c.get("ad_idx")) == str(ad_idx):
                    if int(c.get("total_budget", 0)) == budget:
                        return {"ok": True, "message": f"캠페인 업데이트 완료 (검증됨, budget={budget}, answer={event_name})", "username": username}
                    break

        return {"ok": False, "message": f"캠페인 업데이트 실패 (status={resp.status_code})", "username": username}

    # ── 일괄 처리 (신규/연장 자동 판단) ──────────────────

    def process_orders_bulk(self, orders: list[dict]) -> list[dict]:
        """여러 주문을 일괄 처리.

        - 같은 아이디 주문은 수량 합산 후 1건으로 처리
        - 기존 캠페인 있음 → modify (총한도 + 수량×1.2)
        - 신규 아이디 → create (새 캠페인)

        Args:
            orders: [{"username": str, "quantity": int, "link": str, "answer": str, "channel_name": str (optional)}, ...]
        """
        self._ensure_login()

        # 같은 아이디 수량 합산
        merged: dict[str, dict] = {}
        for order in orders:
            username = order["username"]
            quantity = int(order.get("quantity", 0))
            if self.campaign_type == "youtube":
                link = order.get("link", username)
            else:
                link = order.get("link", f"https://www.instagram.com/{username}/")
            answer = order.get("answer", "")
            channel_name = order.get("channel_name", "")
            if username in merged:
                merged[username]["quantity"] += quantity
            else:
                merged[username] = {"username": username, "quantity": quantity, "link": link, "answer": answer, "channel_name": channel_name}

        # 캠페인 목록 한 번만 조회
        all_campaigns = self.get_all_campaigns()

        # ad_idx → 캠페인 데이터
        campaigns_by_idx: dict[str, dict] = {str(c["ad_idx"]): c for c in all_campaigns}

        # 1) 로컬 매핑 파일에서 username→ad_idx 먼저 확인
        local_map = _load_campaign_map(self.campaign_type)

        # 2) 이름 기반 매핑 (폴백 — 언더스코어 없는 이름에만 유효)
        name_map: dict[str, dict] = {}
        for c in all_campaigns:
            ad_name = c.get("ad_name", "")
            parts = ad_name.rsplit(" ", 1)
            if len(parts) == 2:
                uname = parts[1]
                if uname not in name_map or c["ad_idx"] > name_map[uname]["ad_idx"]:
                    name_map[uname] = c

        # 통합 매핑: 로컬 매핑 우선, 이름 매칭 폴백, URL 매칭 최종 폴백
        campaign_map: dict[str, dict] = {}
        for username in merged:
            # 1) 로컬 매핑에서 찾기
            if username in local_map:
                ad_idx = local_map[username]
                if ad_idx in campaigns_by_idx:
                    campaign_map[username] = campaigns_by_idx[ad_idx]
                    continue
            # 2) 이름 기반 매칭 폴백
            if username in name_map:
                campaign_map[username] = name_map[username]
                continue
            # 2-1) 유튜브: URL에서 핸들 추출 후 이름 매칭 (언더스코어 제거)
            if self.campaign_type == "youtube":
                handle = username
                if "/@" in username:
                    handle = username.split("/@", 1)[1].split("/")[0]
                elif "/channel/" in username:
                    handle = username.split("/channel/", 1)[1].split("/")[0]
                elif username.startswith("https://"):
                    handle = username.rsplit("/", 1)[-1]
                handle = handle.lstrip("@").replace("_", "")
                if handle in name_map:
                    campaign_map[username] = name_map[handle]
                    self._save_mapping_direct(username, str(name_map[handle]["ad_idx"]))
                    logger.info("유튜브 이름 기반 캠페인 매칭: %s → %s (핸들: %s)", username, name_map[handle]["ad_idx"], handle)
                    continue
            # 3) URL 기반 매칭 폴백 (인스타그램 전용 — 언더스코어로 이름이 잘린 캠페인 대응)
            if self.campaign_type == "instagram" and "_" in username:
                truncated = username.split("_")[0]
                candidates = [
                    c for c in all_campaigns
                    if c.get("ad_name", "").endswith(f" {truncated}")
                    and c.get("status") in ("Active", "TotalOff")
                ]
                # 최신(ad_idx 큰) 순으로 확인
                candidates.sort(key=lambda c: int(c["ad_idx"]), reverse=True)
                for c in candidates:
                    url_username = self.get_campaign_url_username(str(c["ad_idx"]))
                    if url_username == username:
                        campaign_map[username] = c
                        self._save_mapping_direct(username, str(c["ad_idx"]))
                        logger.info("URL 기반 캠페인 매칭: %s → %s (잘린 이름: %s)", username, c["ad_idx"], truncated)
                        break

        results = []
        for order in merged.values():
            username = order["username"]
            quantity = order["quantity"]
            link = order["link"]
            answer = order["answer"]
            channel_name = order.get("channel_name", "")

            existing = campaign_map.get(username)

            try:
                if existing:
                    result = self.modify_campaign(
                        ad_idx=existing["ad_idx"],
                        username=username,
                        add_quantity=quantity,
                        existing=existing,
                        answer=answer,
                    )
                    result["구분"] = "캠페인 연장"
                    # 연장 성공 시 매핑 저장
                    if result.get("ok"):
                        self._save_mapping_direct(username, str(existing["ad_idx"]))
                else:
                    result = self.create_campaign(username, quantity, link, answer=answer, channel_name=channel_name)
                    result["구분"] = "신규"
            except Exception as e:
                logger.exception(f"캠페인 처리 실패: {username}")
                result = {
                    "ok": False,
                    "message": str(e),
                    "username": username,
                    "구분": "오류",
                }

            results.append(result)

        return results

    def _save_mapping_direct(self, username: str, ad_idx: str):
        """username→ad_idx 매핑을 직접 저장."""
        try:
            mapping = _load_campaign_map(self.campaign_type)
            mapping[username] = ad_idx
            _save_campaign_map(mapping, self.campaign_type)
            logger.info("캠페인 매핑 저장: %s → %s", username, ad_idx)
        except Exception:
            logger.exception("캠페인 매핑 저장 실패: %s", username)
