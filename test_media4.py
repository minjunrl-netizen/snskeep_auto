"""1346280 수정으로 매체 타겟팅 적용 테스트 + 신규 캠페인 add 문제 분석"""
import requests, config, json

session = requests.Session()
session.post("https://superap.io/j_spring_security_check", data={
    "j_username": config.SUPERAP_USERNAME,
    "j_password": config.SUPERAP_PASSWORD,
}, allow_redirects=True, timeout=30)

# 전체 캠페인 목록에서 Active 캠페인들의 매체 타겟팅 상태 확인
resp = session.get("https://superap.io/service/reward/adver/report/csv", timeout=60)
all_c = resp.json().get("data", [])
active = [c for c in all_c if c.get("status") == "Active"]
active.sort(key=lambda c: c["ad_idx"], reverse=True)

print("=== Active 캠페인 매체 타겟팅 상태 ===")
for c in active:
    ad_idx = str(c["ad_idx"])
    resp2 = session.get("https://superap.io/service/reward/adver/publishers",
                        params={"mode": "modify", "adIdx": ad_idx}, timeout=30)
    data2 = resp2.json()
    pubs = data2.get("data", []) or data2.get("results", [])
    checked = sum(1 for p in pubs if p.get("check"))
    total = len(pubs)

    name = c.get("ad_name", "")
    username = name.rsplit(" ", 1)[-1] if " " in name else name
    budget = c.get("total_budget", 0)

    status_icon = "OK" if checked > 0 else "BROKEN"
    print(f"  [{status_icon}] {ad_idx} {username:20s} budget={budget:>6} checked={checked}/{total}")
