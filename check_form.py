"""superap.io 캠페인 등록 폼 필드 확인."""
import re
from services.superap_client import SuperapClient

client = SuperapClient()
client.login()

resp = client.session.get("https://superap.io/service/reward/adver/add/csv", timeout=30)
print("status:", resp.status_code)
html = resp.text

# 모든 input/select name 필드
print("\n=== ALL FORM FIELDS ===")
for m in re.finditer(r'name=["\'](\w+)["\']', html):
    print(m.group(1))

print("\n=== SELECT OPTIONS ===")
# select 태그 찾기
for m in re.finditer(r'<select[^>]*name=["\'](\w+)["\'][^>]*>([\s\S]*?)</select>', html):
    name = m.group(1)
    options_html = m.group(2)
    print(f"\nselect: {name}")
    for opt in re.finditer(r'value=["\']([^"\']*)["\'][^>]*>([^<]*)', options_html):
        print(f"  {opt.group(1)} = {opt.group(2).strip()}")

print("\n=== ADBC / TARGET / MEDIA ===")
# adbc, target, media 관련
for keyword in ["adbc", "target", "media", "adsomeType", "RCPA", "channel"]:
    indices = [m.start() for m in re.finditer(keyword, html, re.IGNORECASE)]
    for idx in indices:
        start = max(0, idx - 50)
        end = min(len(html), idx + 150)
        print(f"[{keyword}] ...{html[start:end]}...")
        print()
