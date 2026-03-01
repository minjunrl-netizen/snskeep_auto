"""in_progress 주문 중 비공개 계정 찾아서 canceled 처리."""
import json
import requests
import config
from services.profile_extractor import scrape_profiles, extract_username_from_link
from services.campaign_scheduler import change_order_status

# 1. in_progress 주문 조회
headers = {"X-Api-Key": config.INSTAMONSTER_ADMIN_API_KEY, "Content-Type": "application/json"}
resp = requests.get(
    f"{config.INSTAMONSTER_ADMIN_API_URL}/orders",
    headers=headers,
    params={"service_ids": "32", "limit": 500},
    timeout=60,
)
all_orders = resp.json().get("data", {}).get("list", [])
in_progress = [o for o in all_orders if o.get("status") == "in_progress"]
print(f"in_progress 주문: {len(in_progress)}건\n")

# 2. username 추출
order_map = []  # (order_id, username)
usernames = set()
for o in in_progress:
    link = o.get("link", "")
    username = extract_username_from_link(link)
    if username:
        order_map.append((o.get("id"), username))
        usernames.add(username)

print(f"고유 username: {len(usernames)}개")
print(f"스크래핑 시작...\n")

# 3. 프로필 스크래핑
profiles = scrape_profiles(list(usernames))
profile_dict = {p["username"]: p for p in profiles}

# 4. 비공개 계정 확인
print(f"{'username':<25} {'order_id':<12} {'비공개여부':<10} {'action'}")
print("-" * 65)

cancel_ids = []
for oid, username in order_map:
    profile = profile_dict.get(username)
    if not profile:
        is_private = "프로필없음"
        action = "-> canceled (계정 없음)"
        cancel_ids.append(oid)
    elif profile.get("비공개") == "비공개":
        is_private = "비공개"
        action = "-> canceled"
        cancel_ids.append(oid)
    else:
        is_private = "공개"
        action = ""
    print(f"{username:<25} {oid:<12} {is_private:<10} {action}")

print()
print(f"취소 대상: {len(cancel_ids)}건 — {cancel_ids}")

# 5. 취소 처리
if cancel_ids:
    print("취소 처리 중...")
    result = change_order_status(cancel_ids, "canceled")
    print(f"취소 처리 결과: {result}")
else:
    print("취소할 주문 없음")
