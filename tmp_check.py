"""in_progress 주문의 TotalOff 캠페인 확인 및 완료 처리."""
import json
import requests
import config
from services.superap_client import SuperapClient, _load_campaign_map
from services.profile_extractor import extract_username_from_link
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

# 2. superap 캠페인 조회
client = SuperapClient()
campaigns = client.get_all_campaigns()
campaigns_by_idx = {str(c.get("ad_idx", "")): c for c in campaigns}

# 이름 매핑
name_map = {}
for c in campaigns:
    ad_name = c.get("ad_name", "")
    parts = ad_name.rsplit(" ", 1)
    if len(parts) == 2:
        uname = parts[1]
        if uname not in name_map or c["ad_idx"] > name_map[uname]["ad_idx"]:
            name_map[uname] = c

# 3. campaign_map.json
local_map = _load_campaign_map()

# 4. 매칭 및 TotalOff 확인
print(f"{'username':<25} {'order_id':<12} {'ad_idx':<12} {'status':<15} {'action'}")
print("-" * 80)

completed_ids = []
for order in in_progress:
    link = order.get("link", "")
    username = extract_username_from_link(link)
    if not username:
        continue

    campaign = None
    ad_idx = local_map.get(username)
    if ad_idx:
        campaign = campaigns_by_idx.get(str(ad_idx))
    if not campaign:
        campaign = name_map.get(username)
    if not campaign:
        campaign = name_map.get(username.replace("_", ""))

    c_status = campaign.get("status", "?") if campaign else "NO_CAMPAIGN"
    cidx = campaign.get("ad_idx", "-") if campaign else "-"
    oid = order.get("id")
    action = ""
    if campaign and c_status == "TotalOff":
        completed_ids.append(oid)
        action = "-> completed"

    print(f"{username:<25} {oid:<12} {str(cidx):<12} {c_status:<15} {action}")

print()
print(f"TotalOff(완료처리 대상): {len(completed_ids)}건")

# 5. 완료 처리
if completed_ids:
    print(f"완료 처리 중: {completed_ids}")
    result = change_order_status(completed_ids, "completed")
    print(f"완료 처리 결과: {result}")
else:
    print("완료 처리할 주문 없음")
