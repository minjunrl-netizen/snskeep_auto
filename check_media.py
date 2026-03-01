"""superap.io 매체 타겟팅 체크박스 필드 확인."""
import re
from services.superap_client import SuperapClient

client = SuperapClient()
client.login()

resp = client.session.get("https://superap.io/service/reward/adver/add", timeout=30)
html = resp.text

# 매체 타겟팅 관련 체크박스 찾기
# checkbox input fields
print("=== CHECKBOXES ===")
for m in re.finditer(r'<input[^>]*type=["\']checkbox["\'][^>]*>', html):
    tag = m.group()
    name = re.search(r'name=["\']([^"\']+)["\']', tag)
    value = re.search(r'value=["\']([^"\']*)["\']', tag)
    checked = 'checked' in tag.lower()
    label_text = ""
    # try to find nearby label text
    after = html[m.end():m.end()+200]
    label_match = re.search(r'>([^<]+)<', after)
    if label_match:
        label_text = label_match.group(1).strip()
    print(f"  name={name.group(1) if name else '?'} value={value.group(1) if value else '?'} checked={checked} label={label_text}")

# 매체 타겟팅 섹션 주변 HTML 추출
print("\n=== MEDIA TARGETING SECTION ===")
idx = html.find("매체")
if idx >= 0:
    start = max(0, idx - 200)
    end = min(len(html), idx + 2000)
    section = html[start:end]
    print(section)
