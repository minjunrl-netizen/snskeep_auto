import re
import logging

logger = logging.getLogger(__name__)

# 인스타그램 URL 패턴
INSTAGRAM_URL_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?instagram\.com/([a-zA-Z0-9_.]+)/?(?:\?.*)?",
    re.IGNORECASE,
)

# 인스타그램 포스트/릴스 URL 패턴
INSTAGRAM_POST_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.)?instagram\.com/(?:p|reel|reels)/([a-zA-Z0-9_-]+)/?(?:\?.*)?",
    re.IGNORECASE,
)

# @username 패턴
AT_USERNAME_PATTERN = re.compile(r"@([a-zA-Z0-9_.]+)")

# 순수 아이디 패턴 (영문, 숫자, ., _로만 구성)
PURE_USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_.]+$")

# ── 옵션명 자동 감지용 키워드 ──
# 인스타그램 아이디 관련 키워드
USERNAME_KEYWORDS = ["아이디", "인스타 아이디", "계정", "username", "닉네임", "유저"]
# 좋아요 수량 관련 키워드
LIKES_KEYWORDS = ["좋아요", "하트", "likes", "좋아요 구매", "좋아요 수량"]
# 게시물 수량 관련 키워드
POSTS_KEYWORDS = ["게시물", "포스트", "posts", "게시글", "올릴"]
# 팔로워 수량 관련 키워드
FOLLOWERS_KEYWORDS = ["팔로워", "팔로우", "follower", "followers"]
# 인스타그램 링크 관련 키워드
LINK_KEYWORDS = ["링크", "주소", "url", "인스타 링크"]
# 수량 관련 키워드 (일반)
QUANTITY_KEYWORDS = ["수량", "개수", "quantity"]


def _get_option_value(option):
    """옵션에서 값을 추출 (API 버전 호환).

    구버전: option_value = "300개" (문자열)
    신버전: option_value = {"option_text": "300개", "value_no": null} (dict)
    """
    value = option.get("option_value", "") or option.get("value", "")
    if isinstance(value, dict):
        return value.get("option_text", "") or ""
    return value or ""


def _option_name_matches_keywords(name, keywords):
    """옵션명이 키워드 목록 중 하나라도 포함하는지 확인"""
    name_lower = name.lower()
    for kw in keywords:
        if kw.lower() in name_lower:
            return True
    return False


def _find_option_by_keywords(items, keywords):
    """키워드로 옵션을 찾아서 (name, value) 반환"""
    for item in items:
        options = item.get("options", [])
        for option in options:
            name = option.get("option_name", "") or option.get("name", "")
            value = _get_option_value(option)
            if _option_name_matches_keywords(name, keywords):
                return name, value

        additional = item.get("additional_option", []) or []
        for opt in additional:
            name = opt.get("name", "")
            value = opt.get("value", "")
            if _option_name_matches_keywords(name, keywords):
                return name, value

        # additional_option_values (신규 API 형식: "옵션명=값")
        additional_values = item.get("additional_option_values", []) or []
        for opt in additional_values:
            raw = opt.get("value", "")
            if "=" in raw:
                opt_name, opt_val = raw.split("=", 1)
                if _option_name_matches_keywords(opt_name, keywords):
                    return opt_name, opt_val

    return None, None


def normalize_link(raw_input):
    """인스타그램 링크/아이디를 정규화된 URL로 변환

    - URL → 그대로 반환
    - @username → https://www.instagram.com/username/
    - 순수 아이디 → https://www.instagram.com/username/
    """
    if not raw_input:
        return None

    text = raw_input.strip()

    # 이미 인스타그램 포스트/릴스 URL인 경우
    post_match = INSTAGRAM_POST_PATTERN.search(text)
    if post_match:
        return text if text.startswith("http") else f"https://{text}"

    # 이미 인스타그램 프로필 URL인 경우
    url_match = INSTAGRAM_URL_PATTERN.search(text)
    if url_match:
        return text if text.startswith("http") else f"https://{text}"

    # @username 형태
    at_match = AT_USERNAME_PATTERN.search(text)
    if at_match:
        username = at_match.group(1)
        return f"https://www.instagram.com/{username}/"

    # 순수 아이디
    if PURE_USERNAME_PATTERN.match(text):
        return f"https://www.instagram.com/{text}/"

    return None


def extract_link_from_option(items, option_name):
    """주문 항목의 옵션에서 인스타그램 링크 추출"""
    for item in items:
        options = item.get("options", [])
        for option in options:
            name = option.get("option_name", "") or option.get("name", "")
            value = _get_option_value(option)

            # 옵션명이 매칭되거나, 옵션명이 비어있으면 모든 옵션에서 찾기
            if option_name and option_name not in name:
                continue

            link = normalize_link(value)
            if link:
                return link

        # 옵션 외에 추가 입력 항목 확인
        additional = item.get("additional_option", []) or []
        for opt in additional:
            value = opt.get("value", "")
            link = normalize_link(value)
            if link:
                return link

        # additional_option_values (신규 API 형식)
        additional_values = item.get("additional_option_values", []) or []
        for opt in additional_values:
            raw = opt.get("value", "")
            # "게시물 링크 (비공개 계정 작업 불가)=https://..." 형태에서 = 뒤 추출
            if "=" in raw:
                raw = raw.split("=", 1)[1]
            link = normalize_link(raw)
            if link:
                return link

    return None


def extract_link_from_memo(order_detail):
    """주문 메모에서 인스타그램 링크 추출"""
    # buyer_message (구매자 메모)
    memo = order_detail.get("buyer_message", "") or ""
    link = normalize_link(memo)
    if link:
        return link

    # admin_memo (관리자 메모)
    admin_memo = order_detail.get("admin_additional_memo", "") or ""
    link = normalize_link(admin_memo)
    if link:
        return link

    return None


def _parse_quantity_from_value(value):
    """값 문자열에서 숫자를 추출 ("100개" → 100, "1,000명" → 1000)"""
    cleaned = str(value).replace(",", "")
    numbers = re.findall(r"\d+", cleaned)
    if numbers:
        qty = int(numbers[0])
        if qty > 0:
            return qty
    return None


def extract_quantity_from_option(items, quantity_option_name=""):
    """주문 항목의 옵션에서 수량 추출

    옵션 값에서 숫자를 파싱한다.
    예: "100개", "50명", "100", "200개" → 100, 50, 100, 200

    quantity_option_name이 비어있으면 키워드 자동 감지로 수량 옵션을 찾는다.
    """
    # 1. 옵션명이 지정된 경우: 직접 매칭
    if quantity_option_name:
        for item in items:
            options = item.get("options", [])
            for option in options:
                name = option.get("option_name", "") or option.get("name", "")
                value = _get_option_value(option)
                if quantity_option_name in name:
                    qty = _parse_quantity_from_value(value)
                    if qty:
                        logger.info("옵션에서 수량 추출 (지정): %s → %d", value, qty)
                        return qty

            additional = item.get("additional_option", []) or []
            for opt in additional:
                name = opt.get("name", "")
                value = opt.get("value", "")
                if quantity_option_name in name:
                    qty = _parse_quantity_from_value(value)
                    if qty:
                        return qty
        return None

    # 2. 옵션명이 비어있으면: 키워드 자동 감지
    #    수량 관련 키워드가 있는 옵션에서 숫자 추출
    name, value = _find_option_by_keywords(items, QUANTITY_KEYWORDS)
    if value:
        qty = _parse_quantity_from_value(value)
        if qty:
            logger.info("옵션에서 수량 자동 감지: [%s] %s → %d", name, value, qty)
            return qty

    # 3. 모든 옵션에서 숫자가 있는 첫 번째 옵션 반환 (폴백)
    for item in items:
        options = item.get("options", [])
        for option in options:
            value = _get_option_value(option)
            qty = _parse_quantity_from_value(value)
            if qty:
                logger.info("옵션에서 수량 추출 (폴백): %s → %d", value, qty)
                return qty

    return None


def _parse_username_from_value(value):
    """값 문자열에서 인스타그램 아이디 추출"""
    if not value or not value.strip():
        return None

    text = value.strip()

    # URL에서 username 추출
    url_match = INSTAGRAM_URL_PATTERN.search(text)
    if url_match:
        return url_match.group(1)

    # @username에서 추출
    at_match = AT_USERNAME_PATTERN.search(text)
    if at_match:
        return at_match.group(1)

    # 순수 아이디
    if PURE_USERNAME_PATTERN.match(text):
        return text

    return None


def extract_username_from_option(items, username_option_name=""):
    """주문 항목의 옵션에서 인스타그램 아이디(username) 추출

    URL, @username, 순수 아이디 → 순수 아이디만 반환

    username_option_name이 비어있으면 키워드 자동 감지로 아이디 옵션을 찾는다.
    """
    # 1. 옵션명이 지정된 경우: 직접 매칭
    if username_option_name:
        for item in items:
            options = item.get("options", [])
            for option in options:
                name = option.get("option_name", "") or option.get("name", "")
                value = _get_option_value(option)
                if username_option_name in name:
                    username = _parse_username_from_value(value)
                    if username:
                        logger.info("옵션에서 인스타 아이디 추출 (지정): %s → %s", value, username)
                        return username

            additional = item.get("additional_option", []) or []
            for opt in additional:
                name = opt.get("name", "")
                value = opt.get("value", "")
                if username_option_name in name:
                    username = _parse_username_from_value(value)
                    if username:
                        return username

            # additional_option_values (신규 API 형식: "옵션명=값")
            additional_values = item.get("additional_option_values", []) or []
            for opt in additional_values:
                raw = opt.get("value", "")
                if "=" in raw:
                    opt_name, opt_val = raw.split("=", 1)
                    if username_option_name in opt_name:
                        username = _parse_username_from_value(opt_val)
                        if username:
                            logger.info("옵션에서 인스타 아이디 추출 (additional_values): %s → %s", raw, username)
                            return username
        return None

    # 2. 옵션명이 비어있으면: 키워드 자동 감지
    name, value = _find_option_by_keywords(items, USERNAME_KEYWORDS)
    if value:
        username = _parse_username_from_value(value)
        if username:
            logger.info("옵션에서 인스타 아이디 자동 감지: [%s] %s → %s", name, value, username)
            return username

    # 3. 모든 옵션에서 인스타 아이디가 될 수 있는 값 찾기 (폴백)
    for item in items:
        options = item.get("options", [])
        for option in options:
            value = _get_option_value(option)
            username = _parse_username_from_value(value)
            if username:
                logger.info("옵션에서 인스타 아이디 추출 (폴백): %s → %s", value, username)
                return username

        additional = item.get("additional_option", []) or []
        for opt in additional:
            value = opt.get("value", "")
            username = _parse_username_from_value(value)
            if username:
                return username

        # additional_option_values (신규 API 형식: "옵션명=값")
        additional_values = item.get("additional_option_values", []) or []
        for opt in additional_values:
            raw = opt.get("value", "")
            if "=" in raw:
                raw = raw.split("=", 1)[1]
            username = _parse_username_from_value(raw)
            if username:
                logger.info("옵션에서 인스타 아이디 추출 (폴백 additional_values): %s → %s", raw, username)
                return username

    return None


def extract_link(items, order_detail, link_source="option", option_name=""):
    """링크 추출 (link_source에 따라 우선순위 결정)

    link_source='option': 옵션 우선 → 메모 폴백
    link_source='memo': 메모 우선 → 옵션 폴백
    """
    if link_source == "option":
        link = extract_link_from_option(items, option_name)
        if link:
            return link
        return extract_link_from_memo(order_detail)
    else:
        link = extract_link_from_memo(order_detail)
        if link:
            return link
        return extract_link_from_option(items, option_name)


def extract_likes_quantity(items, likes_option_name=""):
    """좋아요 수량 추출 (구독 주문용)

    likes_option_name이 지정되면 해당 옵션에서 추출.
    비어있으면 '좋아요', '하트', 'likes' 등 키워드로 자동 감지.
    """
    if likes_option_name:
        return extract_quantity_from_option(items, quantity_option_name=likes_option_name)

    # 키워드 자동 감지
    name, value = _find_option_by_keywords(items, LIKES_KEYWORDS)
    if value:
        qty = _parse_quantity_from_value(value)
        if qty:
            logger.info("좋아요 수량 자동 감지: [%s] %s → %d", name, value, qty)
            return qty

    return None


def extract_posts_quantity(items, posts_option_name=""):
    """게시물 수량 추출 (구독 주문용)

    posts_option_name이 지정되면 해당 옵션에서 추출.
    비어있으면 '게시물', '포스트', 'posts' 등 키워드로 자동 감지.
    """
    if posts_option_name:
        return extract_quantity_from_option(items, quantity_option_name=posts_option_name)

    # 키워드 자동 감지
    name, value = _find_option_by_keywords(items, POSTS_KEYWORDS)
    if value:
        qty = _parse_quantity_from_value(value)
        if qty:
            logger.info("게시물 수량 자동 감지: [%s] %s → %d", name, value, qty)
            return qty

    return None
