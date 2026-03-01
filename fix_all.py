"""전체 캠페인 점검 및 수정 스크립트.

1. in_progress 주문의 모든 username 프로필 스크래핑
2. 비공개/존재하지 않는 계정 → 주문 canceled
3. 정상 계정의 Active 캠페인 → update_campaign으로 설정 재적용 (설명, 이미지, 매체 등)
4. 캠페인 미등록 건 → 새로 등록
"""
import json
import time
from services.superap_client import SuperapClient, load_campaign_settings, _load_campaign_map
from services.profile_extractor import scrape_profiles, extract_username_from_link
from services.campaign_scheduler import (
    get_orders_by_status, change_order_status, _api_headers, ADMIN_API_URL
)

print("=" * 60)
print("전체 캠페인 점검 및 수정")
print("=" * 60)

# ── 1. in_progress 주문 조회 ──
orders = get_orders_by_status("in_progress")
print(f"\n[1] in_progress 주문: {len(orders)}건")

# username → order_ids 매핑
username_orders = {}
for o in orders:
    link = o.get("link", "")
    username = extract_username_from_link(link)
    if not username:
        username = link  # 그냥 username만 있는 경우
    username_orders.setdefault(username, []).append(o["id"])

unique_usernames = list(username_orders.keys())
print(f"   고유 아이디: {len(unique_usernames)}명")
for u, oids in username_orders.items():
    print(f"   {u} → 주문 {oids}")

# ── 2. 프로필 스크래핑 ──
print(f"\n[2] 프로필 스크래핑 ({len(unique_usernames)}명)...")
profiles = scrape_profiles(unique_usernames)
profile_map = {p["username"]: p for p in profiles}
print(f"   결과: {len(profiles)}개 프로필 반환")

# ── 3. 분류: 존재하지 않음 / 비공개 / 정상 ──
cancel_ids = []
cancel_reasons = {}
valid_users = {}

for username, oids in username_orders.items():
    profile = profile_map.get(username)
    if not profile:
        cancel_ids.extend(oids)
        cancel_reasons[username] = "존재하지 않는 계정"
        print(f"   ❌ {username}: 존재하지 않음 → 주문 {oids} canceled")
    elif profile.get("비공개") == "비공개":
        cancel_ids.extend(oids)
        cancel_reasons[username] = "비공개 계정"
        print(f"   🔒 {username}: 비공개 → 주문 {oids} canceled")
    else:
        valid_users[username] = {
            "oids": oids,
            "profile": profile,
            "answer": profile.get("정답", ""),
            "quantity": sum(
                o.get("quantity", 0)
                for o in orders
                if extract_username_from_link(o.get("link", "")) == username
                or o.get("link", "") == username
            ),
        }
        print(f"   ✅ {username}: 정상 (정답={profile.get('정답')}, {profile.get('비공개')})")

# ── 4. 비공개/미존재 주문 취소 ──
if cancel_ids:
    print(f"\n[4] {len(cancel_ids)}건 주문 취소 중...")
    try:
        change_order_status(cancel_ids, "canceled")
        print(f"   ✅ {len(cancel_ids)}건 canceled 완료")
    except Exception as e:
        print(f"   ❌ canceled 실패: {e}")
else:
    print("\n[4] 취소할 주문 없음")

# ── 5. superap.io 캠페인 확인 및 수정 ──
print(f"\n[5] superap.io 캠페인 확인 및 수정 ({len(valid_users)}명)...")
client = SuperapClient()
client.login()
all_campaigns = client.get_all_campaigns()
local_map = _load_campaign_map()

settings = load_campaign_settings()
print(f"   현재 설정: budget_multiplier={settings['budget_multiplier']}, "
      f"duration_days={settings['duration_days']}, geo={settings['geo']}")

# campaign map: ad_idx → campaign
campaigns_by_idx = {str(c["ad_idx"]): c for c in all_campaigns}

# ad_name 기반 매핑 (username 뒤부분)
name_map = {}
for c in all_campaigns:
    ad_name = c.get("ad_name", "")
    parts = ad_name.rsplit(" ", 1)
    if len(parts) == 2:
        uname = parts[1]
        if uname not in name_map or c["ad_idx"] > name_map[uname]["ad_idx"]:
            name_map[uname] = c

results = []
for username, info in valid_users.items():
    # 기존 캠페인 찾기
    existing = None

    # 1) 로컬 매핑
    if username in local_map:
        ad_idx = local_map[username]
        if ad_idx in campaigns_by_idx:
            existing = campaigns_by_idx[ad_idx]

    # 2) 이름 매핑 폴백
    if not existing and username in name_map:
        existing = name_map[username]

    if existing:
        ad_idx = existing["ad_idx"]
        status = existing.get("status", "")
        budget = int(existing.get("total_budget", 0))

        if status == "Active":
            # Active 캠페인 → update로 설정 재적용
            print(f"\n   📝 {username}: 캠페인 {ad_idx} (Active, budget={budget}) → 설정 재적용")
            try:
                result = client.update_campaign(
                    ad_idx=str(ad_idx),
                    username=username,
                    total_budget=budget,
                    answer=info["answer"],
                )
                print(f"      → {result.get('message')}")
                results.append({"username": username, "action": "update", "ok": result.get("ok"), "msg": result.get("message")})
            except Exception as e:
                print(f"      → 수정 실패: {e}")
                results.append({"username": username, "action": "update", "ok": False, "msg": str(e)})
        else:
            # Deactive/기타 → 새 캠페인 등록
            print(f"\n   🆕 {username}: 캠페인 {ad_idx} ({status}) → 새로 등록")
            try:
                result = client.create_campaign(
                    username=username,
                    quantity=info["quantity"],
                    answer=info["answer"],
                )
                print(f"      → {result.get('message')}")
                results.append({"username": username, "action": "create(re)", "ok": result.get("ok"), "msg": result.get("message")})
            except Exception as e:
                print(f"      → 등록 실패: {e}")
                results.append({"username": username, "action": "create(re)", "ok": False, "msg": str(e)})
    else:
        # 캠페인 없음 → 새로 등록
        print(f"\n   🆕 {username}: 캠페인 없음 → 새로 등록 (qty={info['quantity']})")
        try:
            result = client.create_campaign(
                username=username,
                quantity=info["quantity"],
                answer=info["answer"],
            )
            print(f"      → {result.get('message')}")
            results.append({"username": username, "action": "create", "ok": result.get("ok"), "msg": result.get("message")})
        except Exception as e:
            print(f"      → 등록 실패: {e}")
            results.append({"username": username, "action": "create", "ok": False, "msg": str(e)})

    time.sleep(1)  # API 부하 방지

# ── 6. 최종 결과 ──
print("\n" + "=" * 60)
print("최종 결과")
print("=" * 60)
ok_count = sum(1 for r in results if r.get("ok"))
fail_count = len(results) - ok_count
print(f"캠페인 처리: 성공 {ok_count}건, 실패 {fail_count}건")
print(f"주문 취소: {len(cancel_ids)}건")
for reason_user, reason in cancel_reasons.items():
    print(f"  - {reason_user}: {reason}")

print("\n상세 결과:")
for r in results:
    status = "✅" if r.get("ok") else "❌"
    print(f"  {status} {r['username']} [{r['action']}] {r['msg']}")
