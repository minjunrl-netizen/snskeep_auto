"""모든 캠페인 URL과 campaign_map 비교 검증"""
import requests, config, re, json

session = requests.Session()
session.post("https://superap.io/j_spring_security_check", data={
    "j_username": config.SUPERAP_USERNAME,
    "j_password": config.SUPERAP_PASSWORD,
}, allow_redirects=True, timeout=30)

# campaign_map 로드 (username -> ad_idx)
with open("data/campaign_map.json") as f:
    cmap = json.load(f)

# 역매핑 (ad_idx -> username)
idx_to_user = {v: k for k, v in cmap.items()}

# Active 캠페인만 확인
resp = session.get("https://superap.io/service/reward/adver/report/csv", timeout=60)
all_c = resp.json().get("data", [])
active = [c for c in all_c if c.get("status") == "Active"]
active.sort(key=lambda c: c["ad_idx"])

print(f"Active 캠페인 {len(active)}개 URL 검증\n")
wrong = []
for c in active:
    ad_idx = str(c["ad_idx"])
    correct_username = idx_to_user.get(ad_idx)
    if not correct_username:
        print(f"  [SKIP] {ad_idx} - campaign_map에 없음 (title: {c.get('ad_name','')})")
        continue

    # 수정 페이지에서 URL 확인
    resp2 = session.get(f"https://superap.io/service/reward/adver/mod?ad_idx={ad_idx}", timeout=15)
    url_match = re.findall(r"name=\"url\"[^>]*value=\"([^\"]*)\"", resp2.text)
    if not url_match:
        print(f"  [SKIP] {ad_idx} - URL 추출 실패")
        continue

    current_url = url_match[0]
    expected_url = f"https://www.instagram.com/{correct_username}/#sns_instagram_follow"

    if current_url == expected_url:
        print(f"  [OK] {ad_idx} {correct_username}")
    else:
        print(f"  [WRONG] {ad_idx} {correct_username}")
        print(f"    현재: {current_url}")
        print(f"    정상: {expected_url}")
        wrong.append({"ad_idx": ad_idx, "username": correct_username, "current": current_url})

print(f"\n총 {len(wrong)}건 URL 오류")
