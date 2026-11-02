"""Finance API. Auth belongs to the core: build_router(session) takes its FastAPI dependency, which returns
{'hotel_id': ..., 'user_id': ...} for the signed-in user. Every route acts on that hotel and user only."""

from datetime import date
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from booking_system.finance import seed, service


class Department(BaseModel):
    department_name: str
    department_type: str
    confirm_new: bool = False


class TransactionCode(BaseModel):
    transaction_code: str
    transaction_type: str
    department_name: str
    is_taxable: bool
    payment_method: Optional[str] = None
    gl_export_code: Optional[str] = None
    tax_codes: List[str] = []
    status: str = "active"
    confirm_new: bool = False


class ExportCode(BaseModel):
    gl_export_code: Optional[str]


class TaxCode(BaseModel):
    tax_code: str
    tax_basis: str
    tax_transaction_code: str
    unit: Optional[str] = None
    status: str = "active"
    confirm_new: bool = False


class TaxRule(BaseModel):
    effective_from: date
    rate_percent: Optional[str] = None
    amount_per_unit: Optional[str] = None
    currency: Optional[str] = None


class TaxQuote(BaseModel):
    transaction_code: str
    amount: str
    units: Optional[Dict[str, str]] = None


class Day(BaseModel):
    business_date: date


class ShiftOpen(BaseModel):
    opening_float: str


class ShiftClose(BaseModel):
    counted_cash: str
    variance_reason: Optional[str] = None


class Posting(BaseModel):
    folio_type: str
    folio_ref: str
    transaction_code: str
    amount: str
    source_module: str
    source_ref: str
    quantity: str = "1"
    booking_id: Optional[str] = None
    outlet_id: Optional[str] = None
    ar_account_id: Optional[str] = None
    cashier_shift_id: Optional[str] = None
    units: Optional[Dict[str, str]] = None
    reference: Optional[str] = None
    reason: Optional[str] = None


class Transfer(BaseModel):
    folio_type: str
    folio_ref: str
    ar_account_id: str
    transaction_code: str
    amount: str
    source_module: str
    source_ref: str
    booking_id: Optional[str] = None
    reference: Optional[str] = None


class Reversal(BaseModel):
    reason: str
    source_module: str
    source_ref: str
    cashier_shift_id: Optional[str] = None


class Account(BaseModel):
    account_name: str
    account_type: str
    credit_limit: str
    payment_terms_days: int
    status: str = "active"
    confirm_new: bool = False


class Invoice(BaseModel):
    posting_ids: Optional[List[str]] = None
    all_uninvoiced: bool = False


class Void(BaseModel):
    reason: str


class AccountPayment(BaseModel):
    transaction_code: str
    amount: str
    source_module: str
    source_ref: str
    ar_invoice_id: Optional[str] = None
    cashier_shift_id: Optional[str] = None
    reference: Optional[str] = None


class Allocation(BaseModel):
    credit_posting_id: str
    amount: str


def _reply(result):
    return result if result.get("ok") else JSONResponse(status_code=409, content=result)


def build_router(session):
    router = APIRouter(prefix="/api/finance", tags=["finance"])
    me = Depends(session)

    @router.post("/setup/seed")
    def seed_template(s=me):
        results = seed.seed(s["hotel_id"])
        failed = [row for row in results if not row.get("ok")]
        return _reply({"ok": not failed, "applied": len(results), "failed": failed})

    @router.get("/departments")
    def departments(s=me):
        return _reply(service.list_departments(s["hotel_id"]))

    @router.post("/departments")
    def department_save(body: Department, s=me):
        return _reply(service.save_department(s["hotel_id"], body.department_name, body.department_type, body.confirm_new))

    @router.delete("/departments/{department_name}")
    def department_delete(department_name: str, s=me):
        return _reply(service.delete_department(s["hotel_id"], department_name))

    @router.get("/transaction_codes")
    def codes(s=me):
        return _reply(service.list_transaction_codes(s["hotel_id"]))

    @router.get("/transaction_codes/{transaction_code}")
    def code_resolve(transaction_code: str, s=me):
        return _reply(service.resolve_transaction_code(s["hotel_id"], transaction_code))

    @router.post("/transaction_codes")
    def code_save(body: TransactionCode, s=me):
        return _reply(service.save_transaction_code(s["hotel_id"], body.transaction_code, body.transaction_type, body.department_name,
                                                    body.is_taxable, body.payment_method, body.gl_export_code, body.tax_codes,
                                                    body.status, body.confirm_new))

    @router.put("/transaction_codes/{transaction_code}/gl_export_code")
    def code_export(transaction_code: str, body: ExportCode, s=me):
        return _reply(service.set_gl_export_code(s["hotel_id"], transaction_code, body.gl_export_code))

    @router.delete("/transaction_codes/{transaction_code}")
    def code_delete(transaction_code: str, s=me):
        return _reply(service.delete_transaction_code(s["hotel_id"], transaction_code))

    @router.get("/tax_codes")
    def taxes(s=me):
        return _reply(service.list_tax_codes(s["hotel_id"]))

    @router.post("/tax_codes")
    def tax_save(body: TaxCode, s=me):
        return _reply(service.save_tax_code(s["hotel_id"], body.tax_code, body.tax_basis, body.tax_transaction_code, body.unit,
                                            body.status, body.confirm_new))

    @router.post("/tax_codes/{tax_code}/rules")
    def tax_rule(tax_code: str, body: TaxRule, s=me):
        return _reply(service.save_tax_rule(s["hotel_id"], tax_code, body.effective_from, body.rate_percent, body.amount_per_unit, body.currency))

    @router.post("/tax_quote")
    def tax_quote(body: TaxQuote, s=me):
        return _reply(service.quote_tax(s["hotel_id"], body.transaction_code, body.amount, body.units))

    @router.get("/business_day")
    def business_day(s=me):
        return _reply(service.read_business_day(s["hotel_id"]))

    @router.post("/business_day/open")
    def business_day_open(body: Day, s=me):
        return _reply(service.open_business_day(s["hotel_id"], body.business_date, s["user_id"]))

    @router.post("/business_day/close")
    def business_day_close(body: Day, s=me):
        return _reply(service.close_business_day(s["hotel_id"], body.business_date, s["user_id"]))

    @router.get("/daily_close/{business_date}")
    def daily_close(business_date: date, s=me):
        return _reply(service.daily_close_report(s["hotel_id"], business_date))

    @router.post("/cashier_shifts")
    def shift_open(body: ShiftOpen, s=me):
        return _reply(service.open_cashier_shift(s["hotel_id"], s["user_id"], body.opening_float))

    @router.get("/cashier_shifts/{cashier_shift_id}")
    def shift_read(cashier_shift_id: str, s=me):
        return _reply(service.read_cashier_shift(s["hotel_id"], cashier_shift_id))

    @router.post("/cashier_shifts/{cashier_shift_id}/close")
    def shift_close(cashier_shift_id: str, body: ShiftClose, s=me):
        return _reply(service.close_cashier_shift(s["hotel_id"], cashier_shift_id, s["user_id"], body.counted_cash, body.variance_reason))

    @router.post("/postings/charge")
    def charge(body: Posting, s=me):
        return _reply(service.post_charge(s["hotel_id"], body.folio_type, body.folio_ref, body.transaction_code, body.amount, body.source_module,
                                          body.source_ref, user_id=s["user_id"], quantity=body.quantity, booking_id=body.booking_id,
                                          outlet_id=body.outlet_id, ar_account_id=body.ar_account_id, units=body.units, reference=body.reference))

    @router.post("/postings/adjustment")
    def adjustment(body: Posting, s=me):
        return _reply(service.post_adjustment(s["hotel_id"], body.folio_type, body.folio_ref, body.transaction_code, body.amount, body.reason,
                                              body.source_module, body.source_ref, user_id=s["user_id"], quantity=body.quantity,
                                              booking_id=body.booking_id, outlet_id=body.outlet_id, ar_account_id=body.ar_account_id,
                                              units=body.units, reference=body.reference))

    @router.post("/postings/payment")
    def payment(body: Posting, s=me):
        return _reply(service.post_payment(s["hotel_id"], body.folio_type, body.folio_ref, body.transaction_code, body.amount, body.source_module,
                                           body.source_ref, user_id=s["user_id"], cashier_shift_id=body.cashier_shift_id,
                                           booking_id=body.booking_id, outlet_id=body.outlet_id, ar_account_id=body.ar_account_id,
                                           reference=body.reference))

    @router.post("/postings/transfer")
    def transfer(body: Transfer, s=me):
        return _reply(service.transfer_to_account(s["hotel_id"], body.folio_type, body.folio_ref, body.ar_account_id, body.transaction_code,
                                                  body.amount, body.source_module, body.source_ref, user_id=s["user_id"],
                                                  booking_id=body.booking_id, reference=body.reference))

    @router.post("/postings/{posting_id}/reverse")
    def reverse(posting_id: str, body: Reversal, s=me):
        return _reply(service.reverse_posting(s["hotel_id"], posting_id, body.reason, body.source_module, body.source_ref,
                                              user_id=s["user_id"], cashier_shift_id=body.cashier_shift_id))

    @router.get("/folios/{folio_type}/{folio_ref}")
    def folio(folio_type: str, folio_ref: str, s=me):
        return _reply(service.read_folio(s["hotel_id"], folio_type, folio_ref))

    @router.get("/ar_accounts")
    def accounts(s=me):
        return _reply(service.list_ar_accounts(s["hotel_id"]))

    @router.post("/ar_accounts")
    def account_save(body: Account, s=me):
        return _reply(service.save_ar_account(s["hotel_id"], body.account_name, body.account_type, body.credit_limit, body.payment_terms_days,
                                              body.status, body.confirm_new))

    @router.post("/ar_accounts/{ar_account_id}/invoices")
    def invoice_issue(ar_account_id: str, body: Invoice, s=me):
        return _reply(service.issue_invoice(s["hotel_id"], ar_account_id, s["user_id"], body.posting_ids, body.all_uninvoiced))

    @router.post("/ar_accounts/{ar_account_id}/payments")
    def account_payment(ar_account_id: str, body: AccountPayment, s=me):
        return _reply(service.record_account_payment(s["hotel_id"], ar_account_id, body.transaction_code, body.amount, body.source_module,
                                                     body.source_ref, s["user_id"], body.ar_invoice_id, body.cashier_shift_id, body.reference))

    @router.post("/ar_invoices/{ar_invoice_id}/void")
    def invoice_void(ar_invoice_id: str, body: Void, s=me):
        return _reply(service.void_invoice(s["hotel_id"], ar_invoice_id, body.reason, s["user_id"]))

    @router.post("/ar_invoices/{ar_invoice_id}/allocations")
    def invoice_allocate(ar_invoice_id: str, body: Allocation, s=me):
        return _reply(service.allocate_credit(s["hotel_id"], ar_invoice_id, body.credit_posting_id, body.amount, s["user_id"]))

    @router.get("/ar_aging")
    def aging(s=me):
        return _reply(service.ar_aging(s["hotel_id"]))

    @router.get("/gl_export")
    def export(date_from: date, date_to: date, s=me):
        return _reply(service.gl_export(s["hotel_id"], date_from, date_to))

    return router
