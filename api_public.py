"""공개 API — 인스타몬스터 addfunds 폼에서 호출하는 충전 요청 접수 엔드포인트."""

import json
import logging
import math
from flask import Blueprint, request, jsonify, render_template_string

from models import db, ChargeRequest

logger = logging.getLogger(__name__)

public_bp = Blueprint("public", __name__)


# 허용된 입금 금액 목록 (부가세 포함)
ALLOWED_AMOUNTS = [
    11000, 22000, 33000, 44000, 55000,
    110000, 220000, 330000, 440000, 550000,
    1100000, 2200000, 3300000,
]

# 충전 완료 페이지 HTML
CHARGE_SUCCESS_HTML = """
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>충전 요청 완료</title>
    <link href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard/dist/web/static/pretendard.css" rel="stylesheet">
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: Pretendard Variable, Pretendard, -apple-system, sans-serif;
            background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
            margin: 0; padding: 20px;
            display: flex; justify-content: center; align-items: center; min-height: 100vh;
        }

        /* 카드 */
        .card {
            background: #fff; border-radius: 24px; padding: 0;
            max-width: 420px; width: 100%; text-align: center;
            box-shadow: 0 4px 24px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.04);
            overflow: hidden;
            animation: cardIn 0.6s cubic-bezier(0.16, 1, 0.3, 1) forwards;
            opacity: 0; transform: translateY(30px);
        }
        @keyframes cardIn {
            to { opacity: 1; transform: translateY(0); }
        }

        /* 상단 헤더 */
        .card-header {
            background: linear-gradient(135deg, #ff6e75 0%, #ff4f6d 100%);
            padding: 32px 24px 28px;
            position: relative;
            overflow: hidden;
        }
        .card-header::before {
            content: '';
            position: absolute; top: -50%; left: -50%;
            width: 200%; height: 200%;
            background: radial-gradient(circle, rgba(255,255,255,0.1) 0%, transparent 60%);
            animation: shimmer 3s ease-in-out infinite;
        }
        @keyframes shimmer {
            0%, 100% { transform: translate(0, 0); }
            50% { transform: translate(10%, 10%); }
        }

        /* 체크 아이콘 애니메이션 */
        .check-circle {
            width: 64px; height: 64px; margin: 0 auto 16px;
            background: rgba(255,255,255,0.2); border-radius: 50%;
            display: flex; align-items: center; justify-content: center;
            animation: popIn 0.5s 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275) forwards;
            opacity: 0; transform: scale(0);
            position: relative; z-index: 1;
        }
        @keyframes popIn {
            to { opacity: 1; transform: scale(1); }
        }
        .check-circle svg {
            width: 32px; height: 32px;
            stroke: #fff; stroke-width: 3; fill: none;
            stroke-dasharray: 50; stroke-dashoffset: 50;
            animation: drawCheck 0.5s 0.6s ease forwards;
        }
        @keyframes drawCheck {
            to { stroke-dashoffset: 0; }
        }

        .card-header h2 {
            font-size: 20px; font-weight: 800; color: #fff;
            margin: 0; position: relative; z-index: 1;
            animation: fadeUp 0.5s 0.4s ease forwards;
            opacity: 0; transform: translateY(10px);
        }
        .card-header .sub {
            font-size: 14px; color: rgba(255,255,255,0.8);
            margin-top: 6px; position: relative; z-index: 1;
            animation: fadeUp 0.5s 0.5s ease forwards;
            opacity: 0; transform: translateY(10px);
        }
        @keyframes fadeUp {
            to { opacity: 1; transform: translateY(0); }
        }

        /* 바디 */
        .card-body {
            padding: 24px;
            animation: fadeUp 0.5s 0.6s ease forwards;
            opacity: 0; transform: translateY(10px);
        }

        /* 정보 행 */
        .info-row {
            display: flex; justify-content: space-between; align-items: center;
            padding: 12px 0;
            border-bottom: 1px solid #f2f4f6;
        }
        .info-row:last-child { border-bottom: none; }
        .info-label { font-size: 13px; color: #8b95a1; }
        .info-value { font-size: 14px; font-weight: 700; color: #191f28; }
        .info-value.amount { color: #ff6e75; font-size: 16px; }
        .info-value.charge { color: #3182f6; }

        /* 계좌 카드 */
        .account-card {
            margin-top: 20px; padding: 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border-radius: 16px; color: #fff;
            animation: fadeUp 0.5s 0.7s ease forwards;
            opacity: 0; transform: translateY(10px);
            position: relative; overflow: hidden;
        }
        .account-card::before {
            content: ''; position: absolute; top: -30%; right: -20%;
            width: 200px; height: 200px;
            background: radial-gradient(circle, rgba(255,255,255,0.12) 0%, transparent 70%);
            border-radius: 50%;
        }
        .account-card .label { font-size: 11px; color: rgba(255,255,255,0.6); margin-bottom: 4px; position: relative; z-index: 1; }
        .account-row {
            display: flex; justify-content: space-between; align-items: center;
            padding: 4px 0; position: relative; z-index: 1;
        }
        .account-row .val { font-size: 14px; font-weight: 600; }
        .account-number {
            font-size: 24px; font-weight: 800; letter-spacing: 2px;
            text-align: center; padding: 12px 0 8px;
            position: relative; z-index: 1;
        }
        .copy-wrap {
            text-align: center; position: relative; z-index: 1;
        }
        .copy-btn {
            display: inline-flex; align-items: center; gap: 6px;
            padding: 10px 24px; border-radius: 50px;
            background: rgba(255,255,255,0.2); backdrop-filter: blur(4px);
            border: 1px solid rgba(255,255,255,0.3);
            color: #fff; font-size: 13px; font-weight: 700;
            cursor: pointer; transition: all 0.25s; font-family: inherit;
        }
        .copy-btn:hover {
            background: rgba(255,255,255,0.35);
            transform: translateY(-1px);
            box-shadow: 0 4px 15px rgba(0,0,0,0.15);
        }
        .copy-btn:active { transform: scale(0.96); }
        .copy-btn.copied {
            background: #34d399; border-color: #34d399;
            box-shadow: 0 4px 15px rgba(52,211,153,0.3);
        }
        .copy-btn svg { width: 16px; height: 16px; fill: currentColor; }

        /* 안내 */
        .notice {
            margin-top: 16px; padding: 14px;
            background: #f8f9fa; border-radius: 10px;
            font-size: 12px; color: #8b95a1; line-height: 1.7; text-align: left;
            animation: fadeUp 0.5s 0.8s ease forwards;
            opacity: 0; transform: translateY(10px);
        }

        /* 버튼 */
        .btn-wrap {
            padding: 0 24px 24px;
            animation: fadeUp 0.5s 0.9s ease forwards;
            opacity: 0; transform: translateY(10px);
        }
        .btn {
            display: block; width: 100%; padding: 16px;
            border-radius: 14px; border: none;
            background: #191f28; color: #fff;
            font-size: 15px; font-weight: 700;
            cursor: pointer; transition: all 0.2s;
            font-family: inherit; text-decoration: none; text-align: center;
        }
        .btn:hover { background: #333d4b; transform: translateY(-1px); box-shadow: 0 4px 12px rgba(0,0,0,0.15); }
        .btn:active { transform: scale(0.98); }

        /* 에러 스타일 */
        .card-header.error {
            background: linear-gradient(135deg, #6c757d 0%, #495057 100%);
        }
        .error-icon {
            width: 64px; height: 64px; margin: 0 auto 16px;
            background: rgba(255,255,255,0.2); border-radius: 50%;
            display: flex; align-items: center; justify-content: center;
            font-size: 32px; color: #fff;
            animation: popIn 0.5s 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275) forwards;
            opacity: 0; transform: scale(0);
            position: relative; z-index: 1;
        }

        /* 반응형 */
        @media (max-width: 480px) {
            body { padding: 12px; }
            .card-header { padding: 24px 20px 22px; }
            .card-body { padding: 20px; }
            .btn-wrap { padding: 0 20px 20px; }
            .account-number { font-size: 18px; }
        }
    </style>
    <script>
    function copyAccount() {
        navigator.clipboard.writeText('90007637104010').then(function() {
            var btn = document.getElementById('copyBtn');
            btn.innerHTML = '<svg viewBox="0 0 24 24" style="width:16px;height:16px;fill:currentColor;"><polyline points="20 6 9 17 4 12" style="fill:none;stroke:currentColor;stroke-width:3;stroke-linecap:round;stroke-linejoin:round;"/></svg> 복사 완료!';
            btn.classList.add('copied');
            setTimeout(function() {
                btn.innerHTML = '<svg viewBox="0 0 24 24" style="width:16px;height:16px;fill:currentColor;"><path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/></svg> 계좌번호 복사';
                btn.classList.remove('copied');
            }, 2000);
        });
    }
    </script>
</head>
<body>
    <div class="card">
        {% if is_success %}
        <div class="card-header">
            <div class="check-circle">
                <svg viewBox="0 0 24 24"><polyline points="6 12 10 16 18 8"/></svg>
            </div>
            <h2>{{ title }}</h2>
            <div class="sub">{{ subtitle }}</div>
        </div>
        <div class="card-body">
            {% if info_rows %}
            <div>
                {% for row in info_rows %}
                <div class="info-row">
                    <span class="info-label">{{ row.label }}</span>
                    <span class="info-value {{ row.cls or '' }}">{{ row.value }}</span>
                </div>
                {% endfor %}
            </div>
            {% endif %}

            {% if show_account %}
            <div class="account-card">
                <div class="label">입금 계좌</div>
                <div class="account-row">
                    <span class="val">기업은행</span>
                    <span class="val">민준기(성장기획)</span>
                </div>
                <div class="account-number">900-076371-04-010</div>
                <div class="copy-wrap">
                    <button type="button" id="copyBtn" class="copy-btn" onclick="copyAccount()">
                        <svg viewBox="0 0 24 24"><path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2zm0 16H8V7h11v14z"/></svg>
                        계좌번호 복사
                    </button>
                </div>
            </div>

            <div class="notice">
                입금 금액과 입금자명이 정확히 일치해야 자동 충전됩니다.<br>
                입금 확인 후 자동으로 잔액이 충전됩니다.<br>
                부분 입금 시 나머지 금액을 추가 송금하면 자동 처리됩니다.
            </div>
            {% endif %}

            {% if error_msg %}
            <div style="margin-top:12px;font-size:14px;color:#4e5968;line-height:1.8;">{{ error_msg }}</div>
            {% endif %}
        </div>
        {% else %}
        <div class="card-header error">
            <div class="error-icon">!</div>
            <h2>{{ title }}</h2>
            <div class="sub">{{ subtitle }}</div>
        </div>
        <div class="card-body">
            {% if error_msg %}
            <div style="font-size:14px;color:#4e5968;line-height:1.8;">{{ error_msg }}</div>
            {% endif %}
        </div>
        {% endif %}

        <div class="btn-wrap">
            <a href="javascript:window.close();history.back();" class="btn">돌아가기</a>
        </div>
    </div>
</body>
</html>
"""

CHARGE_ERROR_HTML = CHARGE_SUCCESS_HTML


@public_bp.route("/api/charge-request", methods=["POST"])
def charge_request():
    """무통장입금 충전 요청 접수.

    인스타몬스터 addfunds 폼에서 호출:
      - id: 인스타몬스터 username
      - price: 입금 금액 (부가세 포함)
      - name: 입금자명
      - stat2: 세금계산서 타입 (0=없음, 1=세금계산서, 2=현금영수증)
      - d1~d6: 세금계산서 상세
    """
    username = (request.form.get("id") or "").strip()
    amount_str = request.form.get("price") or "0"
    depositor_name = (request.form.get("name") or "").strip()
    tax_type = int(request.form.get("stat2") or "0")

    # 세금계산서 정보
    tax_info = {}
    if tax_type == 1:
        tax_info = {
            "company": request.form.get("d1", ""),
            "biz_no": request.form.get("d2", ""),
            "ceo": request.form.get("d3", ""),
            "contact": request.form.get("d4", ""),
            "email": request.form.get("d5", ""),
        }
    elif tax_type == 2:
        tax_info = {"phone": request.form.get("d6", "")}

    # 유효성 검사
    if not username:
        return _render_error("아이디를 확인해주세요.")
    if not depositor_name:
        return _render_error("입금자명을 입력해주세요.")
    if len(depositor_name) > 20:
        return _render_error("입금자명은 20글자 이내로 입력해주세요.")

    try:
        amount = int(amount_str)
    except (ValueError, TypeError):
        return _render_error("올바르지 않은 금액입니다.")

    if amount < 1000:
        return _render_error("최소 충전 금액은 1,000원입니다.")
    if amount > 10000000:
        return _render_error("최대 충전 금액은 10,000,000원입니다.")

    # 충전 금액 계산 (부가세 10% 제외)
    charge_amount = round(amount / 1.1)

    # 동일 조건 중복 요청 확인 (같은 username + 입금자명 + 금액으로 pending인 건)
    existing = ChargeRequest.query.filter_by(
        username=username,
        depositor_name=depositor_name,
        amount=amount,
        status="pending",
    ).first()
    if existing:
        return _render_success(username, depositor_name, amount, charge_amount)

    # 충전 요청 저장
    req = ChargeRequest(
        username=username,
        depositor_name=depositor_name,
        amount=amount,
        charge_amount=charge_amount,
        status="pending",
        tax_type=tax_type,
        tax_info=json.dumps(tax_info, ensure_ascii=False) if tax_info else "",
    )
    db.session.add(req)
    db.session.commit()

    logger.info(
        "충전 요청 접수: user=%s, 입금자=%s, 금액=%d원 (충전=%d원)",
        username, depositor_name, amount, charge_amount,
    )

    return _render_success(username, depositor_name, amount, charge_amount)


def _render_success(username, depositor_name, amount, charge_amount):
    tax = amount - charge_amount
    return render_template_string(
        CHARGE_SUCCESS_HTML,
        is_success=True,
        title="충전 요청 완료",
        subtitle=f"{username}님의 충전 요청이 접수되었습니다",
        show_account=True,
        info_rows=[
            {"label": "입금자명", "value": depositor_name, "cls": ""},
            {"label": "입금 금액", "value": f"{amount:,}원", "cls": "amount"},
            {"label": "충전 금액", "value": f"{charge_amount:,}원", "cls": "charge"},
            {"label": "부가세", "value": f"{tax:,}원", "cls": ""},
        ],
        error_msg=None,
    )


def _render_error(message):
    return render_template_string(
        CHARGE_ERROR_HTML,
        is_success=False,
        title="요청 실패",
        subtitle="충전 요청을 처리할 수 없습니다",
        show_account=False,
        info_rows=None,
        error_msg=message,
    ), 400
