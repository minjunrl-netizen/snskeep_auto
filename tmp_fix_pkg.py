"""패키지 매핑 좋아요 서비스 267→268 변경"""
import json, sys
sys.path.insert(0, ".")
from app import create_app
from models import db, ProductMapping

app = create_app()
with app.app_context():
    m = db.session.get(ProductMapping, 11)
    pc = json.loads(m.package_config)

    # 267 -> 268 변경
    pc[0]["service_id"] = 268
    pc[0]["service_name"] = "[랜덤] 실제 한국인 자동 좋아요"

    m.package_config = json.dumps(pc, ensure_ascii=False)
    m.insta_service_id = 268
    m.insta_service_name = "[랜덤] 실제 한국인 자동 좋아요 (패키지 대표)"
    db.session.commit()

    # 확인
    pc2 = json.loads(m.package_config)
    print("업데이트 완료:")
    print(json.dumps(pc2, ensure_ascii=False, indent=2))
