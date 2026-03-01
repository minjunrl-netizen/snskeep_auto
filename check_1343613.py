"""캠페인 1343613 상태 확인."""
from services.superap_client import SuperapClient

client = SuperapClient()
client.login()

campaigns = client.get_all_campaigns()
for c in campaigns:
    if str(c.get("ad_idx")) == "1343613":
        print(f"캠페인 1343613:")
        print(f"  status: {c['status']}")
        print(f"  total_budget: {c['total_budget']}")
        print(f"  day_budget: {c.get('day_budget')}")
        print(f"  ad_name: {c.get('ad_name')}")
        break
else:
    print("캠페인 1343613 찾을 수 없음")
