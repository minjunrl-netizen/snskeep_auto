"""현재 캠페인 및 주문 상태 전체 점검."""
import json
import requests
import config
from services.superap_client import SuperapClient, load_campaign_settings
from services.campaign_scheduler import get_orders_by_status, ADMIN_API_URL, _api_headers

# 현재 설정
settings = load_campaign_settings()
print("=== 현재 캠페인 설정 ===")
print(json.dumps(settings, ensure_ascii=False, indent=2))

# superap 캠페인 목록
client = SuperapClient()
client.login()
campaigns = client.get_all_campaigns()

# 최근 캠페인
recent = [c for c in campaigns if c.get("reg_date", "") >= "2026-02-18"]
print(f"\n=== 오늘 등록된 캠페인: {len(recent)}건 ===")
for c in sorted(recent, key=lambda x: x.get("ad_idx", "")):
    print(f"  [{c.get('ad_idx')}] {c.get('ad_name')}  budget={c.get('total_budget')}  action={c.get('action_count')}  status={c.get('status')}")

# 활성 캠페인
active = [c for c in campaigns if c.get("status") not in ("TotalOff", "Deactive")]
print(f"\n=== 활성 캠페인: {len(active)}건 ===")
for c in sorted(active, key=lambda x: x.get("ad_idx", ""), reverse=True)[:30]:
    print(f"  [{c.get('ad_idx')}] {c.get('ad_name')}  budget={c.get('total_budget')}  action={c.get('action_count')}  status={c.get('status')}")

# 인스타몬스터 주문 상태별 조회
print("\n=== 인스타몬스터 주문 현황 ===")
for status in ["pending", "processing", "in_progress", "completed", "canceled"]:
    try:
        orders = get_orders_by_status(status)
        print(f"  {status}: {len(orders)}건")
        if orders and status in ("pending", "processing", "in_progress"):
            for o in orders[:20]:
                print(f"    id={o.get('id')} link={o.get('link','')[:60]} qty={o.get('quantity')} status={o.get('status')}")
    except Exception as e:
        print(f"  {status}: 오류 - {e}")
