"""j_donghyuk1 캠페인(1343589) 예산 0으로 설정하여 끄기."""
from services.superap_client import SuperapClient

client = SuperapClient()
ad_idx = 1343589
username = "j_donghyuk1"

# 현재 상태 확인
campaigns = client.get_all_campaigns()
for c in campaigns:
    if c.get("ad_idx") == ad_idx:
        print(f"현재: ad_idx={ad_idx}, status={c.get('status')}, budget={c.get('total_budget')}, name={c.get('ad_name')}")
        break

# 예산 0으로 수정 → 캠페인 비활성화
print(f"\n캠페인 {ad_idx} 예산 0으로 변경 중...")
result = client.update_campaign(ad_idx=str(ad_idx), username=username, total_budget=0, answer=None)
print(f"결과: {result}")

# 변경 후 확인
campaigns2 = client.get_all_campaigns()
for c in campaigns2:
    if c.get("ad_idx") == ad_idx:
        print(f"\n변경 후: status={c.get('status')}, budget={c.get('total_budget')}")
        break
