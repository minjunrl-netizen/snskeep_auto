"""캠페인 1343035의 이미지 URL 확인 스크립트."""
import re
from services.superap_client import SuperapClient

client = SuperapClient()
client.login()

resp = client.session.get(
    "https://superap.io/service/reward/adver/modify?ad_idx=1343035", timeout=30
)
print("status:", resp.status_code)

html = resp.text

# image_url input fields
for m in re.finditer(r'name=["\']image_url["\'][^>]*value=["\']([^"\']+)', html):
    print("image_url:", m.group(1))

# hidden inputs with image
for m in re.finditer(r'image_url[^>]{0,300}', html):
    print("found:", m.group()[:300])
    print("---")

# all image sources from superap
for m in re.finditer(r'src=["\']([^"\']+)', html):
    url = m.group(1)
    if "superap" in url or "res/" in url or ".png" in url or ".jpg" in url:
        print("img:", url)
