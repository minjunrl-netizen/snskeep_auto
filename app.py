import logging
from datetime import timedelta
from flask import Flask
from flask_login import LoginManager
from models import db, AdminUser, BankDeposit, ChargeRequest
from admin.routes import admin_bp
from cafe24.auth import oauth_bp
from api_public import public_bp
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

        # Migration: charge_requests 테이블에 tax 컬럼 추가
        for col_name in ("tax_issued", "tax_mgt_key", "tax_error"):
            try:
                default = "0" if col_name == "tax_issued" else "''"
                col_type = "BOOLEAN DEFAULT 0" if col_name == "tax_issued" else f"TEXT DEFAULT ''"
                with db.engine.connect() as conn:
                    conn.execute(sqlalchemy.text(
                        f"ALTER TABLE charge_requests ADD COLUMN {col_name} {col_type}"
                    ))
                    conn.commit()
                    logger.info("Migration: charge_requests.%s 컬럼 추가 완료", col_name)
            except Exception:
                pass

        # 초기 super_admin 계정 생성 (없을 경우)
        if not AdminUser.query.filter_by(username="bjdlclrh").first():
            admin = AdminUser(username="bjdlclrh", role="super_admin")
            admin.set_password("wnsrl1019")
            db.session.add(admin)
            db.session.commit()
            logger.info("초기 super_admin 계정 생성 완료")

    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(oauth_bp)
    app.register_blueprint(public_bp)

    return app


def start_scheduler(app):
    from apscheduler.schedulers.background import BackgroundScheduler
    from services.order_processor import process_new_orders
    from services.status_checker import check_order_statuses
    from services.campaign_scheduler import (
        auto_campaign_job, check_campaign_completion_job, sync_remains_job,
        auto_youtube_campaign_job, check_youtube_campaign_completion_job, sync_youtube_remains_job,
    )
    from services.popbill_bank import poll_deposits, expire_old_requests
    from services.telegram_notifier import notify_scheduler_failure, notify_health_check_fail

    scheduler = BackgroundScheduler()

    # ── 연속 실패 카운터 ──
    _fail_counts = {}

    def _run_job(job_name, func):
        """스케줄러 작업 래퍼 — 연속 실패 시 텔레그램 알림."""
        with app.app_context():
            try:
                func()
                # 성공 시 실패 카운터 리셋
                if _fail_counts.get(job_name, 0) > 0:
                    logger.info("[%s] 복구됨 (이전 연속 실패 %d회)", job_name, _fail_counts[job_name])
                _fail_counts[job_name] = 0
            except Exception as e:
                _fail_counts[job_name] = _fail_counts.get(job_name, 0) + 1
                count = _fail_counts[job_name]
                logger.exception("[%s] 오류 발생 (연속 %d회)", job_name, count)
                # 3회 연속 실패 시, 그 이후 매 3회마다 알림
                if count >= 3 and count % 3 == 0:
                    notify_scheduler_failure(job_name, str(e), count)

    def poll_job():
        _run_job("주문 폴링", process_new_orders)

    def status_job():
        _run_job("상태 체크", check_order_statuses)

    def campaign_job():
        _run_job("캠페인 자동 세팅", auto_campaign_job)

    def campaign_check_job():
        _run_job("캠페인 완료 체크", check_campaign_completion_job)

    def remains_sync_job():
        _run_job("리메인 동기화", sync_remains_job)

    def yt_campaign_job():
        _run_job("유튜브 캠페인 세팅", auto_youtube_campaign_job)

    def yt_campaign_check_job():
        _run_job("유튜브 완료 체크", check_youtube_campaign_completion_job)

    def yt_remains_sync_job():
        _run_job("유튜브 리메인 동기화", sync_youtube_remains_job)

    # ── 헬스체크 ──
    def health_check_job():
        """10분마다 외부 서비스 연결 상태 확인."""
        with app.app_context():
            # 1. 카페24 OAuth 토큰 확인
            try:
                from cafe24.auth import get_valid_token
                token = get_valid_token()
                if token is None:
                    notify_health_check_fail("카페24 OAuth", "유효한 토큰 없음 - 재인증 필요")
            except Exception as e:
                logger.exception("헬스체크: 카페24 토큰 확인 실패")
                notify_health_check_fail("카페24 OAuth", str(e))

            # 2. superap.io 로그인 테스트
            try:
                from services.superap_client import SuperapClient
                client = SuperapClient()
                client.login()
                logger.info("헬스체크: superap.io 로그인 정상")
            except Exception as e:
                logger.exception("헬스체크: superap.io 로그인 실패")
                notify_health_check_fail("superap.io", str(e))

            # 3. 인스타몬스터 API 확인
            try:
                from instamonster.client import get_balance
                balance = get_balance()
                if balance is not None:
                    logger.info("헬스체크: 인스타몬스터 API 정상 (잔액: %.0f)", balance)
                else:
                    notify_health_check_fail("인스타몬스터", "잔액 조회 실패 - API 키 확인 필요")
            except Exception as e:
                logger.exception("헬스체크: 인스타몬스터 API 확인 실패")
                notify_health_check_fail("인스타몬스터", str(e))

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

    # ── 무통장입금 (팝빌) 폴링: 20초마다 ──
    def deposit_poll_job():
        with app.app_context():
            try:
                poll_deposits()
            except Exception:
                logger.exception("팝빌 입금 폴링 중 오류 발생")

    # 충전 요청 만료 처리: 1시간마다
    def expire_job():
        with app.app_context():
            try:
                expire_old_requests()
            except Exception:
                logger.exception("충전 요청 만료 처리 중 오류")

    if config.POPBILL_LINK_ID and config.POPBILL_CORP_NUM:
        scheduler.add_job(deposit_poll_job, "interval", seconds=20, id="popbill_deposits")
        scheduler.add_job(expire_job, "interval", seconds=3600, id="expire_charge_requests")
        logger.info("팝빌 입금 폴링 스케줄러 등록 (20초 간격)")

    # 헬스체크: 10분마다
    scheduler.add_job(health_check_job, "interval", seconds=600, id="health_check")
    scheduler.start()
    logger.info(
        "스케줄러 시작 (폴링: %s초, 상태체크: 300초, 캠페인세팅: 180초, 캠페인완료체크: 180초, 리메인동기화: 180초, "
        "유튜브캠페인: 180초, 유튜브완료체크: 180초, 유튜브리메인: 180초, 헬스체크: 600초)",
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
