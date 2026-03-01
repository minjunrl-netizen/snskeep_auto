"""superap.io 캠페인 등록 폼 필드 확인."""
import re
from services.superap_client import SuperapClient

client = SuperapClient()
client.login()

resp = client.session.get("https://superap.io/service/reward/adver/add", timeout=30)
print("status:", resp.status_code)
html = resp.text

# 모든 input/select name 필드
print("\n=== ALL FORM FIELDS ===")
seen = set()
for m in re.finditer(r'name=["\'](\w+)["\']', html):
    name = m.group(1)
    if name not in seen:
        seen.add(name)
        print(name)

print("\n=== SELECT OPTIONS ===")
for m in re.finditer(r'<select[^>]*name=["\'](\w+)["\'][^>]*>([\s\S]*?)</select>', html):
    name = m.group(1)
    options_html = m.group(2)
    print(f"\nselect: {name}")
    for opt in re.finditer(r'value=["\']([^"\']*)["\'][^>]*>([^<]*)', options_html):
        print(f"  {opt.group(1)} = {opt.group(2).strip()}")

print("\n=== ADBC / TARGET / MEDIA / RCPA ===")
for keyword in ["adbc", "target", "media", "adsomeType", "RCPA", "channel", "ADBC"]:
    for m in re.finditer(keyword, html, re.IGNORECASE):
        idx = m.start()
        start = max(0, idx - 50)
        end = min(len(html), idx + 200)
        snippet = html[start:end].replace("\n", " ").replace("\r", "")
        print(f"[{keyword}] ...{snippet}...")
        print()
