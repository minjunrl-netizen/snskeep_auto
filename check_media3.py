"""superap.io 매체 타겟팅 JS 분석."""
import re
from services.superap_client import SuperapClient

client = SuperapClient()
client.login()

resp = client.session.get("https://superap.io/service/reward/adver/add", timeout=30)
html = resp.text

# publisher_layer 관련 JS 찾기
print("=== publisher_layer CONTEXT ===")
for m in re.finditer(r'publisher[^"\'<>\n]{0,300}', html, re.IGNORECASE):
    print(m.group()[:300])
    print("---")

# script 태그에서 매체 관련 코드 찾기
print("\n=== SCRIPT SECTIONS WITH publisher/media ===")
for m in re.finditer(r'<script[^>]*>([\s\S]*?)</script>', html):
    script_content = m.group(1)
    if 'publisher' in script_content.lower() or 'media' in script_content.lower():
        # publisher 관련 부분만 추출
        lines = script_content.split('\n')
        for i, line in enumerate(lines):
            if 'publisher' in line.lower() or 'media' in line.lower():
                start = max(0, i-2)
                end = min(len(lines), i+5)
                for j in range(start, end):
                    print(f"  {j}: {lines[j].rstrip()}")
                print("  ---")
