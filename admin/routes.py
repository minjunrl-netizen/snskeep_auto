import json
import logging
from functools import wraps
from datetime import datetime, timezone, timedelta

from urllib.parse import urlparse
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_user, logout_user, login_required, current_user

from models import db, ProductMapping, ProcessedOrder, OAuthToken, AdminUser
from instamonster.client import get_services, get_balance
from cafe24.auth import get_authorization_url
from cafe24.orders import get_all_products
from services.order_processor import retry_order as do_retry_order
from services.profile_extractor import (
    fetch_pending_orders as extractor_fetch_orders,
    scrape_profiles,
    check_and_update_history,
    import_history_from_csv,
    extract_username_from_link,
)
from services.superap_client import SuperapClient, load_campaign_settings, save_campaign_settings, _load_campaign_map
from services.campaign_scheduler import load_campaign_log
from services.youtube_scraper import (
    fetch_youtube_pending_orders,
    scrape_youtube_channels,
    check_and_update_youtube_history,
    cancel_youtube_orders,
    normalize_youtube_url,
)
from services.campaign_scheduler import get_orders_by_status
from services.popbill_bank import poll_deposits, get_bank_account_info
from services.instamonster_charge import add_payment
from services.popbill_tax import issue_tax_invoice, issue_cash_receipt, cancel_tax_invoice, cancel_cash_receipt
import config

logger = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__)

# 서비스 목록 캐시
_services_cache = {"data": [], "fetched_at": None}
# 카페24 상품 목록 캐시
_products_cache = {"data": [], "fetched_at": None}
CACHE_TTL = 300  # 5분


def super_admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_super_admin:
            flash("접근 권한이 없습니다.", "danger")
            return redirect(url_for("admin.dashboard"))
        return f(*args, **kwargs)
    return decorated


def permission_required(perm):
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated(*args, **kwargs):
            if not current_user.has_permission(perm):
                flash("접근 권한이 없습니다.", "danger")
                return redirect(url_for("admin.dashboard"))
            return f(*args, **kwargs)
        return decorated
    return decorator


# ── 로그인/로그아웃 ──

@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("admin.dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = AdminUser.query.filter_by(username=username).first()

        if user and user.is_active and user.check_password(password):
            login_user(user, remember=False)
            next_page = request.args.get("next")
            # Open Redirect 방지: 내부 경로만 허용
            if next_page:
                parsed = urlparse(next_page)
                if parsed.netloc or parsed.scheme:
                    next_page = None
            return redirect(next_page or url_for("admin.dashboard"))

        flash("아이디 또는 비밀번호가 올바르지 않습니다.", "danger")

    return render_template("login.html")


@admin_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("로그아웃되었습니다.", "success")
    return redirect(url_for("admin.login"))


# ── 비밀번호 변경 (본인) ──

@admin_bp.route("/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")

        if not current_user.check_password(current_pw):
            flash("현재 비밀번호가 올바르지 않습니다.", "danger")
        elif len(new_pw) < 6:
            flash("새 비밀번호는 6자 이상이어야 합니다.", "danger")
        elif new_pw != confirm_pw:
            flash("새 비밀번호가 일치하지 않습니다.", "danger")
        else:
            current_user.set_password(new_pw)
            db.session.commit()
            flash("비밀번호가 변경되었습니다.", "success")
            return redirect(url_for("admin.dashboard"))

    return render_template("password.html")


def _get_insta_services(force_refresh=False):
    """인스타몬스터 서비스 목록 (캐시 포함)"""
    now = datetime.now(timezone.utc)
    if (
        not force_refresh
        and _services_cache["data"]
        and _services_cache["fetched_at"]
        and (now - _services_cache["fetched_at"]).total_seconds() < CACHE_TTL
    ):
        return _services_cache["data"]

    try:
        result = get_services()
        if isinstance(result, list):
            _services_cache["data"] = result
            _services_cache["fetched_at"] = now
            return result
    except Exception:
        logger.exception("서비스 목록 조회 실패")

    return _services_cache["data"]


def _get_cafe24_products(force_refresh=False):
    """카페24 상품 목록 (캐시 포함)"""
    now = datetime.now(timezone.utc)
    if (
        not force_refresh
        and _products_cache["data"]
        and _products_cache["fetched_at"]
        and (now - _products_cache["fetched_at"]).total_seconds() < CACHE_TTL
    ):
        return _products_cache["data"]

    try:
        token = OAuthToken.query.first()
        if not token:
            return _products_cache["data"]

        result = get_all_products()
        if isinstance(result, list):
            _products_cache["data"] = result
            _products_cache["fetched_at"] = now
            return result
    except Exception:
        logger.exception("카페24 상품 목록 조회 실패")

    return _products_cache["data"]


# ── 대시보드 ──

@admin_bp.route("/")
@permission_required("dashboard")
def dashboard():
    # 잔액
    try:
        balance = get_balance()
    except Exception:
        balance = None

    # 오늘 통계
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_completed = ProcessedOrder.query.filter(
        ProcessedOrder.created_at >= today_start,
        ProcessedOrder.status.in_(["shipping", "delivered", "completed"]),
    ).count()
    today_errors = ProcessedOrder.query.filter(
        ProcessedOrder.created_at >= today_start,
        ProcessedOrder.status.in_(["error", "needs_review", "partial", "canceled"]),
    ).count()
    today_shipping = ProcessedOrder.query.filter(
        ProcessedOrder.created_at >= today_start,
        ProcessedOrder.status == "shipping",
    ).count()

    # 최근 5건
    recent_orders = ProcessedOrder.query.order_by(ProcessedOrder.created_at.desc()).limit(5).all()

    return render_template(
        "dashboard.html",
        balance=balance,
        today_completed=today_completed,
        today_errors=today_errors,
        today_shipping=today_shipping,
        recent_orders=recent_orders,
    )


# ── 상품 매핑 ──

@admin_bp.route("/mappings")
@permission_required("mappings")
def mappings():
    force_refresh = request.args.get("refresh") == "1"
    insta_services = _get_insta_services(force_refresh=force_refresh)
    cafe24_products = _get_cafe24_products(force_refresh=force_refresh)
    all_mappings = ProductMapping.query.order_by(ProductMapping.id.desc()).all()

    # OAuth 연동 여부 확인
    token = OAuthToken.query.first()
    if token and token.refresh_expires_at:
        refresh_exp = token.refresh_expires_at
        if refresh_exp.tzinfo is None:
            refresh_exp = refresh_exp.replace(tzinfo=timezone.utc)
        has_oauth = refresh_exp > datetime.now(timezone.utc)
    else:
        has_oauth = False

    if force_refresh:
        flash("목록을 새로고침했습니다.", "success")

    return render_template(
        "mappings.html",
        mappings=all_mappings,
        insta_services=insta_services,
        cafe24_products=cafe24_products,
        has_oauth=has_oauth,
    )


@admin_bp.route("/mappings/add", methods=["POST"])
@permission_required("mappings")
def add_mapping():
    # 서비스 이름 찾기
    service_id = int(request.form["insta_service_id"])
    services = _get_insta_services()
    service_name = ""
    for s in services:
        if int(s.get("service", 0)) == service_id:
            service_name = s.get("name", "")
            break

    order_type = request.form.get("order_type", "default")

    # 조건부 서비스 매핑 (JSON)
    service_map_raw = request.form.get("service_map", "").strip()
    if service_map_raw:
        try:
            smap = json.loads(service_map_raw)
            if not isinstance(smap, dict) or "option_name" not in smap or "map" not in smap:
                flash("서비스 맵 형식이 올바르지 않습니다.", "danger")
                return redirect(url_for("admin.mappings"))
        except json.JSONDecodeError:
            flash("서비스 맵 JSON 파싱 실패.", "danger")
            return redirect(url_for("admin.mappings"))

    package_config_raw = request.form.get("package_config", "").strip()
    if package_config_raw:
        try:
            pkg = json.loads(package_config_raw)
            if not isinstance(pkg, list) or len(pkg) == 0:
                flash("패키지 설정은 배열 형식이어야 합니다.", "danger")
                return redirect(url_for("admin.mappings"))
        except json.JSONDecodeError:
            flash("패키지 설정 JSON 파싱 실패.", "danger")
            return redirect(url_for("admin.mappings"))

    mapping = ProductMapping(
        cafe24_product_no=int(request.form["cafe24_product_no"]),
        cafe24_product_name=request.form.get("cafe24_product_name", ""),
        insta_service_id=service_id,
        insta_service_name=service_name,
        order_type=order_type,
        quantity_option_name=request.form.get("quantity_option_name", ""),
        link_source=request.form.get("link_source", "option"),
        option_name=request.form.get("option_name", ""),
        service_map=service_map_raw,
        package_config=package_config_raw,
        sub_username_option=request.form.get("sub_username_option", ""),
        sub_likes_option=request.form.get("sub_likes_option", ""),
        sub_posts_option=request.form.get("sub_posts_option", ""),
        sub_delay=int(request.form.get("sub_delay", 0)),
    )
    db.session.add(mapping)
    db.session.commit()
    flash("매핑이 추가되었습니다.", "success")
    return redirect(url_for("admin.mappings"))


@admin_bp.route("/mappings/<int:mapping_id>/edit", methods=["POST"])
@permission_required("mappings")
def edit_mapping(mapping_id):
    mapping = ProductMapping.query.get_or_404(mapping_id)

    service_id = int(request.form["insta_service_id"])
    services = _get_insta_services()
    service_name = ""
    for s in services:
        if int(s.get("service", 0)) == service_id:
            service_name = s.get("name", "")
            break

    # 조건부 서비스 매핑
    service_map_raw = request.form.get("service_map", "").strip()
    if service_map_raw:
        try:
            smap = json.loads(service_map_raw)
            if not isinstance(smap, dict) or "option_name" not in smap or "map" not in smap:
                flash("서비스 맵 형식이 올바르지 않습니다.", "danger")
                return redirect(url_for("admin.mappings"))
        except json.JSONDecodeError:
            flash("서비스 맵 JSON 파싱 실패.", "danger")
            return redirect(url_for("admin.mappings"))

    mapping.cafe24_product_no = int(request.form["cafe24_product_no"])
    mapping.cafe24_product_name = request.form.get("cafe24_product_name", "")
    mapping.insta_service_id = service_id
    mapping.insta_service_name = service_name
    mapping.order_type = request.form.get("order_type", "default")
    mapping.quantity_option_name = request.form.get("quantity_option_name", "")
    mapping.link_source = request.form.get("link_source", "option")
    mapping.option_name = request.form.get("option_name", "")
    mapping.service_map = service_map_raw

    package_config_raw = request.form.get("package_config", "").strip()
    if package_config_raw:
        try:
            pkg = json.loads(package_config_raw)
            if not isinstance(pkg, list) or len(pkg) == 0:
                flash("패키지 설정은 배열 형식이어야 합니다.", "danger")
                return redirect(url_for("admin.mappings"))
        except json.JSONDecodeError:
            flash("패키지 설정 JSON 파싱 실패.", "danger")
            return redirect(url_for("admin.mappings"))
    mapping.package_config = package_config_raw

    mapping.sub_username_option = request.form.get("sub_username_option", "")
    mapping.sub_likes_option = request.form.get("sub_likes_option", "")
    mapping.sub_posts_option = request.form.get("sub_posts_option", "")
    mapping.sub_delay = int(request.form.get("sub_delay", 0))
    mapping.is_active = request.form.get("is_active", "true") == "true"

    db.session.commit()
    flash("매핑이 수정되었습니다.", "success")
    return redirect(url_for("admin.mappings"))


@admin_bp.route("/mappings/<int:mapping_id>/delete", methods=["POST"])
@permission_required("mappings")
def delete_mapping(mapping_id):
    mapping = ProductMapping.query.get_or_404(mapping_id)
    db.session.delete(mapping)
    db.session.commit()
    flash("매핑이 삭제되었습니다.", "success")
    return redirect(url_for("admin.mappings"))


# ── 주문 현황 ──

@admin_bp.route("/orders")
@permission_required("orders")
def orders():
    filter_status = request.args.get("status", "")
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")

    query = ProcessedOrder.query

    if filter_status:
        query = query.filter_by(status=filter_status)

    if start_date:
        try:
            dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            query = query.filter(ProcessedOrder.created_at >= dt)
        except ValueError:
            pass

    if end_date:
        try:
            dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
            query = query.filter(ProcessedOrder.created_at < dt)
        except ValueError:
            pass

    all_orders = query.order_by(ProcessedOrder.created_at.desc()).limit(200).all()

    return render_template(
        "orders.html",
        orders=all_orders,
        filter_status=filter_status,
        start_date=start_date,
        end_date=end_date,
    )


@admin_bp.route("/orders/<int:order_id>/retry", methods=["POST"])
@permission_required("orders")
def retry_order(order_id):
    success, message = do_retry_order(order_id)
    if success:
        flash(message, "success")
    else:
        flash(message, "danger")
    return redirect(url_for("admin.orders"))


@admin_bp.route("/orders/retry-all-errors", methods=["POST"])
@permission_required("orders")
def retry_all_errors():
    """에러/검토필요 주문 일괄 재처리"""
    error_orders = ProcessedOrder.query.filter(
        ProcessedOrder.status.in_(["error", "needs_review"])
    ).all()

    if not error_orders:
        flash("재처리할 에러 주문이 없습니다.", "info")
        return redirect(url_for("admin.dashboard"))

    success_count = 0
    fail_count = 0
    for record in error_orders:
        ok, msg = do_retry_order(record.id)
        if ok:
            success_count += 1
        else:
            fail_count += 1

    flash(f"일괄 재처리 완료: 성공 {success_count}건, 실패 {fail_count}건", "success" if fail_count == 0 else "warning")
    return redirect(url_for("admin.dashboard"))


# ── 카페24 상품 API ──

@admin_bp.route("/api/products")
@permission_required("mappings")
def api_products():
    """카페24 상품 목록 JSON API (AJAX용)"""
    force_refresh = request.args.get("refresh") == "1"
    products = _get_cafe24_products(force_refresh=force_refresh)
    # 필요한 필드만 반환
    result = []
    for p in products:
        result.append({
            "product_no": p.get("product_no"),
            "product_name": p.get("product_name", ""),
            "product_code": p.get("product_code", ""),
            "display": p.get("display", ""),
            "selling": p.get("selling", ""),
            "price": p.get("price", ""),
        })
    return jsonify(result)


# ── 서비스 목록 ──

@admin_bp.route("/services")
@permission_required("services")
def services():
    force_refresh = request.args.get("refresh") == "1"
    insta_services = _get_insta_services(force_refresh=force_refresh)
    return render_template("services.html", services=insta_services)


# ── 설정 ──

@admin_bp.route("/setup")
@permission_required("setup")
def setup():
    token = OAuthToken.query.first()
    token_valid = False
    if token and token.refresh_expires_at:
        refresh_exp = token.refresh_expires_at
        if refresh_exp.tzinfo is None:
            refresh_exp = refresh_exp.replace(tzinfo=timezone.utc)
        token_valid = refresh_exp > datetime.now(timezone.utc)

    auth_url = get_authorization_url()

    return render_template(
        "setup.html",
        token=token,
        token_valid=token_valid,
        auth_url=auth_url,
        mall_id=config.CAFE24_MALL_ID,
        api_key_set=bool(config.INSTAMONSTER_API_KEY),
        polling_interval=config.POLLING_INTERVAL,
    )


# ── 계정 관리 (super_admin 전용) ──

@admin_bp.route("/users")
@super_admin_required
def users():
    all_users = AdminUser.query.order_by(AdminUser.created_at.desc()).all()
    return render_template("users.html", users=all_users, perm_labels=AdminUser.PERMISSION_LABELS)


@admin_bp.route("/users/add", methods=["POST"])
@super_admin_required
def add_user():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    if not username or not password:
        flash("아이디와 비밀번호를 입력해주세요.", "danger")
        return redirect(url_for("admin.users"))

    if AdminUser.query.filter_by(username=username).first():
        flash("이미 존재하는 아이디입니다.", "danger")
        return redirect(url_for("admin.users"))

    perms = request.form.getlist("permissions")

    user = AdminUser(username=username, role="admin")
    user.set_password(password)
    user.set_permissions(perms)
    db.session.add(user)
    db.session.commit()
    flash(f"관리자 '{username}'이(가) 추가되었습니다.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/permissions", methods=["POST"])
@super_admin_required
def update_permissions(user_id):
    user = AdminUser.query.get_or_404(user_id)
    if user.role == "super_admin":
        flash("총 관리자의 권한은 변경할 수 없습니다.", "danger")
        return redirect(url_for("admin.users"))

    perms = request.form.getlist("permissions")
    user.set_permissions(perms)
    db.session.commit()
    flash(f"'{user.username}'의 권한이 변경되었습니다.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
@super_admin_required
def reset_password(user_id):
    user = AdminUser.query.get_or_404(user_id)
    if user.role == "super_admin":
        flash("총 관리자의 비밀번호는 여기서 초기화할 수 없습니다.", "danger")
        return redirect(url_for("admin.users"))

    new_pw = request.form.get("new_password", "")
    if len(new_pw) < 6:
        flash("비밀번호는 6자 이상이어야 합니다.", "danger")
        return redirect(url_for("admin.users"))

    user.set_password(new_pw)
    db.session.commit()
    flash(f"'{user.username}'의 비밀번호가 초기화되었습니다.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@super_admin_required
def delete_user(user_id):
    user = AdminUser.query.get_or_404(user_id)
    if user.role == "super_admin":
        flash("총 관리자는 삭제할 수 없습니다.", "danger")
        return redirect(url_for("admin.users"))

    db.session.delete(user)
    db.session.commit()
    flash(f"관리자 '{user.username}'이(가) 삭제되었습니다.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@super_admin_required
def toggle_user(user_id):
    user = AdminUser.query.get_or_404(user_id)
    if user.role == "super_admin":
        flash("총 관리자는 비활성화할 수 없습니다.", "danger")
        return redirect(url_for("admin.users"))

    user.is_active = not user.is_active
    db.session.commit()
    status = "활성화" if user.is_active else "비활성화"
    flash(f"관리자 '{user.username}'이(가) {status}되었습니다.", "success")
    return redirect(url_for("admin.users"))


# ── 프로필 추출기 ──

@admin_bp.route("/extractor")
@permission_required("extractor")
def extractor():
    return render_template("extractor.html")


@admin_bp.route("/api/fetch-orders", methods=["POST"])
@permission_required("extractor")
def api_fetch_orders():
    """인스타몬스터 Admin API에서 대기 주문을 가져온다."""
    data = request.get_json(silent=True) or {}
    service_id = data.get("service_id", "32")
    limit = data.get("limit", 100)

    try:
        orders = extractor_fetch_orders(service_id=str(service_id), limit=int(limit))
        return jsonify({"ok": True, "orders": orders})
    except Exception as e:
        logger.exception("대기 주문 조회 실패")
        return jsonify({"ok": False, "error": str(e)}), 500


@admin_bp.route("/api/scrape-profiles", methods=["POST"])
@permission_required("extractor")
def api_scrape_profiles():
    """프로필 스크래핑 → 이력 업데이트 → 결과 반환."""
    data = request.get_json(silent=True) or {}
    usernames = data.get("usernames", [])
    quantity_map = data.get("quantity_map", {})

    if not usernames:
        return jsonify({"ok": False, "error": "추출할 사용자명이 없습니다."}), 400

    try:
        results = scrape_profiles(usernames)
        for r in results:
            r["수량"] = quantity_map.get(r["username"], "")
        results = check_and_update_history(results)
        return jsonify({"ok": True, "results": results})
    except Exception as e:
        logger.exception("프로필 스크래핑 실패")
        return jsonify({"ok": False, "error": str(e)}), 500


@admin_bp.route("/api/import-history", methods=["POST"])
@permission_required("extractor")
def api_import_history():
    """CSV 파일 업로드 → 이력 병합."""
    import tempfile

    if "file" not in request.files:
        return jsonify({"ok": False, "error": "파일이 없습니다."}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"ok": False, "error": "파일이 선택되지 않았습니다."}), 400

    import os
    suffix = os.path.splitext(file.filename)[1] or ".csv"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name

    try:
        new_count, update_count = import_history_from_csv(tmp_path)
        return jsonify({"ok": True, "new_count": new_count, "update_count": update_count})
    except Exception as e:
        logger.exception("이력 불러오기 실패")
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        os.unlink(tmp_path)


# ── superap.io 캠페인 자동 등록 ──

@admin_bp.route("/api/superap/register", methods=["POST"])
@permission_required("extractor")
def api_superap_register():
    """superap.io에 캠페인 일괄 등록/연장."""
    data = request.get_json(silent=True) or {}
    orders = data.get("orders", [])

    if not orders:
        return jsonify({"ok": False, "error": "등록할 주문이 없습니다."}), 400

    try:
        client = SuperapClient()
        results = client.process_orders_bulk(orders)
        success_count = sum(1 for r in results if r.get("ok"))
        fail_count = len(results) - success_count
        return jsonify({
            "ok": True,
            "results": results,
            "success_count": success_count,
            "fail_count": fail_count,
        })
    except Exception as e:
        logger.exception("superap 캠페인 등록 실패")
        return jsonify({"ok": False, "error": str(e)}), 500


@admin_bp.route("/api/superap/campaigns", methods=["GET"])
@permission_required("extractor")
def api_superap_campaigns():
    """superap.io 기존 캠페인 목록 조회."""
    try:
        client = SuperapClient()
        campaigns = client.get_all_campaigns()
        return jsonify({"ok": True, "campaigns": campaigns})
    except Exception as e:
        logger.exception("superap 캠페인 목록 조회 실패")
        return jsonify({"ok": False, "error": str(e)}), 500


# ── 캠페인 설정 ──

@admin_bp.route("/api/campaign-settings", methods=["GET"])
@permission_required("extractor")
def api_get_campaign_settings():
    """현재 캠페인 설정 반환."""
    return jsonify(load_campaign_settings())


@admin_bp.route("/api/campaign-settings", methods=["POST"])
@permission_required("extractor")
def api_save_campaign_settings():
    """캠페인 설정 저장."""
    data = request.get_json(silent=True) or {}

    # 허용된 키만 추출
    allowed_keys = ["title_template", "description", "budget_multiplier",
                    "duration_days", "geo", "event_limit",
                    "img1_url", "img2_url", "adsome_type",
                    "target_media_ids"]
    current = load_campaign_settings()
    for key in allowed_keys:
        if key in data:
            current[key] = data[key]

    # 타입 검증
    try:
        current["budget_multiplier"] = float(current["budget_multiplier"])
        current["duration_days"] = int(current["duration_days"])
        current["event_limit"] = str(current["event_limit"])
        if not isinstance(current.get("target_media_ids"), list):
            current["target_media_ids"] = []
    except (ValueError, TypeError) as e:
        return jsonify({"ok": False, "error": f"잘못된 값: {e}"}), 400

    save_campaign_settings(current)
    return jsonify({"ok": True, "settings": current})


@admin_bp.route("/api/superap/publishers", methods=["GET"])
@permission_required("extractor")
def api_superap_publishers():
    """superap.io 매체 타겟팅 목록 조회."""
    try:
        client = SuperapClient()
        publishers = client.get_publishers()
        return jsonify({"ok": True, "publishers": publishers})
    except Exception as e:
        logger.exception("매체 타겟팅 목록 조회 실패")
        return jsonify({"ok": False, "error": str(e)}), 500


# ── 세팅 로그 (크로스 체크) ──

@admin_bp.route("/setting-log")
@permission_required("extractor")
def setting_log():
    return render_template("setting_log.html")


@admin_bp.route("/api/setting-log/data")
@permission_required("extractor")
def api_setting_log_data():
    """인스타몬스터 주문 + superap 캠페인 크로스 체크 데이터."""
    try:
        # 1) 인스타몬스터 주문 (여러 상태)
        im_orders = []
        for status in ("processing", "in_progress", "completed"):
            im_orders.extend(get_orders_by_status(status))

        # 2) superap 캠페인 목록
        client = SuperapClient()
        campaigns = client.get_all_campaigns()

        # 3) campaign_map (username → ad_idx)
        campaign_map = _load_campaign_map()

        # 3-1) 캠페인 세팅 로그 (신규/연장 구분)
        campaign_log = load_campaign_log()

        # ad_idx → campaign dict
        campaigns_by_idx = {str(c["ad_idx"]): c for c in campaigns}

        # campaign ad_idx → username (역매핑)
        idx_to_username = {v: k for k, v in campaign_map.items()}

        # username → IM 주문 (가장 최신 1건)
        im_by_username = {}
        for o in im_orders:
            link = o.get("link", "")
            username = extract_username_from_link(link)
            if not username:
                continue
            if username not in im_by_username or o.get("id", 0) > im_by_username[username].get("id", 0):
                im_by_username[username] = o

        matched_usernames = set()

        rows = []
        summary = {"total": 0, "ok": 0, "completed": 0, "mismatch": 0, "no_campaign": 0, "no_order": 0}

        # 4) IM 주문 기준으로 행 생성
        for username, order in im_by_username.items():
            matched_usernames.add(username)
            ad_idx = campaign_map.get(username)
            campaign = campaigns_by_idx.get(str(ad_idx)) if ad_idx else None

            im_status = order.get("status", "")
            c_status = campaign.get("status", "") if campaign else None
            c_budget = int(campaign.get("total_budget", 0)) if campaign else None
            c_name = campaign.get("ad_name", "") if campaign else None

            if not campaign:
                match_status = "no_campaign"
            elif im_status == "in_progress" and c_status == "Active":
                match_status = "ok"
            elif im_status == "completed" and c_status == "TotalOff":
                match_status = "completed"
            else:
                match_status = "mismatch"

            summary[match_status] = summary.get(match_status, 0) + 1
            log_entry = campaign_log.get(username, {})
            rows.append({
                "username": username,
                "im_order_id": order.get("id"),
                "im_status": im_status,
                "im_quantity": order.get("quantity", 0),
                "im_remains": order.get("remains", 0),
                "ad_idx": int(ad_idx) if ad_idx else None,
                "campaign_status": c_status,
                "campaign_budget": c_budget,
                "campaign_action": int(campaign.get("action_count", 0) or 0) if campaign else None,
                "campaign_name": c_name,
                "match_status": match_status,
                "setting_type": log_entry.get("type"),
                "setting_time": log_entry.get("time"),
            })

        # 5) 캠페인은 있지만 IM 주문 없음
        for ad_idx_str, campaign in campaigns_by_idx.items():
            username = idx_to_username.get(ad_idx_str)
            if not username or username in matched_usernames:
                continue
            c_status = campaign.get("status", "")
            if c_status not in ("Active", "TotalOff"):
                continue

            summary["no_order"] += 1
            log_entry = campaign_log.get(username, {})
            rows.append({
                "username": username,
                "im_order_id": None,
                "im_status": None,
                "im_quantity": None,
                "im_remains": None,
                "ad_idx": int(ad_idx_str),
                "campaign_status": c_status,
                "campaign_budget": int(campaign.get("total_budget", 0)),
                "campaign_action": int(campaign.get("action_count", 0) or 0),
                "campaign_name": campaign.get("ad_name", ""),
                "match_status": "no_order",
                "setting_type": log_entry.get("type"),
                "setting_time": log_entry.get("time"),
            })

        summary["total"] = len(rows)

        return jsonify({"ok": True, "rows": rows, "summary": summary})
    except Exception as e:
        logger.exception("세팅 로그 데이터 조회 실패")
        return jsonify({"ok": False, "error": str(e)}), 500


# ── 유튜브 구독자 세팅 ──

@admin_bp.route("/youtube")
@permission_required("youtube")
def youtube():
    return render_template("youtube.html")


@admin_bp.route("/api/youtube/fetch-orders", methods=["POST"])
@permission_required("youtube")
def api_youtube_fetch_orders():
    """인스타몬스터 Admin API에서 유튜브 구독자 대기 주문을 가져온다 (서비스 129)."""
    data = request.get_json(silent=True) or {}
    service_id = data.get("service_id", "129")
    limit = data.get("limit", 100)

    try:
        orders = fetch_youtube_pending_orders(service_id=str(service_id), limit=int(limit))
        return jsonify({"ok": True, "orders": orders})
    except Exception as e:
        logger.exception("유튜브 대기 주문 조회 실패")
        return jsonify({"ok": False, "error": str(e)}), 500


@admin_bp.route("/api/youtube/cancel-orders", methods=["POST"])
@permission_required("youtube")
def api_youtube_cancel_orders():
    """인스타몬스터 유튜브 주문 취소."""
    data = request.get_json(silent=True) or {}
    order_ids = data.get("order_ids", [])

    if not order_ids:
        return jsonify({"ok": False, "error": "취소할 주문이 없습니다."}), 400

    try:
        order_ids = [int(oid) for oid in order_ids]
        result = cancel_youtube_orders(order_ids)
        return jsonify(result)
    except Exception as e:
        logger.exception("유튜브 주문 취소 실패")
        return jsonify({"ok": False, "error": str(e)}), 500


@admin_bp.route("/api/youtube/scrape-channels", methods=["POST"])
@permission_required("youtube")
def api_youtube_scrape_channels():
    """유튜브 채널 스크래핑 → 이력 업데이트 → 성공/실패 분리 반환."""
    data = request.get_json(silent=True) or {}
    channel_urls = data.get("channel_urls", [])
    quantity_map = data.get("quantity_map", {})
    order_id_map = data.get("order_id_map", {})

    if not channel_urls:
        return jsonify({"ok": False, "error": "스크래핑할 채널 URL이 없습니다."}), 400

    try:
        results = scrape_youtube_channels(channel_urls)

        # 성공한 채널 URL 세트 (정규화된 URL 기준)
        success_urls = set()
        for r in results:
            success_urls.add(r["channel_url"].lower())
            r["수량"] = quantity_map.get(r["channel_url"], "")

        results = check_and_update_youtube_history(results)

        # 실패 채널 감지: 입력 URL 중 결과에 없는 것
        failed = []
        for raw_url in channel_urls:
            norm = normalize_youtube_url(raw_url).lower()
            if norm and norm not in success_urls:
                failed.append({
                    "channel_url": raw_url,
                    "normalized_url": normalize_youtube_url(raw_url),
                    "order_id": order_id_map.get(raw_url),
                    "수량": quantity_map.get(raw_url, ""),
                    "reason": "채널을 찾을 수 없음 (존재하지 않거나 삭제된 채널)",
                })

        return jsonify({
            "ok": True,
            "results": results,
            "failed": failed,
        })
    except Exception as e:
        logger.exception("유튜브 채널 스크래핑 실패")
        return jsonify({"ok": False, "error": str(e)}), 500


@admin_bp.route("/api/youtube/superap/register", methods=["POST"])
@permission_required("youtube")
def api_youtube_superap_register():
    """superap.io에 유튜브 캠페인 일괄 등록/연장."""
    data = request.get_json(silent=True) or {}
    orders = data.get("orders", [])

    if not orders:
        return jsonify({"ok": False, "error": "등록할 주문이 없습니다."}), 400

    try:
        client = SuperapClient("youtube")
        results = client.process_orders_bulk(orders)
        success_count = sum(1 for r in results if r.get("ok"))
        fail_count = len(results) - success_count
        return jsonify({
            "ok": True,
            "results": results,
            "success_count": success_count,
            "fail_count": fail_count,
        })
    except Exception as e:
        logger.exception("유튜브 superap 캠페인 등록 실패")
        return jsonify({"ok": False, "error": str(e)}), 500


@admin_bp.route("/api/youtube/campaign-settings", methods=["GET"])
@permission_required("youtube")
def api_youtube_get_campaign_settings():
    """유튜브 캠페인 설정 반환."""
    return jsonify(load_campaign_settings("youtube"))


@admin_bp.route("/api/youtube/campaign-settings", methods=["POST"])
@permission_required("youtube")
def api_youtube_save_campaign_settings():
    """유튜브 캠페인 설정 저장."""
    data = request.get_json(silent=True) or {}

    allowed_keys = ["title_template", "detail_type", "description", "budget_multiplier",
                    "duration_days", "geo", "event_limit",
                    "img1_url", "img2_url", "adsome_type",
                    "target_media_ids"]
    current = load_campaign_settings("youtube")
    for key in allowed_keys:
        if key in data:
            current[key] = data[key]

    try:
        current["budget_multiplier"] = float(current["budget_multiplier"])
        current["duration_days"] = int(current["duration_days"])
        current["event_limit"] = str(current["event_limit"])
        if not isinstance(current.get("target_media_ids"), list):
            current["target_media_ids"] = []
    except (ValueError, TypeError) as e:
        return jsonify({"ok": False, "error": f"잘못된 값: {e}"}), 400

    save_campaign_settings(current, "youtube")
    return jsonify({"ok": True, "settings": current})


@admin_bp.route("/api/youtube/superap/publishers", methods=["GET"])
@permission_required("youtube")
def api_youtube_superap_publishers():
    """superap.io 매체 타겟팅 목록 조회 (유튜브)."""
    try:
        client = SuperapClient("youtube")
        publishers = client.get_publishers()
        return jsonify({"ok": True, "publishers": publishers})
    except Exception as e:
        logger.exception("유튜브 매체 타겟팅 목록 조회 실패")
        return jsonify({"ok": False, "error": str(e)}), 500


@admin_bp.route("/youtube-setting-log")
@permission_required("youtube")
def youtube_setting_log():
    return render_template("youtube_setting_log.html")


@admin_bp.route("/api/youtube/setting-log/data")
@permission_required("youtube")
def api_youtube_setting_log_data():
    """유튜브 인스타몬스터 주문 + superap 캠페인 크로스 체크 데이터."""
    try:
        # 1) 인스타몬스터 유튜브 주문 (서비스 129, 여러 상태)
        im_orders = []
        for status in ("processing", "in_progress", "completed"):
            im_orders.extend(get_orders_by_status(status, service_id="129"))

        # 2) superap 캠페인 목록
        client = SuperapClient("youtube")
        campaigns = client.get_all_campaigns()

        # 3) youtube_campaign_map (channel_url → ad_idx)
        campaign_map = _load_campaign_map("youtube")

        # ad_idx → campaign dict
        campaigns_by_idx = {str(c["ad_idx"]): c for c in campaigns}

        # ad_idx → channel_url (역매핑)
        idx_to_url = {v: k for k, v in campaign_map.items()}

        # channel_url → IM 주문 (가장 최신 1건)
        im_by_url = {}
        for o in im_orders:
            raw_link = o.get("link", "").strip()
            if not raw_link:
                continue
            channel_url = normalize_youtube_url(raw_link)
            if not channel_url:
                continue
            if channel_url not in im_by_url or o.get("id", 0) > im_by_url[channel_url].get("id", 0):
                im_by_url[channel_url] = o

        matched_urls = set()

        rows = []
        summary = {"total": 0, "ok": 0, "completed": 0, "mismatch": 0, "no_campaign": 0, "no_order": 0}

        # 4) IM 주문 기준으로 행 생성
        for channel_url, order in im_by_url.items():
            matched_urls.add(channel_url)
            ad_idx = campaign_map.get(channel_url)
            campaign = campaigns_by_idx.get(str(ad_idx)) if ad_idx else None

            im_status = order.get("status", "")
            c_status = campaign.get("status", "") if campaign else None
            c_budget = int(campaign.get("total_budget", 0)) if campaign else None

            if not campaign:
                match_status = "no_campaign"
            elif im_status == "in_progress" and c_status == "Active":
                match_status = "ok"
            elif im_status == "completed" and c_status == "TotalOff":
                match_status = "completed"
            else:
                match_status = "mismatch"

            summary[match_status] = summary.get(match_status, 0) + 1
            rows.append({
                "channel_url": channel_url,
                "im_order_id": order.get("id"),
                "im_status": im_status,
                "im_quantity": order.get("quantity", 0),
                "im_remains": order.get("remains", 0),
                "ad_idx": int(ad_idx) if ad_idx else None,
                "campaign_status": c_status,
                "campaign_budget": c_budget,
                "campaign_action": int(campaign.get("action_count", 0) or 0) if campaign else None,
                "match_status": match_status,
            })

        # 5) 캠페인은 있지만 IM 주문 없음
        for ad_idx_str, campaign in campaigns_by_idx.items():
            channel_url = idx_to_url.get(ad_idx_str)
            if not channel_url or channel_url in matched_urls:
                continue
            c_status = campaign.get("status", "")
            if c_status not in ("Active", "TotalOff"):
                continue

            summary["no_order"] += 1
            rows.append({
                "channel_url": channel_url,
                "im_order_id": None,
                "im_status": None,
                "im_quantity": None,
                "im_remains": None,
                "ad_idx": int(ad_idx_str),
                "campaign_status": c_status,
                "campaign_budget": int(campaign.get("total_budget", 0)),
                "campaign_action": int(campaign.get("action_count", 0) or 0),
                "match_status": "no_order",
            })

        summary["total"] = len(rows)

        return jsonify({"ok": True, "rows": rows, "summary": summary})
    except Exception as e:
        logger.exception("유튜브 세팅 로그 데이터 조회 실패")
        return jsonify({"ok": False, "error": str(e)}), 500


# ══════════════════════════════════════════════
# ── 무통장입금 ──
# ══════════════════════════════════════════════

@admin_bp.route("/deposits")
@permission_required("deposits")
def deposits():
    """무통장입금 현황 페이지."""
    from models import BankDeposit

    filter_status = request.args.get("status", "")
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")

    query = BankDeposit.query

    if filter_status:
        query = query.filter_by(status=filter_status)

    if start_date:
        try:
            dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            query = query.filter(BankDeposit.transaction_at >= dt)
        except ValueError:
            pass

    if end_date:
        try:
            dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
            query = query.filter(BankDeposit.transaction_at < dt)
        except ValueError:
            pass

    all_deposits = query.order_by(BankDeposit.transaction_at.desc()).limit(300).all()

    # 오늘 통계
    kst = timezone(timedelta(hours=9))
    today_start = datetime.now(kst).replace(hour=0, minute=0, second=0, microsecond=0)
    today_deposits = BankDeposit.query.filter(BankDeposit.transaction_at >= today_start).all()
    today_total_amount = sum(d.amount for d in today_deposits)
    today_count = len(today_deposits)
    today_new = sum(1 for d in today_deposits if d.status == "new")

    return render_template(
        "deposits.html",
        deposits=all_deposits,
        filter_status=filter_status,
        start_date=start_date,
        end_date=end_date,
        today_total_amount=today_total_amount,
        today_count=today_count,
        today_new=today_new,
        popbill_configured=bool(config.POPBILL_CORP_NUM and config.POPBILL_BANK_CODE),
    )


@admin_bp.route("/deposits/<int:deposit_id>/confirm", methods=["POST"])
@permission_required("deposits")
def confirm_deposit(deposit_id):
    """입금 확인 처리."""
    from models import BankDeposit

    deposit = BankDeposit.query.get_or_404(deposit_id)
    matched_order = request.form.get("matched_order_id", "").strip()

    deposit.status = "confirmed"
    if matched_order:
        deposit.matched_order_id = matched_order
    db.session.commit()

    flash(f"입금 #{deposit_id} ({deposit.depositor_name}, {deposit.amount:,}원) 확인 처리됨.", "success")
    return redirect(url_for("admin.deposits"))


@admin_bp.route("/deposits/<int:deposit_id>/match", methods=["POST"])
@permission_required("deposits")
def match_deposit(deposit_id):
    """입금-주문 매칭."""
    from models import BankDeposit

    deposit = BankDeposit.query.get_or_404(deposit_id)
    order_id = request.form.get("order_id", "").strip()

    if not order_id:
        flash("주문번호를 입력해주세요.", "danger")
        return redirect(url_for("admin.deposits"))

    deposit.status = "matched"
    deposit.matched_order_id = order_id
    db.session.commit()

    flash(f"입금 #{deposit_id} → 주문 {order_id} 매칭 완료.", "success")
    return redirect(url_for("admin.deposits"))


@admin_bp.route("/api/deposits/poll", methods=["POST"])
@permission_required("deposits")
def api_poll_deposits():
    """수동으로 팝빌 입금 폴링 실행."""
    try:
        poll_deposits()
        return jsonify({"ok": True, "message": "폴링 완료"})
    except Exception as e:
        logger.exception("수동 폴링 실패")
        return jsonify({"ok": False, "error": str(e)}), 500


@admin_bp.route("/api/deposits/stats")
@permission_required("deposits")
def api_deposit_stats():
    """입금 통계 JSON."""
    from models import BankDeposit

    kst = timezone(timedelta(hours=9))
    today_start = datetime.now(kst).replace(hour=0, minute=0, second=0, microsecond=0)

    today_deposits = BankDeposit.query.filter(BankDeposit.transaction_at >= today_start).all()
    return jsonify({
        "ok": True,
        "today_count": len(today_deposits),
        "today_amount": sum(d.amount for d in today_deposits),
        "today_new": sum(1 for d in today_deposits if d.status == "new"),
        "today_matched": sum(1 for d in today_deposits if d.status == "matched"),
        "today_confirmed": sum(1 for d in today_deposits if d.status == "confirmed"),
    })


# ── 충전 요청 관리 ──

@admin_bp.route("/charge-requests")
@permission_required("deposits")
def charge_requests():
    """충전 요청 현황 페이지."""
    from models import ChargeRequest

    filter_status = request.args.get("status", "")
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")
    search_name = request.args.get("search_name", "").strip()
    page = request.args.get("page", 1, type=int)
    dep_page = request.args.get("dep_page", 1, type=int)
    per_page = 30

    # 2달 이내만 조회
    two_months_ago = datetime.now(timezone.utc) - timedelta(days=60)

    query = ChargeRequest.query.filter(ChargeRequest.created_at >= two_months_ago)

    if filter_status:
        query = query.filter_by(status=filter_status)

    if search_name:
        query = query.filter(ChargeRequest.depositor_name.contains(search_name))

    # 날짜 필터 (charged_at 기준)
    date_filter_query = ChargeRequest.query.filter_by(status="charged").filter(ChargeRequest.created_at >= two_months_ago)
    if start_date:
        try:
            dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            query = query.filter(ChargeRequest.created_at >= dt)
            date_filter_query = date_filter_query.filter(ChargeRequest.charged_at >= dt)
        except ValueError:
            pass
    if end_date:
        try:
            dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
            query = query.filter(ChargeRequest.created_at < dt)
            date_filter_query = date_filter_query.filter(ChargeRequest.charged_at < dt)
        except ValueError:
            pass

    # 페이지네이션
    total_requests = query.count()
    total_req_pages = max(1, (total_requests + per_page - 1) // per_page)
    all_requests = query.order_by(ChargeRequest.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()

    # UTC → KST 변환
    kst = timezone(timedelta(hours=9))
    for req in all_requests:
        if req.created_at and req.created_at.tzinfo is None:
            req._kst_created = req.created_at.replace(tzinfo=timezone.utc).astimezone(kst)
        elif req.created_at:
            req._kst_created = req.created_at.astimezone(kst)
        else:
            req._kst_created = None

        if req.charged_at and req.charged_at.tzinfo is None:
            req._kst_charged = req.charged_at.replace(tzinfo=timezone.utc).astimezone(kst)
        elif req.charged_at:
            req._kst_charged = req.charged_at.astimezone(kst)
        else:
            req._kst_charged = None

    # 매칭된 입금시간 조회
    from models import BankDeposit
    deposit_times = {}
    for req in all_requests:
        if req.matched_deposit_id:
            dep = BankDeposit.query.get(req.matched_deposit_id)
            if dep and dep.transaction_at:
                deposit_times[req.id] = dep.transaction_at

    # 전체 입금 내역 (2달 이내)
    deposit_query = BankDeposit.query.filter(BankDeposit.transaction_at >= two_months_ago)
    if start_date:
        try:
            dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            deposit_query = deposit_query.filter(BankDeposit.transaction_at >= dt)
        except ValueError:
            pass
    if end_date:
        try:
            dt = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
            deposit_query = deposit_query.filter(BankDeposit.transaction_at < dt)
        except ValueError:
            pass
    total_deposits = deposit_query.count()
    total_dep_pages = max(1, (total_deposits + per_page - 1) // per_page)
    all_deposits = deposit_query.order_by(BankDeposit.transaction_at.desc()).offset((dep_page - 1) * per_page).limit(per_page).all()

    # 기본 통계
    pending_count = ChargeRequest.query.filter_by(status="pending").count()
    charged_count = ChargeRequest.query.filter_by(status="charged").count()
    failed_count = ChargeRequest.query.filter_by(status="failed").count()

    # 매출 통계 (날짜 필터 적용)
    charged_list = date_filter_query.all()
    cash_sales = sum(r.amount for r in charged_list if r.tax_type == 0 or (not r.tax_issued and r.tax_type != 0))
    tax_invoice_sales = sum(r.amount for r in charged_list if r.tax_type == 1 and r.tax_issued)
    cash_receipt_sales = sum(r.amount for r in charged_list if r.tax_type == 2 and r.tax_issued)
    total_sales = sum(r.amount for r in charged_list)

    return render_template(
        "charge_requests.html",
        requests=all_requests,
        filter_status=filter_status,
        start_date=start_date,
        end_date=end_date,
        search_name=search_name,
        pending_count=pending_count,
        charged_count=charged_count,
        failed_count=failed_count,
        cash_sales=cash_sales,
        tax_invoice_sales=tax_invoice_sales,
        cash_receipt_sales=cash_receipt_sales,
        total_sales=total_sales,
        deposit_times=deposit_times,
        all_deposits=all_deposits,
        page=page,
        total_req_pages=total_req_pages,
        dep_page=dep_page,
        total_dep_pages=total_dep_pages,
    )


@admin_bp.route("/charge-requests/<int:req_id>/manual-charge", methods=["POST"])
@permission_required("deposits")
def manual_charge(req_id):
    """수동 충전 처리."""
    from models import ChargeRequest

    req = ChargeRequest.query.get_or_404(req_id)
    if req.status == "charged":
        flash("이미 충전 완료된 요청입니다.", "warning")
        return redirect(url_for("admin.charge_requests"))

    result = add_payment(
        username=req.username,
        amount=req.charge_amount,
        memo=f"무통장입금 - {req.amount:,}원(부가세 제외 {req.charge_amount:,}원 충전)",
    )

    if result.get("ok"):
        req.status = "charged"
        req.payment_id = result.get("payment_id")
        req.charged_at = datetime.now(timezone.utc)
        db.session.commit()
        flash(f"수동 충전 완료: {req.username}, {req.charge_amount:,}원", "success")

        # 세금계산서/현금영수증 자동발행
        if req.tax_type == 1:
            tax_result = issue_tax_invoice(req)
            if tax_result.get("ok"):
                req.tax_issued = True
                req.tax_mgt_key = tax_result.get("mgt_key", "")
                db.session.commit()
                flash("세금계산서 자동발행 완료", "success")
            else:
                req.tax_error = tax_result.get("error", "")
                db.session.commit()
                flash(f"세금계산서 발행 실패: {tax_result.get('error')}", "warning")
        elif req.tax_type == 2:
            tax_result = issue_cash_receipt(req)
            if tax_result.get("ok"):
                req.tax_issued = True
                req.tax_mgt_key = tax_result.get("mgt_key", "")
                db.session.commit()
                flash("현금영수증 자동발행 완료", "success")
            else:
                req.tax_error = tax_result.get("error", "")
                db.session.commit()
                flash(f"현금영수증 발행 실패: {tax_result.get('error')}", "warning")
    else:
        req.status = "failed"
        req.error_message = result.get("error", "")
        db.session.commit()
        flash(f"충전 실패: {result.get('error')}", "danger")

    return redirect(url_for("admin.charge_requests"))


@admin_bp.route("/charge-requests/<int:req_id>/cancel", methods=["POST"])
@permission_required("deposits")
def cancel_charge_request(req_id):
    """충전 요청 취소."""
    from models import ChargeRequest

    req = ChargeRequest.query.get_or_404(req_id)
    if req.status == "charged":
        flash("이미 충전 완료된 요청은 취소할 수 없습니다.", "danger")
        return redirect(url_for("admin.charge_requests"))

    req.status = "expired"
    db.session.commit()
    flash(f"충전 요청 #{req.id} 취소됨.", "success")
    return redirect(url_for("admin.charge_requests"))


@admin_bp.route("/charge-requests/<int:req_id>/issue-tax", methods=["GET", "POST"])
@permission_required("deposits")
def issue_tax(req_id):
    """세금계산서/현금영수증 수동발행 페이지."""
    from models import ChargeRequest

    req = ChargeRequest.query.get_or_404(req_id)

    if request.method == "POST":
        tax_type = int(request.form.get("tax_type", "0"))
        tax_info = {}

        if tax_type == 1:
            tax_info = {
                "company": request.form.get("company", ""),
                "biz_no": request.form.get("biz_no", ""),
                "ceo": request.form.get("ceo", ""),
                "contact": request.form.get("contact", ""),
                "email": request.form.get("email", ""),
            }
        elif tax_type == 2:
            tax_info = {"phone": request.form.get("phone", "")}
        else:
            flash("발행 유형을 선택해주세요.", "danger")
            return redirect(url_for("admin.issue_tax", req_id=req_id))

        # 정보 업데이트
        import json as _json
        req.tax_type = tax_type
        req.tax_info = _json.dumps(tax_info, ensure_ascii=False)
        db.session.commit()

        # 발행
        if tax_type == 1:
            result = issue_tax_invoice(req)
        else:
            result = issue_cash_receipt(req)

        if result.get("ok"):
            req.tax_issued = True
            req.tax_mgt_key = result.get("mgt_key", "")
            req.tax_error = ""
            db.session.commit()
            type_name = "세금계산서" if tax_type == 1 else "현금영수증"
            flash(f"{type_name} 발행 완료 (문서번호: {result.get('mgt_key')})", "success")
        else:
            req.tax_error = result.get("error", "")
            db.session.commit()
            flash(f"발행 실패: {result.get('error')}", "danger")

        return redirect(url_for("admin.charge_requests"))

    # GET: 기존 세금 정보 로드
    import json as _json
    try:
        existing_info = _json.loads(req.tax_info) if req.tax_info else {}
    except (ValueError, TypeError):
        existing_info = {}

    return render_template("issue_tax.html", req=req, existing_info=existing_info)


@admin_bp.route("/charge-requests/<int:req_id>/cancel-tax", methods=["POST"])
@permission_required("deposits")
def cancel_tax(req_id):
    """세금계산서/현금영수증 발행취소."""
    from models import ChargeRequest

    req = ChargeRequest.query.get_or_404(req_id)
    if not req.tax_issued:
        flash("발행된 계산서가 없습니다.", "danger")
        return redirect(url_for("admin.charge_requests"))

    if not req.tax_mgt_key:
        flash("문서번호가 없어 취소할 수 없습니다.", "danger")
        return redirect(url_for("admin.charge_requests"))

    if req.tax_type == 1:
        result = cancel_tax_invoice(req.tax_mgt_key)
        type_name = "세금계산서"
    elif req.tax_type == 2:
        result = cancel_cash_receipt(req)
        type_name = "현금영수증"
    else:
        flash("발행 유형을 확인할 수 없습니다.", "danger")
        return redirect(url_for("admin.charge_requests"))

    if result.get("ok"):
        req.tax_issued = False
        req.tax_mgt_key = ""
        req.tax_error = ""
        db.session.commit()
        flash(f"{type_name} 취소 완료", "success")
    else:
        flash(f"{type_name} 취소 실패: {result.get('error')}", "danger")

    return redirect(url_for("admin.charge_requests"))
