"""campaign_map에 없는 Active 캠페인 URL 확인"""
import requests, config, re, json

session = requests.Session()
session.post("https://superap.io/j_spring_security_check", data={
    "j_username": config.SUPERAP_USERNAME,
    "j_password": config.SUPERAP_PASSWORD,
}, allow_redirects=True, timeout=30)

skip_ids = ["1339575", "1345933", "1346280", "1346291"]
for ad_idx in skip_ids:
    resp = session.get(f"https://superap.io/service/reward/adver/mod?ad_idx={ad_idx}", timeout=15)
    url_match = re.findall(r"name=\"url\"[^>]*value=\"([^\"]*)\"", resp.text)
    title_match = re.findall(r"ad_title[^>]*value=\"([^\"]*)\"", resp.text)
    title = title_match[0] if title_match else "?"
    url = url_match[0] if url_match else "?"
    # URL에서 username 추출
    um = re.findall(r"instagram\.com/([^/]+)/", url)
    username = um[0] if um else "?"
    # 제목에서 username 추출
    name_parts = title.rsplit(" ", 1)
    title_name = name_parts[-1] if len(name_parts) == 2 else "?"

    has_underscore_issue = "_" not in username and "_" in title_name
    status = "WRONG?" if username != title_name and has_underscore_issue else "OK"
    print(f"  [{status}] {ad_idx} title_name={title_name} url_user={username}")
    print(f"    URL: {url}")
