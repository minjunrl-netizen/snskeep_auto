"""캠페인 1343613 비활성화."""
from services.superap_client import SuperapClient

client = SuperapClient()
client.login()

base = "https://superap.io"
urls = [
    f"{base}/service/reward/adver/status_to?status=Deactive&ad_idx=1343613",
    f"{base}/service/reward/adver/csv/status_to?status=Deactive&ad_idx=1343613",
    f"{base}/service/reward/adver/report/status_to?status=Deactive&ad_idx=1343613",
    f"{base}/service/reward/adver/report/csv/status_to?status=Deactive&ad_idx=1343613",
]
for u in urls:
    r = client.session.get(u, timeout=30, allow_redirects=False)
    loc = r.headers.get("Location", "")
    short = u.replace(base, "")
    print(f"{r.status_code} {short} {loc}")

# budget을 0으로 수정하면 자동 Deactive 될 수 있음
print("\n=== budget=0으로 수정 시도 ===")
result = client.update_campaign(
    ad_idx="1343613",
    username="minwhitebeaer",
    total_budget=0,
)
print(result)

# 상태 확인
campaigns = client.get_all_campaigns()
for c in campaigns:
    if str(c.get("ad_idx")) == "1343613":
        print(f"\n캠페인 1343613 최종 상태: {c['status']} budget={c['total_budget']}")
        break
