"""superap 수정 페이지 분석"""
import requests, config, re

session = requests.Session()
session.post("https://superap.io/j_spring_security_check", data={
    "j_username": config.SUPERAP_USERNAME,
    "j_password": config.SUPERAP_PASSWORD,
}, allow_redirects=True, timeout=30)

resp = session.get("https://superap.io/service/reward/adver/mod?ad_idx=1346291", timeout=15)
html = resp.text

# 외부 JS 파일 경로 확인
js_files = re.findall(r'src="([^"]*\.js[^"]*)"', html)
for js in js_files:
    if "adver" in js.lower() or "reward" in js.lower() or "modify" in js.lower() or "form" in js.lower():
        print("RELEVANT JS:", js)

# form 태그 확인
forms = re.findall(r"<form[^>]*>", html)
for f in forms:
    print("FORM:", f)

# onclick 확인
onclicks = re.findall(r"onclick=\"([^\"]*)\"", html)
for oc in onclicks:
    if "fn_" in oc or "submit" in oc.lower() or "save" in oc.lower() or "modify" in oc.lower():
        print("ONCLICK:", oc[:200])

# 관련 JS 파일 내용 확인
for js in js_files:
    if "adver" in js.lower() or "reward" in js.lower():
        full_url = js if js.startswith("http") else "https://superap.io" + js
        print("\nFetching:", full_url)
        js_resp = session.get(full_url, timeout=15)
        js_content = js_resp.text
        # publisher/targetMedia 관련 부분 찾기
        for line in js_content.split("\n"):
            if "publisher" in line.lower() or "targetmedia" in line.lower() or "media_id" in line.lower():
                print("  JS:", line.strip()[:200])
