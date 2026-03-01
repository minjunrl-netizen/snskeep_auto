"""캠페인 1343613 비활성화."""
from services.superap_client import SuperapClient

client = SuperapClient()
client.login()
base = "https://superap.io"

endpoints = [
    ("POST", "/service/reward/adver/deactive", {"ad_idx": "1343613"}),
    ("POST", "/service/reward/adver/pause", {"ad_idx": "1343613"}),
    ("POST", "/service/reward/adver/stop", {"ad_idx": "1343613"}),
    ("POST", "/service/reward/adver/status", {"ad_idx": "1343613", "status": "Deactive"}),
    ("GET", "/service/reward/adver/deactive?ad_idx=1343613", None),
]

for method, path, data in endpoints:
    url = base + path
    if method == "POST":
        resp = client.session.post(url, data=data, timeout=30, allow_redirects=False)
    else:
        resp = client.session.get(url, timeout=30, allow_redirects=False)
    loc = resp.headers.get("Location", "")
    body = resp.text[:150] if resp.status_code != 302 else ""
    print(f"{method} {path}: {resp.status_code} {loc} {body}")
