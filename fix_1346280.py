"""1346280 매체 타겟팅 수정"""
import sys
sys.path.insert(0, ".")
from app import create_app
from services.superap_client import SuperapClient

app = create_app()
with app.app_context():
    client = SuperapClient()
    client.login()
    result = client.update_campaign(
        ad_idx="1346280",
        username="sunheekimm_",
    )
    print("Result:", result)
