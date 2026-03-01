"""superap.io 매체 타겟팅 - JS 파일 및 API 분석."""
import re
from services.superap_client import SuperapClient

client = SuperapClient()
client.login()

resp = client.session.get("https://superap.io/service/reward/adver/add", timeout=30)
html = resp.text

# 외부 JS 파일 목록
print("=== EXTERNAL JS FILES ===")
for m in re.finditer(r'src=["\']([^"\']+\.js[^"\']*)["\']', html):
    print(m.group(1))

# publisher_layer 포함 전체 form 주변
print("\n=== publisher_layer surrounding ===")
idx = html.find("publisher_layer")
if idx >= 0:
    start = max(0, idx - 500)
    end = min(len(html), idx + 500)
    print(html[start:end])

# detail_type 변경 시 호출되는 JS 함수 찾기
print("\n=== detail_type CHANGE HANDLER ===")
for m in re.finditer(r'detail_type[^<\n]{0,500}', html):
    txt = m.group()
    if 'change' in txt.lower() or 'function' in txt.lower() or 'ajax' in txt.lower() or 'fetch' in txt.lower():
        print(txt[:500])
        print("---")

# 전체 inline script 출력 (마지막 큰 script)
scripts = list(re.finditer(r'<script[^>]*>([\s\S]*?)</script>', html))
if scripts:
    last_script = scripts[-1].group(1).strip()
    print(f"\n=== LAST INLINE SCRIPT ({len(last_script)} chars) ===")
    print(last_script[:3000])
