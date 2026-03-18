"""팝빌 EasyFinBank 계좌조회 서비스 — 입금 내역 폴링, 자동 매칭, 자동 충전."""

import json
import logging
import time
from datetime import datetime, timezone, timedelta

from popbill import EasyFinBankService, PopbillException

import config
from models import db, BankDeposit, ChargeRequest
from services.instamonster_charge import add_payment
from services.popbill_tax import issue_tax_invoice, issue_cash_receipt

logger = logging.getLogger(__name__)

# ── 팝빌 클라이언트 초기화 ──

_bank_service = None


def _get_bank_service():
    """싱글턴 EasyFinBankService 인스턴스 반환."""
    global _bank_service
    if _bank_service is not None:
        return _bank_service

    if not config.POPBILL_LINK_ID or not config.POPBILL_SECRET_KEY:
        logger.warning("팝빌 LinkID/SecretKey가 설정되지 않았습니다.")
        return None

    _bank_service = EasyFinBankService(config.POPBILL_LINK_ID, config.POPBILL_SECRET_KEY)
    _bank_service.IsTest = config.POPBILL_IS_TEST
    _bank_service.IPRestrictOnOff = False
    _bank_service.UseStaticIP = False
    logger.info("팝빌 EasyFinBankService 초기화 완료 (테스트=%s)", config.POPBILL_IS_TEST)
    return _bank_service


# ── 입금 폴링 + 자동 매칭 ──

def poll_deposits():
    """팝빌 계좌조회 API로 오늘의 입금 내역을 조회하고 새 입금을 DB에 저장한다.
    저장 후 대기 중인 충전 요청과 자동 매칭을 시도한다."""
    svc = _get_bank_service()
    if svc is None:
        return

    corp_num = config.POPBILL_CORP_NUM
    bank_code = config.POPBILL_BANK_CODE
    account_number = config.POPBILL_ACCOUNT_NUMBER

    if not corp_num or not bank_code or not account_number:
        logger.warning("팝빌 사업자번호/은행코드/계좌번호가 설정되지 않았습니다.")
        return

    # 오늘 날짜 (KST 기준)
    kst = timezone(timedelta(hours=9))
    today = datetime.now(kst).strftime("%Y%m%d")

    new_deposits = []

    try:
        # 1. 수집 요청
        job_id = svc.requestJob(corp_num, bank_code, account_number, today, today)
        logger.info("팝빌 수집 요청 완료: jobID=%s", job_id)

        # 2. 수집 완료 대기 (최대 30초)
        for _ in range(10):
            state = svc.getJobState(corp_num, job_id)
            if state.jobState == 3:  # 3 = 완료
                break
            time.sleep(3)
        else:
            logger.warning("팝빌 수집 시간 초과: jobID=%s", job_id)
            return

        # 3. 입금(I) 내역 조회
        result = svc.search(
            corp_num, job_id,
            TradeType=["I"],        # I=입금만
            SearchString="",
            Page=1,
            PerPage=100,
            Order="D",              # D=최신순
        )

        if not result or not hasattr(result, "list"):
            logger.info("팝빌 입금 내역 없음")
            return

        new_count = 0
        for tx in result.list:
            ext_id = str(getattr(tx, "tid", "") or getattr(tx, "trSN", "") or getattr(tx, "trserial", ""))
            if not ext_id:
                continue

            # 중복 체크
            exists = BankDeposit.query.filter_by(source="popbill", external_id=ext_id).first()
            if exists:
                continue

            # 거래 시각 파싱 (trdt = YYYYMMDDHHmmss)
            tx_dt_str = getattr(tx, "trdt", "") or ""
            if not tx_dt_str:
                tx_date_str = getattr(tx, "trdate", today)
                tx_dt_str = tx_date_str + "000000"
            try:
                tx_dt = datetime.strptime(tx_dt_str[:14], "%Y%m%d%H%M%S")
                tx_dt = tx_dt.replace(tzinfo=kst)
            except (ValueError, TypeError):
                tx_dt = datetime.now(kst)

            # accIn = 입금액, accOut = 출금액
            amount = int(getattr(tx, "accIn", 0) or getattr(tx, "trAmount", 0) or 0)
            if amount <= 0:
                continue

            depositor = getattr(tx, "remark1", "") or getattr(tx, "trName", "") or ""
            memo = getattr(tx, "remark2", "") or getattr(tx, "memo", "") or ""
            balance = int(getattr(tx, "balance", 0) or getattr(tx, "trBalance", 0) or 0)

            deposit = BankDeposit(
                source="popbill",
                external_id=ext_id,
                depositor_name=depositor,
                amount=amount,
                bank_name=bank_code,
                account_number=account_number,
                memo=memo,
                balance_after=balance,
                transaction_at=tx_dt,
                status="new",
            )
            db.session.add(deposit)
            new_count += 1
            new_deposits.append(deposit)

        if new_count > 0:
            db.session.commit()
            logger.info("팝빌 새 입금 %d건 저장", new_count)
        else:
            logger.info("팝빌 새 입금 없음 (기존 %d건)", len(result.list) if result.list else 0)

    except PopbillException as e:
        logger.error("팝빌 API 오류: [%s] %s", e.code, e.message)
        return
    except Exception:
        logger.exception("팝빌 입금 폴링 중 오류")
        return

    # 4. 매 폴링마다 모든 pending 요청에 대해 매칭 시도
    _auto_match_all()


def _auto_match_all():
    """매 폴링마다 모든 pending 충전 요청에 대해 매칭을 시도한다.

    매칭 로직:
    1. pending 충전 요청의 입금자명과 일치하는 new 입금을 찾는다
    2. 입금 합산이 요청 금액 이상이면 충전 실행
    3. 부족하면 대기
    """
    pending_requests = ChargeRequest.query.filter_by(status="pending").all()
    if not pending_requests:
        return

    for req in pending_requests:
        # 이 요청의 입금자명과 일치하는 new 입금 합산
        matching_deposits = BankDeposit.query.filter(
            BankDeposit.depositor_name == req.depositor_name.strip(),
            BankDeposit.status == "new",
        ).all()

        if not matching_deposits:
            continue

        total_deposited = sum(d.amount for d in matching_deposits)

        if total_deposited < req.amount:
            continue

        # 금액 충족 → 충전 실행
        logger.info(
            "자동 매칭 완료: 입금자=%s, 누적=%d원, 요청=%d원, user=%s",
            req.depositor_name, total_deposited, req.amount, req.username,
        )

        result = add_payment(
            username=req.username,
            amount=req.charge_amount,
            memo=f"무통장입금 - {req.amount:,}원(부가세 제외 {req.charge_amount:,}원 충전)",
        )

        if result.get("ok"):
            for d in matching_deposits:
                d.status = "matched"
                d.matched_order_id = str(req.id)

            req.status = "charged"
            req.matched_deposit_id = matching_deposits[0].id
            req.payment_id = result.get("payment_id")
            req.charged_at = datetime.now(timezone.utc)
            db.session.commit()

            logger.info(
                "자동 충전 완료: user=%s, %d원 충전 (payment_id=%s, 입금 %d건)",
                req.username, req.charge_amount,
                result.get("payment_id"), len(matching_deposits),
            )

            _auto_issue_receipt(req)
        else:
            req.status = "failed"
            req.error_message = result.get("error", "알 수 없는 오류")
            db.session.commit()

            logger.error(
                "자동 충전 실패: user=%s, error=%s",
                req.username, result.get("error"),
            )


def _auto_issue_receipt(charge_request):
    """충전 완료 후 세금계산서 또는 현금영수증 자동발행."""
    if charge_request.tax_type == 1:
        result = issue_tax_invoice(charge_request)
        if result.get("ok"):
            charge_request.tax_issued = True
            charge_request.tax_mgt_key = result.get("mgt_key", "")
            charge_request.tax_error = ""
            db.session.commit()
            logger.info("세금계산서 자동발행 완료: user=%s, mgtKey=%s",
                        charge_request.username, result.get("mgt_key"))
        else:
            charge_request.tax_error = result.get("error", "알 수 없는 오류")
            db.session.commit()
            logger.error("세금계산서 자동발행 실패: user=%s, error=%s",
                         charge_request.username, result.get("error"))

    elif charge_request.tax_type == 2:
        result = issue_cash_receipt(charge_request)
        if result.get("ok"):
            charge_request.tax_issued = True
            charge_request.tax_mgt_key = result.get("mgt_key", "")
            charge_request.tax_error = ""
            db.session.commit()
            logger.info("현금영수증 자동발행 완료: user=%s, mgtKey=%s",
                        charge_request.username, result.get("mgt_key"))
        else:
            charge_request.tax_error = result.get("error", "알 수 없는 오류")
            db.session.commit()
            logger.error("현금영수증 자동발행 실패: user=%s, error=%s",
                         charge_request.username, result.get("error"))


# ── 만료 처리 ──

def expire_old_requests(hours=24):
    """24시간 이상 된 대기 충전 요청을 만료 처리한다."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    expired = ChargeRequest.query.filter(
        ChargeRequest.status == "pending",
        ChargeRequest.created_at < cutoff,
    ).all()

    for req in expired:
        req.status = "expired"

    if expired:
        db.session.commit()
        logger.info("만료 처리: %d건", len(expired))


# ── 계좌 등록 상태 확인 ──

def get_bank_account_info():
    """등록된 계좌 정보를 반환한다."""
    svc = _get_bank_service()
    if svc is None:
        return None

    try:
        accounts = svc.getBankAccountInfo(
            config.POPBILL_CORP_NUM,
            config.POPBILL_BANK_CODE,
            config.POPBILL_ACCOUNT_NUMBER,
        )
        return accounts
    except PopbillException as e:
        logger.error("팝빌 계좌 조회 오류: [%s] %s", e.code, e.message)
        return None
    except Exception:
        logger.exception("팝빌 계좌 조회 중 오류")
        return None
