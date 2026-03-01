import logging
from datetime import timedelta
from flask import Flask
from flask_login import LoginManager
from models import db, AdminUser
from admin.routes import admin_bp
from cafe24.auth import oauth_bp
import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = config.FLASK_SECRET_KEY
    app.config["SQLALCHEMY_DATABASE_URI"] = config.DATABASE_URI
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # 세션 보안 설정
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    if not config.FLASK_DEBUG:
        app.config["SESSION_COOKIE_SECURE"] = True

    db.init_app(app)

    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = "admin.login"
    login_manager.login_message = "로그인이 필요합니다."
    login_manager.login_message_category = "danger"

    @login_manager.user_loader
    def load_user(user_id):
        return AdminUser.query.get(int(user_id))

    with app.app_context():
        db.create_all()

        # Migration: service_map 컬럼 추가 (없으면)
        import sqlalchemy
        try:
            with db.engine.connect() as conn:
                conn.execute(sqlalchemy.text(
                    "ALTER TABLE product_mappings ADD COLUMN service_map TEXT DEFAULT ''"
                ))
                conn.commit()
                logger.info("Migration: service_map 컬럼 추가 완료")
        except Exception:
            pass  # 이미 존재

        # Migration: package_config 컬럼 추가 (없으면)
        try:
            with db.engine.connect() as conn:
                conn.execute(sqlalchemy.text(
                    "ALTER TABLE product_mappings ADD COLUMN package_config TEXT DEFAULT ''"
                ))
                conn.commit()
                logger.info("Migration: package_config 컬럼 추가 완료")
        except Exception:
            pass  # 이미 존재

        # 초기 super_admin 계정 생성 (없을 경우)
        if not AdminUser.query.filter_by(username="bjdlclrh").first():
            admin = AdminUser(username="bjdlclrh", role="super_admin")
            admin.set_password("wnsrl1019")
            db.session.add(admin)
            db.session.commit()
            logger.info("초기 super_admin 계정 생성 완료")

    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(oauth_bp)

    return app


def start_scheduler(app):
    from apscheduler.schedulers.background import BackgroundScheduler
    from services.order_processor import process_new_orders
    from services.status_checker import check_order_statuses
    from services.campaign_scheduler import (
        auto_campaign_job, check_campaign_completion_job, sync_remains_job,
        auto_youtube_campaign_job, check_youtube_campaign_completion_job, sync_youtube_remains_job,
    )

    scheduler = BackgroundScheduler()

    def poll_job():
        with app.app_context():
            try:
                process_new_orders()
            except Exception:
                logger.exception("폴링 작업 중 오류 발생")

    def status_job():
        with app.app_context():
            try:
                check_order_statuses()
            except Exception:
                logger.exception("상태 체크 중 오류 발생")

    def campaign_job():
        with app.app_context():
            try:
                auto_campaign_job()
            except Exception:
                logger.exception("캠페인 자동 세팅 중 오류 발생")

    def campaign_check_job():
        with app.app_context():
            try:
                check_campaign_completion_job()
            except Exception:
                logger.exception("캠페인 완료 체크 중 오류 발생")

    def remains_sync_job():
        with app.app_context():
            try:
                sync_remains_job()
            except Exception:
                logger.exception("리메인 동기화 중 오류 발생")

    # ── 유튜브 스케줄러 ──
    def yt_campaign_job():
        with app.app_context():
            try:
                auto_youtube_campaign_job()
            except Exception:
                logger.exception("유튜브 캠페인 자동 세팅 중 오류 발생")

    def yt_campaign_check_job():
        with app.app_context():
            try:
                check_youtube_campaign_completion_job()
            except Exception:
                logger.exception("유튜브 캠페인 완료 체크 중 오류 발생")

    def yt_remains_sync_job():
        with app.app_context():
            try:
                sync_youtube_remains_job()
            except Exception:
                logger.exception("유튜브 리메인 동기화 중 오류 발생")

    # 주문 폴링: 90초마다
    scheduler.add_job(poll_job, "interval", seconds=config.POLLING_INTERVAL, id="poll_orders")
    # 상태 체크: 5분마다 (인스타몬스터 완료 여부 확인 → 카페24 배송완료 처리)
    scheduler.add_job(status_job, "interval", seconds=300, id="check_statuses")
    # superap 캠페인 자동 세팅: 3분마다 (대기 주문 → 캠페인 등록/연장 → in_progress)
    scheduler.add_job(campaign_job, "interval", seconds=180, id="campaign_setup")
    # superap 캠페인 완료 체크: 3분마다 (in_progress → TotalOff 확인 → completed)
    scheduler.add_job(campaign_check_job, "interval", seconds=180, id="campaign_check")
    # 리메인 동기화: 3분마다 (superap action_count → 인스타몬스터 remains 업데이트)
    scheduler.add_job(remains_sync_job, "interval", seconds=180, id="remains_sync")
    # 유튜브 캠페인 자동 세팅: 3분마다
    scheduler.add_job(yt_campaign_job, "interval", seconds=180, id="yt_campaign_setup")
    # 유튜브 캠페인 완료 체크: 3분마다
    scheduler.add_job(yt_campaign_check_job, "interval", seconds=180, id="yt_campaign_check")
    # 유튜브 리메인 동기화: 3분마다
    scheduler.add_job(yt_remains_sync_job, "interval", seconds=180, id="yt_remains_sync")
    scheduler.start()
    logger.info(
        "스케줄러 시작 (폴링: %s초, 상태체크: 300초, 캠페인세팅: 180초, 캠페인완료체크: 180초, 리메인동기화: 180초, "
        "유튜브캠페인: 180초, 유튜브완료체크: 180초, 유튜브리메인: 180초)",
        config.POLLING_INTERVAL,
    )
    return scheduler


if __name__ == "__main__":
    app = create_app()
    scheduler = start_scheduler(app)
    try:
        app.run(host="0.0.0.0", port=5000, debug=config.FLASK_DEBUG, use_reloader=False)
    finally:
        scheduler.shutdown()
