import json
from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


def utcnow():
    return datetime.now(timezone.utc)


class AdminUser(UserMixin, db.Model):
    __tablename__ = "admin_users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), default="admin")  # super_admin / admin
    permissions = db.Column(db.Text, default="[]")  # JSON: ["dashboard","mappings","orders","services","setup"]
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    ALL_PERMISSIONS = ["dashboard", "mappings", "orders", "services", "setup", "extractor", "youtube"]
    PERMISSION_LABELS = {
        "dashboard": "대시보드",
        "mappings": "상품 매핑",
        "orders": "주문 현황",
        "services": "서비스 목록",
        "setup": "설정",
        "extractor": "퀀텀 팔로워 세팅",
        "youtube": "퀀텀 유튜브 구독자",
    }

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_id(self):
        return str(self.id)

    @property
    def is_super_admin(self):
        return self.role == "super_admin"

    def get_permissions(self):
        if self.is_super_admin:
            return self.ALL_PERMISSIONS[:]
        try:
            return json.loads(self.permissions or "[]")
        except (json.JSONDecodeError, TypeError):
            return []

    def set_permissions(self, perm_list):
        valid = [p for p in perm_list if p in self.ALL_PERMISSIONS]
        self.permissions = json.dumps(valid)

    def has_permission(self, perm):
        if self.is_super_admin:
            return True
        return perm in self.get_permissions()


class ProductMapping(db.Model):
    __tablename__ = "product_mappings"

    id = db.Column(db.Integer, primary_key=True)
    cafe24_product_no = db.Column(db.Integer, nullable=False)
    cafe24_product_name = db.Column(db.Text, default="")
    insta_service_id = db.Column(db.Integer, nullable=False)
    insta_service_name = db.Column(db.Text, default="")

    # 주문 타입: default(일반) / subscription(자동-구독) / package(패키지-복수발주)
    order_type = db.Column(db.Text, nullable=False, default="default")

    # ── 일반 주문 설정 ──
    quantity = db.Column(db.Integer, default=0)  # 레거시 (사용 안 함)
    quantity_source = db.Column(db.Text, default="option")  # 레거시 (사용 안 함)
    quantity_option_name = db.Column(db.Text, default="")
    link_source = db.Column(db.Text, nullable=False, default="option")  # option / memo
    option_name = db.Column(db.Text, default="")  # 링크 추출할 옵션명

    # ── 조건부 서비스 매핑 (옵션값에 따라 서비스 분기) ──
    service_map = db.Column(db.Text, default="")

    # ── 패키지 설정 (1주문 → 복수 서비스 동시 발주) ──
    package_config = db.Column(db.Text, default="")

    def get_service_map(self):
        """service_map JSON 파싱. 유효하면 dict 반환, 아니면 None."""
        if not self.service_map:
            return None
        try:
            data = json.loads(self.service_map)
            if isinstance(data, dict) and "option_name" in data and "map" in data:
                return data
        except (json.JSONDecodeError, TypeError):
            pass
        return None

    def get_package_config(self):
        """package_config JSON 파싱. 유효하면 list 반환, 아니면 None."""
        if not self.package_config:
            return None
        try:
            data = json.loads(self.package_config)
            if isinstance(data, list) and len(data) > 0:
                return data
        except (json.JSONDecodeError, TypeError):
            pass
        return None

    # ── 구독(자동) 주문 설정 ──
    sub_username_option = db.Column(db.Text, default="")   # 인스타 아이디 추출할 옵션명
    sub_likes_option = db.Column(db.Text, default="")      # 좋아요 수량 추출할 옵션명
    sub_posts_option = db.Column(db.Text, default="")      # 게시물 수량 추출할 옵션명
    sub_delay = db.Column(db.Integer, default=0)             # 딜레이(분) - 0=즉시
    sub_expiry_days = db.Column(db.Integer, default=365)    # 만료일 (주문일 기준 N일 후)

    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=utcnow)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)


class ProcessedOrder(db.Model):
    __tablename__ = "processed_orders"
    __table_args__ = (
        db.UniqueConstraint("cafe24_order_id", "cafe24_order_item_id", name="uq_order_item"),
    )

    id = db.Column(db.Integer, primary_key=True)
    cafe24_order_id = db.Column(db.Text, nullable=False)
    cafe24_order_item_id = db.Column(db.Text, default="")
    insta_order_id = db.Column(db.Integer, nullable=True)
    service_id = db.Column(db.Integer, nullable=True)
    order_type = db.Column(db.Text, default="default")  # default / subscription
    link = db.Column(db.Text, default="")
    quantity = db.Column(db.Integer, default=0)
    status = db.Column(db.Text, default="pending")  # shipping / delivered / error / needs_review / partial / canceled / partial_refund
    error_message = db.Column(db.Text, default="")
    extra_info = db.Column(db.Text, default="")  # 구독 주문 시 상세 정보 (JSON)
    created_at = db.Column(db.DateTime, default=utcnow)


class OAuthToken(db.Model):
    __tablename__ = "oauth_tokens"

    id = db.Column(db.Integer, primary_key=True)
    access_token = db.Column(db.Text, nullable=False)
    refresh_token = db.Column(db.Text, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    refresh_expires_at = db.Column(db.DateTime, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
