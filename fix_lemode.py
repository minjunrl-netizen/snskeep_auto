"""1345632 lemode_shop URL만 수정 (활성화 안 함)"""
import sys
sys.path.insert(0, ".")
from app import create_app
from services.superap_client import SuperapClient

app = create_app()
with app.app_context():
    client = SuperapClient()
    client.login()
    result = client.update_campaign(ad_idx="1345632", username="lemode_shop")
    print(f"1345632 (lemode_shop): {result.get('ok')} - {result.get('message')}")
