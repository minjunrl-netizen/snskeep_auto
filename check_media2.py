"""superap.io 매체 타겟팅 필드 확인 (2차)."""
import re
from services.superap_client import SuperapClient

client = SuperapClient()
client.login()

resp = client.session.get("https://superap.io/service/reward/adver/add", timeout=30)
html = resp.text

# 다양한 키워드로 검색
keywords = ["타겟", "MyB", "TIP", "캐시워크", "네트워크", "showTime", "adsomeType", "전체 선택", "체리포인트", "머니워크", "플러스팟"]
for kw in keywords:
    idx = html.find(kw)
    if idx >= 0:
        start = max(0, idx - 100)
        end = min(len(html), idx + 300)
        print(f"[{kw}] found at {idx}:")
        print(html[start:end].replace("\n", "\n  "))
        print("---")
    else:
        print(f"[{kw}] NOT FOUND")
        print("---")

# adsomeType 근처 전체 context
print("\n=== adsomeType FULL CONTEXT ===")
idx = html.find("adsomeType")
if idx >= 0:
    start = max(0, idx - 500)
    end = min(len(html), idx + 500)
    print(html[start:end])

# showTime 근처
print("\n=== showTime CONTEXT ===")
idx = html.find("showTime")
if idx >= 0:
    start = max(0, idx - 200)
    end = min(len(html), idx + 500)
    print(html[start:end])
