"""매체 타겟팅 테스트 스크립트 - 서버에서 실행"""
import requests
import config
import json
import re
from datetime import datetime, timedelta

session = requests.Session()
session.post("https://superap.io/j_spring_security_check", data={
    "j_username": config.SUPERAP_USERNAME,
    "j_password": config.SUPERAP_PASSWORD,
}, allow_redirects=True, timeout=30)

AD_IDX = "1346291"

# 퍼블리셔 ID 목록 조회
pubs_resp = session.get("https://superap.io/service/reward/adver/publishers",
                        params={"mode": "mod", "ad_idx": AD_IDX}, timeout=30)
pub_data = pubs_resp.json().get("data", [])
pub_values = [p["value"] for p in pub_data if p.get("value")]
print("Available publisher IDs:", pub_values)

# 캠페인 설정에서 target_media_ids 가져오기
settings_file = config.BASE_DIR + "/data/campaign_settings.json"
with open(settings_file) as f:
    settings = json.load(f)
media_ids = settings.get("target_media_ids", [])
valid_media_ids = [mid for mid in media_ids if mid in pub_values]
print("Valid media IDs:", len(valid_media_ids), "out of", len(media_ids))

# 수정 페이지에서 form 값 추출 (textarea 포함)
resp = session.get("https://superap.io/service/reward/adver/mod?ad_idx=" + AD_IDX, timeout=15)
html = resp.text

# input 추출
inputs = re.findall(r'name="([^"]*)"[^>]*value="([^"]*)"', html)
form_vals = {}
for name, val in inputs:
    if name not in form_vals:
        form_vals[name] = val

# textarea (description) 추출
desc_match = re.findall(r'name="description"[^>]*>(.*?)</textarea>', html, re.DOTALL)
if desc_match:
    form_vals["description"] = desc_match[0]

# select (ad_event_limit, ad_charge_price) 추출
for field in ["ad_event_limit", "ad_charge_price"]:
    pattern = r'name="' + field + r'".*?<option[^>]*selected[^>]*value="([^"]*)"'
    match = re.findall(pattern, html, re.DOTALL)
    if match:
        form_vals[field] = match[0]
    else:
        # fallback: selected 앞에 value가 올 수도 있음
        pattern2 = r'name="' + field + r'".*?value="([^"]*)"[^>]*selected'
        match2 = re.findall(pattern2, html, re.DOTALL)
        if match2:
            form_vals[field] = match2[0]

print("\nForm values extracted:")
for k in sorted(form_vals.keys()):
    v = form_vals[k]
    if len(v) > 80:
        v = v[:80] + "..."
    print("  " + k + " = " + v)

# image_url 3개 추출
img_matches = re.findall(r'name="image_url"[^>]*value="([^"]*)"', html)
print("\nimage_url values:", img_matches)

# === TEST: urlencoded (data=list of tuples, no files) ===
print("\n=== TEST: urlencoded data ===")
form_tuples = []
for k in ["ad_idx", "ad_title", "detail_type", "total_budget", "day_budget",
           "description", "target_package", "search_keyword",
           "begin_date", "end_date", "url", "geo", "adsomeType",
           "ad_event_name", "ad_event_limit", "conversion", "ad_charge_price"]:
    form_tuples.append((k, form_vals.get(k, "")))

for img_url in img_matches:
    form_tuples.append(("image_url", img_url))

for mid in valid_media_ids:
    form_tuples.append(("targetMediaIds", str(mid)))

print("Sending", len(form_tuples), "fields")
print("targetMediaIds count:", sum(1 for k, v in form_tuples if k == "targetMediaIds"))

resp2 = session.post("https://superap.io/service/reward/adver/modify/post",
                      data=form_tuples, timeout=30, allow_redirects=False)
print("Status:", resp2.status_code)
print("Location:", resp2.headers.get("Location", "none"))
if resp2.status_code not in (302, 200):
    print("Response:", resp2.text[:300])
