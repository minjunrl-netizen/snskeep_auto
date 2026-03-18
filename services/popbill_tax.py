"""팝빌 세금계산서 / 현금영수증 자동발행 서비스."""

import json
import logging
import uuid
from datetime import datetime, timezone, timedelta

from popbill import TaxinvoiceService, CashbillService, PopbillException
from popbill import Taxinvoice, TaxinvoiceDetail, Cashbill

import config

logger = logging.getLogger(__name__)

# ── 팝빌 서비스 초기화 ──

_tax_service = None
_cash_service = None


def _get_tax_service():
    """싱글턴 TaxinvoiceService."""
    global _tax_service
    if _tax_service is not None:
        return _tax_service

    if not config.POPBILL_LINK_ID or not config.POPBILL_SECRET_KEY:
        return None

    _tax_service = TaxinvoiceService(config.POPBILL_LINK_ID, config.POPBILL_SECRET_KEY)
    _tax_service.IsTest = config.POPBILL_IS_TEST
    _tax_service.IPRestrictOnOff = False
    _tax_service.UseStaticIP = False
    logger.info("팝빌 TaxinvoiceService 초기화 완료")
    return _tax_service


def _get_cash_service():
    """싱글턴 CashbillService."""
    global _cash_service
    if _cash_service is not None:
        return _cash_service

    if not config.POPBILL_LINK_ID or not config.POPBILL_SECRET_KEY:
        return None

    _cash_service = CashbillService(config.POPBILL_LINK_ID, config.POPBILL_SECRET_KEY)
    _cash_service.IsTest = config.POPBILL_IS_TEST
    _cash_service.IPRestrictOnOff = False
    _cash_service.UseStaticIP = False
    logger.info("팝빌 CashbillService 초기화 완료")
    return _cash_service


def _make_mgt_key():
    """고유 문서번호 생성 (최대 24자)."""
    now = datetime.now(timezone(timedelta(hours=9)))
    return now.strftime("%Y%m%d%H%M%S") + uuid.uuid4().hex[:10]


# ── 세금계산서 즉시발행 ──

def issue_tax_invoice(charge_request):
    """충전 요청에 대한 세금계산서를 즉시발행한다.

    Args:
        charge_request: ChargeRequest 객체 (tax_type=1, tax_info에 사업자 정보)

    Returns:
        dict: {"ok": True, "mgt_key": ...} 또는 {"ok": False, "error": ...}
    """
    svc = _get_tax_service()
    if svc is None:
        return {"ok": False, "error": "팝빌 서비스 미설정"}

    corp_num = config.POPBILL_CORP_NUM
    if not corp_num:
        return {"ok": False, "error": "사업자번호 미설정"}

    try:
        tax_info = json.loads(charge_request.tax_info) if charge_request.tax_info else {}
    except (json.JSONDecodeError, TypeError):
        tax_info = {}

    buyer_corp_num = tax_info.get("biz_no", "")
    buyer_corp_name = tax_info.get("company", "")
    buyer_ceo = tax_info.get("ceo", "")
    buyer_email = tax_info.get("email", "")

    if not buyer_corp_num or not buyer_corp_name:
        return {"ok": False, "error": "세금계산서 발행에 필요한 사업자 정보 부족"}

    amount = charge_request.amount  # 부가세 포함 총액
    supply_cost = charge_request.charge_amount  # 공급가액 (부가세 제외)
    tax = amount - supply_cost  # 세액

    kst = timezone(timedelta(hours=9))
    write_date = datetime.now(kst).strftime("%Y%m%d")
    mgt_key = _make_mgt_key()

    try:
        taxinvoice = Taxinvoice(
            writeDate=write_date,
            chargeDirection="정과금",
            issueType="정발행",
            purposeType="영수",
            taxType="과세",

            # 공급자 (우리)
            invoicerCorpNum=corp_num,
            invoicerCorpName="성장기획",
            invoicerMgtKey=mgt_key,
            invoicerCEOName="민준기",

            # 공급받는자 (고객)
            invoiceeType="사업자",
            invoiceeCorpNum=buyer_corp_num,
            invoiceeCorpName=buyer_corp_name,
            invoiceeCEOName=buyer_ceo,
            invoiceeEmail1=buyer_email,

            # 금액
            supplyCostTotal=str(supply_cost),
            taxTotal=str(tax),
            totalAmount=str(amount),
        )

        # 품목
        taxinvoice.detailList = [
            TaxinvoiceDetail(
                serialNum=1,
                purchaseDT=write_date,
                itemName="SNS 마케팅 서비스 포인트 충전",
                unitCost=str(supply_cost),
                qty="1",
                supplyCost=str(supply_cost),
                tax=str(tax),
            )
        ]

        response = svc.registIssue(
            corp_num,
            taxinvoice,
            memo=f"자동발행 - {charge_request.username}",
        )

        logger.info(
            "세금계산서 발행 완료: mgtKey=%s, user=%s, amount=%d",
            mgt_key, charge_request.username, amount,
        )
        return {"ok": True, "mgt_key": mgt_key, "nts_confirm_num": getattr(response, "ntsConfirmNum", "")}

    except PopbillException as e:
        logger.error("세금계산서 발행 실패: [%s] %s", e.code, e.message)
        return {"ok": False, "error": f"[{e.code}] {e.message}"}
    except Exception:
        logger.exception("세금계산서 발행 중 오류")
        return {"ok": False, "error": "세금계산서 발행 중 오류 발생"}


# ── 현금영수증 즉시발행 ──

def issue_cash_receipt(charge_request):
    """충전 요청에 대한 현금영수증을 즉시발행한다.

    Args:
        charge_request: ChargeRequest 객체 (tax_type=2, tax_info에 휴대번호)

    Returns:
        dict: {"ok": True, "mgt_key": ...} 또는 {"ok": False, "error": ...}
    """
    svc = _get_cash_service()
    if svc is None:
        return {"ok": False, "error": "팝빌 서비스 미설정"}

    corp_num = config.POPBILL_CORP_NUM
    if not corp_num:
        return {"ok": False, "error": "사업자번호 미설정"}

    try:
        tax_info = json.loads(charge_request.tax_info) if charge_request.tax_info else {}
    except (json.JSONDecodeError, TypeError):
        tax_info = {}

    phone = tax_info.get("phone", "")
    if not phone:
        return {"ok": False, "error": "현금영수증 발행에 필요한 휴대번호 없음"}

    # 휴대번호에서 하이픈 제거
    identity_num = phone.replace("-", "").strip()

    amount = charge_request.amount
    supply_cost = charge_request.charge_amount
    tax = amount - supply_cost

    kst = timezone(timedelta(hours=9))
    mgt_key = _make_mgt_key()

    try:
        cashbill = Cashbill(
            mgtKey=mgt_key,
            tradeType="승인거래",
            tradeUsage="소득공제용",
            taxationType="과세",
            totalAmount=str(amount),
            supplyCost=str(supply_cost),
            tax=str(tax),
            serviceFee="0",
            franchiseCorpNum=corp_num,
            franchiseCorpName="성장기획",
            franchiseCEOName="민준기",
            identityNum=identity_num,
            customerName=charge_request.depositor_name,
            itemName="SNS 마케팅 서비스 포인트 충전",
            orderNumber=str(charge_request.id),
        )

        response = svc.registIssue(
            corp_num,
            cashbill,
            f"자동발행 - {charge_request.username}",
        )

        logger.info(
            "현금영수증 발행 완료: mgtKey=%s, user=%s, amount=%d",
            mgt_key, charge_request.username, amount,
        )
        return {"ok": True, "mgt_key": mgt_key, "confirm_num": getattr(response, "confirmNum", "")}

    except PopbillException as e:
        logger.error("현금영수증 발행 실패: [%s] %s", e.code, e.message)
        return {"ok": False, "error": f"[{e.code}] {e.message}"}
    except Exception:
        logger.exception("현금영수증 발행 중 오류")
        return {"ok": False, "error": "현금영수증 발행 중 오류 발생"}


# ── 세금계산서 발행취소 ──

def cancel_tax_invoice(mgt_key):
    """세금계산서 발행취소 (국세청 전송 이전만 가능)."""
    svc = _get_tax_service()
    if svc is None:
        return {"ok": False, "error": "팝빌 서비스 미설정"}

    corp_num = config.POPBILL_CORP_NUM
    if not corp_num:
        return {"ok": False, "error": "사업자번호 미설정"}

    try:
        svc.cancelIssue(corp_num, "SELL", mgt_key, "관리자 취소")
        logger.info("세금계산서 발행취소 완료: mgtKey=%s", mgt_key)
        return {"ok": True}
    except PopbillException as e:
        logger.error("세금계산서 발행취소 실패: [%s] %s", e.code, e.message)
        return {"ok": False, "error": f"[{e.code}] {e.message}"}
    except Exception:
        logger.exception("세금계산서 발행취소 중 오류")
        return {"ok": False, "error": "세금계산서 발행취소 중 오류 발생"}


# ── 현금영수증 취소발행 ──

def cancel_cash_receipt(charge_request):
    """현금영수증 취소거래 발행."""
    svc = _get_cash_service()
    if svc is None:
        return {"ok": False, "error": "팝빌 서비스 미설정"}

    corp_num = config.POPBILL_CORP_NUM
    if not corp_num:
        return {"ok": False, "error": "사업자번호 미설정"}

    org_mgt_key = charge_request.tax_mgt_key
    if not org_mgt_key:
        return {"ok": False, "error": "원본 문서번호 없음"}

    # 원본 현금영수증의 국세청 승인번호와 거래일자 조회
    try:
        info = svc.getInfo(corp_num, org_mgt_key)
        org_confirm_num = getattr(info, "confirmNum", "")
        org_trade_date = getattr(info, "tradeDate", "")
        if not org_confirm_num or not org_trade_date:
            return {"ok": False, "error": "원본 승인번호/거래일자 조회 실패"}
    except PopbillException as e:
        return {"ok": False, "error": f"원본 조회 실패: [{e.code}] {e.message}"}

    cancel_mgt_key = _make_mgt_key()

    try:
        svc.revokeRegistIssue(
            corp_num,
            cancel_mgt_key,
            org_confirm_num,
            org_trade_date,
            memo="관리자 취소",
        )
        logger.info("현금영수증 취소 완료: cancelMgtKey=%s, orgMgtKey=%s", cancel_mgt_key, org_mgt_key)
        return {"ok": True, "cancel_mgt_key": cancel_mgt_key}
    except PopbillException as e:
        logger.error("현금영수증 취소 실패: [%s] %s", e.code, e.message)
        return {"ok": False, "error": f"[{e.code}] {e.message}"}
    except Exception:
        logger.exception("현금영수증 취소 중 오류")
        return {"ok": False, "error": "현금영수증 취소 중 오류 발생"}
