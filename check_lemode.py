import requests, config, re, json

session = requests.Session()
session.post("https://superap.io/j_spring_security_check", data={
    "j_username": config.SUPERAP_USERNAME,
    "j_password": config.SUPERAP_PASSWORD,
}, allow_redirects=True, timeout=30)

# campaign_map에서 lemode_shop 찾기
with open("data/campaign_map.json") as f:
    cmap = json.load(f)
ad_idx = cmap.get("lemode_shop")
print("campaign_map: lemode_shop ->", ad_idx)

# CSV에서 lemode 관련 캠페인 찾기
resp = session.get("https://superap.io/service/reward/adver/report/csv", timeout=60)
all_c = resp.json().get("data", [])
for c in all_c:
    name = c.get("ad_name", "")
    if "lemode" in name.lower():
        ad = c["ad_idx"]
        print(f"\nad_idx={ad} status={c['status']} name={name}")
        # 수정 페이지에서 URL 확인
        resp2 = session.get(f"https://superap.io/service/reward/adver/mod?ad_idx={ad}", timeout=15)
        url_match = re.findall(r"name=\"url\"[^>]*value=\"([^\"]*)\"", resp2.text)
        if url_match:
            print(f"  URL: {url_match[0]}")
