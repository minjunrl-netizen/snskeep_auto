"""캠페인 1343613 비활성화 - JS 파일에서 비활성화 API 찾기."""
import re
from services.superap_client import SuperapClient

client = SuperapClient()
client.login()

# 관리 페이지 JS 확인
js_urls = [
    "https://superap.io/resources/assets/js/adver/add.js",
    "https://superap.io/resources/assets/js/common/common.js",
]

for js_url in js_urls:
    resp = client.session.get(js_url, timeout=30)
    text = resp.text
    # deactive, pause, stop, status 관련 찾기
    for kw in ["deactive", "pause", "active", "status/change", "onoff", "toggle"]:
        for m in re.finditer(kw + r'[^\n]{0,200}', text, re.IGNORECASE):
            print(f"[{js_url.split('/')[-1]}] {m.group()[:250]}")
            print()

# 캠페인 관리 페이지 확인
resp = client.session.get("https://superap.io/service/reward/adver/report", timeout=30)
html = resp.text
# 관리 페이지의 JS 파일 찾기
for m in re.finditer(r'src=["\']([^"\']+\.js[^"\']*)["\']', html):
    src = m.group(1)
    if "adver" in src or "report" in src or "manage" in src:
        print(f"관리 페이지 JS: {src}")

# 관리 페이지에서 deactive/pause 버튼 찾기
for kw in ["deactive", "pause", "비활성", "중지", "off", "onoff"]:
    for m in re.finditer(kw + r'[^<\n]{0,200}', html, re.IGNORECASE):
        print(f"[report] {m.group()[:250]}")
