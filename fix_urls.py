"""잘못된 URL 캠페인 수정"""
import sys
sys.path.insert(0, ".")
from app import create_app
from services.superap_client import SuperapClient

app = create_app()
with app.app_context():
    client = SuperapClient()
    client.login()

    fixes = [
        ("1344610", "star_0_v"),
        ("1345874", "__joy84"),
    ]

    for ad_idx, username in fixes:
        result = client.update_campaign(ad_idx=ad_idx, username=username)
        print(f"{ad_idx} ({username}): {result.get('ok')} - {result.get('message')}")
