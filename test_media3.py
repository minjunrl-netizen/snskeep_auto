"""정확한 파라미터로 publishers API 조회 및 실제 수정 테스트"""
import requests, config, json

session = requests.Session()
session.post("https://superap.io/j_spring_security_check", data={
    "j_username": config.SUPERAP_USERNAME,
    "j_password": config.SUPERAP_PASSWORD,
}, allow_redirects=True, timeout=30)

# JS에서 보내는 것과 동일한 파라미터로 조회
# JS: http.get("/service/reward/adver/publishers", {mode: "modify", adIdx: "1346291"})
print("=== 파라미터 비교 테스트 ===")

params_tests = [
    {"mode": "mod", "ad_idx": "1346291"},        # 내가 사용한 것
    {"mode": "modify", "adIdx": "1346291"},       # JS와 동일
    {"mode": "modify", "ad_idx": "1346291"},      # 혼합
    {"mode": "mod", "adIdx": "1346291"},           # 혼합
]

for params in params_tests:
    resp = session.get("https://superap.io/service/reward/adver/publishers",
                       params=params, timeout=30)
    data = resp.json()
    pubs = data.get("data", []) or data.get("results", [])
    checked = sum(1 for p in pubs if p.get("check"))
    unchecked = sum(1 for p in pubs if not p.get("check"))
    print("params:", json.dumps(params), "-> total:", len(pubs), "checked:", checked, "unchecked:", unchecked)

print()

# 정상 캠페인과 비교 (1346196 moondong18 - 이전 batch update된 것)
print("=== 캠페인 비교 (정확한 파라미터) ===")
for ad_idx in ["1346291", "1346280", "1346196", "1345978"]:
    resp = session.get("https://superap.io/service/reward/adver/publishers",
                       params={"mode": "modify", "adIdx": ad_idx}, timeout=30)
    data = resp.json()
    pubs = data.get("data", []) or data.get("results", [])
    checked = sum(1 for p in pubs if p.get("check"))
    unchecked = sum(1 for p in pubs if not p.get("check"))

    # 체크된 퍼블리셔 이름만 출력
    checked_names = [p.get("name") for p in pubs if p.get("check")]
    print("ad_idx:", ad_idx, "-> checked:", checked, "/", len(pubs),
          "names:", ",".join(checked_names[:5]) + ("..." if checked > 5 else ""))
