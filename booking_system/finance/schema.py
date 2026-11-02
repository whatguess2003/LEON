"""Finance tables, triggers and views. Idempotent: each object is read from the catalog first and created only when missing;
every DDL waits at most LOCK_TIMEOUT. Ids are prefix + uuid4; codes and names are unique attributes, never keys.
Contacts of city-ledger accounts are 'ar_account_profile' documents keyed by ar_account_id."""

import psycopg2

from booking_system import schema_catalog
from booking_system.finance.db import transaction

TABLES = (
    ("transaction_code", """
        transaction_code_id VARCHAR(48) PRIMARY KEY CHECK (left(transaction_code_id, 4) = 'txc_'),
        hotel_id            VARCHAR(48) NOT NULL REFERENCES hotel (hotel_id),
        transaction_code    VARCHAR(32) NOT NULL CHECK (transaction_code = UPPER(btrim(transaction_code)) AND transaction_code <> ''),
        transaction_type    VARCHAR(16) NOT NULL CHECK (transaction_type IN ('charge','tax','payment','refund','adjustment','transfer')),
        department_id       VARCHAR(48) NOT NULL REFERENCES department (department_id),
        payment_method      VARCHAR(16) CHECK (payment_method IN ('cash','card','bank_transfer','channel_collect','voucher','other')),
        is_taxable          BOOLEAN     NOT NULL,
        gl_export_code      VARCHAR(32) CHECK (gl_export_code = btrim(gl_export_code) AND gl_export_code <> ''),
        status              VARCHAR(16) NOT NULL CHECK (status IN ('active','inactive')),
        UNIQUE (hotel_id, transaction_code),
        UNIQUE (transaction_code_id, transaction_type),
        CHECK ((transaction_type IN ('payment','refund')) = (payment_method IS NOT NULL)),
        CHECK (NOT is_taxable OR transaction_type IN ('charge','adjustment'))"""),
    ("tax_code", """
        tax_code_id             VARCHAR(48) PRIMARY KEY CHECK (left(tax_code_id, 4) = 'tax_'),
        hotel_id                VARCHAR(48) NOT NULL REFERENCES hotel (hotel_id),
        tax_code                VARCHAR(32) NOT NULL CHECK (tax_code = UPPER(btrim(tax_code)) AND tax_code <> ''),
        tax_basis               VARCHAR(8)  NOT NULL CHECK (tax_basis IN ('percent','per_unit')),
        unit                    VARCHAR(16) CHECK (unit IN ('night','person_night','item')),
        tax_transaction_code_id VARCHAR(48) NOT NULL,
        tax_transaction_type    VARCHAR(16) NOT NULL DEFAULT 'tax' CHECK (tax_transaction_type = 'tax'),
        status                  VARCHAR(16) NOT NULL CHECK (status IN ('active','inactive')),
        UNIQUE (hotel_id, tax_code),
        UNIQUE (tax_code_id, tax_basis),
        FOREIGN KEY (tax_transaction_code_id, tax_transaction_type) REFERENCES transaction_code (transaction_code_id, transaction_type),
        CHECK ((tax_basis = 'per_unit') = (unit IS NOT NULL))"""),
    ("tax_rule", """
        tax_rule_id     VARCHAR(48)   PRIMARY KEY CHECK (left(tax_rule_id, 4) = 'txr_'),
        tax_code_id     VARCHAR(48)   NOT NULL,
        tax_basis       VARCHAR(8)    NOT NULL,
        effective_from  DATE          NOT NULL,
        rate_percent    NUMERIC(7,4)  CHECK (rate_percent >= 0),
        amount_per_unit NUMERIC(12,2) CHECK (amount_per_unit >= 0),
        currency        CHAR(3),
        created_at      TIMESTAMPTZ   NOT NULL DEFAULT now(),
        UNIQUE (tax_code_id, effective_from),
        FOREIGN KEY (tax_code_id, tax_basis) REFERENCES tax_code (tax_code_id, tax_basis) ON UPDATE CASCADE,
        CHECK ((tax_basis = 'percent') = (rate_percent IS NOT NULL)),
        CHECK ((tax_basis = 'per_unit') = (amount_per_unit IS NOT NULL AND currency IS NOT NULL))"""),
    ("transaction_code_tax", """
        transaction_code_tax_id VARCHAR(48) PRIMARY KEY CHECK (left(transaction_code_tax_id, 4) = 'tct_'),
        transaction_code_id     VARCHAR(48) NOT NULL REFERENCES transaction_code (transaction_code_id) ON DELETE CASCADE,
        tax_code_id             VARCHAR(48) NOT NULL REFERENCES tax_code (tax_code_id),
        UNIQUE (transaction_code_id, tax_code_id)"""),
    ("business_day", """
        business_day_id   VARCHAR(48) PRIMARY KEY CHECK (left(business_day_id, 4) = 'bdy_'),
        hotel_id          VARCHAR(48) NOT NULL REFERENCES hotel (hotel_id),
        business_date     DATE        NOT NULL,
        status            VARCHAR(8)  NOT NULL CHECK (status IN ('open','closed')),
        opened_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        opened_by_user_id VARCHAR(48) NOT NULL CHECK (left(opened_by_user_id, 4) = 'usr_'),
        closed_at         TIMESTAMPTZ,
        closed_by_user_id VARCHAR(48) CHECK (left(closed_by_user_id, 4) = 'usr_'),
        UNIQUE (hotel_id, business_date),
        UNIQUE (business_day_id, business_date),
        CHECK ((status = 'closed') = (closed_at IS NOT NULL AND closed_by_user_id IS NOT NULL))"""),
    ("cashier_shift", """
        cashier_shift_id VARCHAR(48)   PRIMARY KEY CHECK (left(cashier_shift_id, 4) = 'csh_'),
        hotel_id         VARCHAR(48)   NOT NULL REFERENCES hotel (hotel_id),
        business_day_id  VARCHAR(48)   NOT NULL REFERENCES business_day (business_day_id),
        cashier_user_id  VARCHAR(48)   NOT NULL CHECK (left(cashier_user_id, 4) = 'usr_'),
        currency         CHAR(3)       NOT NULL,
        opening_float    NUMERIC(12,2) NOT NULL CHECK (opening_float >= 0),
        status           VARCHAR(8)    NOT NULL CHECK (status IN ('open','closed')),
        opened_at        TIMESTAMPTZ   NOT NULL DEFAULT now(),
        closed_at        TIMESTAMPTZ,
        expected_cash    NUMERIC(12,2),
        counted_cash     NUMERIC(12,2) CHECK (counted_cash >= 0),
        variance         NUMERIC(12,2) GENERATED ALWAYS AS (counted_cash - expected_cash) STORED,
        variance_reason  TEXT          CHECK (variance_reason IS NULL OR btrim(variance_reason) <> ''),
        CHECK ((status = 'closed') = (closed_at IS NOT NULL AND expected_cash IS NOT NULL AND counted_cash IS NOT NULL))"""),
    ("ar_account", """
        ar_account_id      VARCHAR(48)   PRIMARY KEY CHECK (left(ar_account_id, 4) = 'ara_'),
        hotel_id           VARCHAR(48)   NOT NULL REFERENCES hotel (hotel_id),
        account_name       VARCHAR(128)  NOT NULL CHECK (account_name = UPPER(btrim(account_name)) AND account_name <> ''),
        account_type       VARCHAR(16)   NOT NULL CHECK (account_type IN ('company','travel_agent','channel','government','other')),
        currency           CHAR(3)       NOT NULL,
        credit_limit       NUMERIC(12,2) NOT NULL CHECK (credit_limit >= 0),
        payment_terms_days SMALLINT      NOT NULL CHECK (payment_terms_days >= 0),
        status             VARCHAR(16)   NOT NULL CHECK (status IN ('active','suspended','closed')),
        created_at         TIMESTAMPTZ   NOT NULL DEFAULT now(),
        UNIQUE (hotel_id, account_name)"""),
    ("finance_posting", """
        posting_id          VARCHAR(48)   PRIMARY KEY CHECK (left(posting_id, 4) = 'pst_'),
        hotel_id            VARCHAR(48)   NOT NULL REFERENCES hotel (hotel_id),
        business_day_id     VARCHAR(48)   NOT NULL,
        business_date       DATE          NOT NULL,
        folio_type          VARCHAR(8)    NOT NULL CHECK (folio_type IN ('guest','account','outlet')),
        folio_ref           VARCHAR(64)   NOT NULL CHECK (btrim(folio_ref) <> ''),
        booking_id          VARCHAR(48)   CHECK (left(booking_id, 4) = 'bkg_'),
        ar_account_id       VARCHAR(48)   REFERENCES ar_account (ar_account_id),
        outlet_id           VARCHAR(48),
        transaction_code_id VARCHAR(48)   NOT NULL,
        transaction_type    VARCHAR(16)   NOT NULL,
        quantity            NUMERIC(10,2) NOT NULL CHECK (quantity > 0),
        amount              NUMERIC(12,2) NOT NULL CHECK (amount <> 0),
        currency            CHAR(3)       NOT NULL,
        tax_code_id         VARCHAR(48)   REFERENCES tax_code (tax_code_id),
        tax_rule_id         VARCHAR(48)   REFERENCES tax_rule (tax_rule_id),
        parent_posting_id   VARCHAR(48)   REFERENCES finance_posting (posting_id),
        reverses_posting_id VARCHAR(48)   UNIQUE REFERENCES finance_posting (posting_id),
        transfer_posting_id VARCHAR(48)   UNIQUE REFERENCES finance_posting (posting_id) DEFERRABLE INITIALLY DEFERRED,
        cashier_shift_id    VARCHAR(48)   REFERENCES cashier_shift (cashier_shift_id),
        reason              VARCHAR(255),
        reference           VARCHAR(128),
        source_module       VARCHAR(32)   NOT NULL CHECK (source_module = lower(btrim(source_module)) AND source_module <> ''),
        source_ref          VARCHAR(128)  NOT NULL CHECK (btrim(source_ref) <> ''),
        posted_at           TIMESTAMPTZ   NOT NULL DEFAULT now(),
        posted_by_user_id   VARCHAR(48)   CHECK (left(posted_by_user_id, 4) = 'usr_'),
        posted_by_system    VARCHAR(32)   CHECK (posted_by_system IN ('night_audit','channel_aiosell','migration','scheduler')),
        FOREIGN KEY (business_day_id, business_date) REFERENCES business_day (business_day_id, business_date),
        FOREIGN KEY (transaction_code_id, transaction_type) REFERENCES transaction_code (transaction_code_id, transaction_type),
        CHECK ((posted_by_user_id IS NULL) <> (posted_by_system IS NULL)),
        CHECK ((folio_type = 'account') = (ar_account_id IS NOT NULL)),
        CHECK (folio_type <> 'account' OR folio_ref = ar_account_id),
        CHECK ((folio_type = 'outlet') <= (outlet_id IS NOT NULL AND folio_ref = outlet_id)),
        CHECK ((transaction_type = 'tax') = (tax_code_id IS NOT NULL AND tax_rule_id IS NOT NULL AND parent_posting_id IS NOT NULL)),
        CHECK ((transaction_type = 'transfer') = (transfer_posting_id IS NOT NULL)),
        CHECK ((reverses_posting_id IS NOT NULL OR transaction_type = 'adjustment') <= (reason IS NOT NULL)),
        CHECK (reverses_posting_id IS NOT NULL OR CASE transaction_type
               WHEN 'charge' THEN amount > 0 WHEN 'refund' THEN amount > 0
               WHEN 'payment' THEN amount < 0 ELSE true END)"""),
    ("ar_invoice", """
        ar_invoice_id     VARCHAR(48) PRIMARY KEY CHECK (left(ar_invoice_id, 4) = 'inv_'),
        hotel_id          VARCHAR(48) NOT NULL REFERENCES hotel (hotel_id),
        ar_account_id     VARCHAR(48) NOT NULL REFERENCES ar_account (ar_account_id),
        invoice_number    VARCHAR(32) NOT NULL CHECK (invoice_number = UPPER(btrim(invoice_number)) AND invoice_number <> ''),
        invoice_date      DATE        NOT NULL,
        due_date          DATE        NOT NULL CHECK (due_date >= invoice_date),
        currency          CHAR(3)     NOT NULL,
        status            VARCHAR(8)  NOT NULL CHECK (status IN ('open','void')),
        issued_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
        issued_by_user_id VARCHAR(48) NOT NULL CHECK (left(issued_by_user_id, 4) = 'usr_'),
        void_reason       VARCHAR(255),
        voided_by_user_id VARCHAR(48) CHECK (left(voided_by_user_id, 4) = 'usr_'),
        UNIQUE (hotel_id, invoice_number),
        CHECK ((status = 'void') = (void_reason IS NOT NULL AND voided_by_user_id IS NOT NULL))"""),
    ("ar_invoice_item", """
        ar_invoice_item_id VARCHAR(48) PRIMARY KEY CHECK (left(ar_invoice_item_id, 4) = 'aii_'),
        ar_invoice_id      VARCHAR(48) NOT NULL REFERENCES ar_invoice (ar_invoice_id),
        posting_id         VARCHAR(48) NOT NULL UNIQUE REFERENCES finance_posting (posting_id)"""),
    ("ar_allocation", """
        ar_allocation_id     VARCHAR(48)   PRIMARY KEY CHECK (left(ar_allocation_id, 4) = 'ala_'),
        ar_invoice_id        VARCHAR(48)   NOT NULL REFERENCES ar_invoice (ar_invoice_id),
        credit_posting_id    VARCHAR(48)   NOT NULL REFERENCES finance_posting (posting_id),
        amount               NUMERIC(12,2) NOT NULL CHECK (amount > 0),
        allocated_at         TIMESTAMPTZ   NOT NULL DEFAULT now(),
        allocated_by_user_id VARCHAR(48)   NOT NULL CHECK (left(allocated_by_user_id, 4) = 'usr_'),
        UNIQUE (ar_invoice_id, credit_posting_id)"""),
    ("daily_close_summary", """
        daily_close_summary_id VARCHAR(48)   PRIMARY KEY CHECK (left(daily_close_summary_id, 4) = 'dcs_'),
        hotel_id               VARCHAR(48)   NOT NULL REFERENCES hotel (hotel_id),
        business_day_id        VARCHAR(48)   NOT NULL,
        business_date          DATE          NOT NULL,
        transaction_code_id    VARCHAR(48)   NOT NULL REFERENCES transaction_code (transaction_code_id),
        transaction_type       VARCHAR(16)   NOT NULL,
        department_id          VARCHAR(48)   NOT NULL REFERENCES department (department_id),
        payment_method         VARCHAR(16),
        tax_code_id            VARCHAR(48)   REFERENCES tax_code (tax_code_id),
        posting_count          INTEGER       NOT NULL CHECK (posting_count > 0),
        quantity_total         NUMERIC(12,2) NOT NULL,
        amount_total           NUMERIC(14,2) NOT NULL,
        currency               CHAR(3)       NOT NULL,
        FOREIGN KEY (business_day_id, business_date) REFERENCES business_day (business_day_id, business_date),
        UNIQUE NULLS NOT DISTINCT (business_day_id, transaction_code_id, tax_code_id)"""),
)

INDEXES = (
    ("one_open_business_day", "UNIQUE INDEX", "business_day (hotel_id) WHERE status = 'open'"),
    ("one_open_shift_per_cashier", "UNIQUE INDEX", "cashier_shift (hotel_id, cashier_user_id) WHERE status = 'open'"),
    ("finance_posting_source_once", "UNIQUE INDEX", "finance_posting (hotel_id, source_module, source_ref) WHERE parent_posting_id IS NULL"),
    ("finance_posting_folio_idx", "INDEX", "finance_posting (hotel_id, folio_type, folio_ref)"),
    ("finance_posting_day_idx", "INDEX", "finance_posting (business_day_id)"),
    ("finance_posting_booking_idx", "INDEX", "finance_posting (booking_id) WHERE booking_id IS NOT NULL"),
    ("finance_posting_shift_idx", "INDEX", "finance_posting (cashier_shift_id) WHERE cashier_shift_id IS NOT NULL"),
    ("finance_posting_parent_idx", "INDEX", "finance_posting (parent_posting_id) WHERE parent_posting_id IS NOT NULL"),
    ("ar_invoice_account_idx", "INDEX", "ar_invoice (ar_account_id)"),
    ("ar_allocation_credit_idx", "INDEX", "ar_allocation (credit_posting_id)"),
    ("transaction_code_department_idx", "INDEX", "transaction_code (department_id)"),
    ("daily_close_summary_hotel_idx", "INDEX", "daily_close_summary (hotel_id, business_date)"),
)

FUNCTIONS = (
    ("fin_refuse_change", """() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION '% rows are immutable; post a reversal instead', TG_TABLE_NAME;
    END $$"""),
    ("fin_posting_guard", """() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE day_status text; code record; shift record; original record;
    BEGIN
        SELECT status INTO day_status FROM business_day WHERE business_day_id = NEW.business_day_id AND hotel_id = NEW.hotel_id FOR SHARE;
        IF day_status IS DISTINCT FROM 'open' THEN
            RAISE EXCEPTION 'business date % is not open for posting', NEW.business_date;
        END IF;
        IF NEW.currency <> (SELECT currency FROM hotel WHERE hotel_id = NEW.hotel_id) THEN
            RAISE EXCEPTION 'posting currency % is not the hotel currency', NEW.currency;
        END IF;
        SELECT hotel_id, status, payment_method INTO code FROM transaction_code WHERE transaction_code_id = NEW.transaction_code_id;
        IF code.hotel_id <> NEW.hotel_id THEN
            RAISE EXCEPTION 'transaction code belongs to another hotel';
        END IF;
        IF code.status <> 'active' AND NEW.reverses_posting_id IS NULL THEN
            RAISE EXCEPTION 'transaction code is inactive';
        END IF;
        IF NEW.ar_account_id IS NOT NULL AND (SELECT hotel_id FROM ar_account WHERE ar_account_id = NEW.ar_account_id) <> NEW.hotel_id THEN
            RAISE EXCEPTION 'city ledger account belongs to another hotel';
        END IF;
        IF code.payment_method = 'cash' AND NEW.cashier_shift_id IS NULL THEN
            RAISE EXCEPTION 'a cash posting needs the open cashier shift of the poster';
        END IF;
        IF NEW.cashier_shift_id IS NOT NULL THEN
            SELECT * INTO shift FROM cashier_shift WHERE cashier_shift_id = NEW.cashier_shift_id FOR SHARE;
            IF shift.status <> 'open' OR shift.hotel_id <> NEW.hotel_id OR shift.business_day_id <> NEW.business_day_id
               OR shift.cashier_user_id IS DISTINCT FROM NEW.posted_by_user_id THEN
                RAISE EXCEPTION 'cashier shift is not the open shift of the poster on this business date';
            END IF;
        END IF;
        IF NEW.reverses_posting_id IS NOT NULL THEN
            SELECT * INTO original FROM finance_posting WHERE posting_id = NEW.reverses_posting_id;
            IF original.reverses_posting_id IS NOT NULL OR original.hotel_id <> NEW.hotel_id OR original.folio_type <> NEW.folio_type
               OR original.folio_ref <> NEW.folio_ref OR original.transaction_code_id <> NEW.transaction_code_id
               OR original.amount <> -NEW.amount OR original.quantity <> NEW.quantity THEN
                RAISE EXCEPTION 'a reversal must negate exactly one original posting on the same folio and code';
            END IF;
        END IF;
        RETURN NEW;
    END $$"""),
    ("fin_business_day_guard", """() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' OR OLD.status = 'closed' THEN
            RAISE EXCEPTION 'a closed or existing business day cannot be changed or deleted';
        END IF;
        IF NEW.hotel_id <> OLD.hotel_id OR NEW.business_date <> OLD.business_date OR NEW.opened_at <> OLD.opened_at THEN
            RAISE EXCEPTION 'business day identity cannot change';
        END IF;
        RETURN NEW;
    END $$"""),
    ("fin_cashier_shift_guard", """() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' OR OLD.status = 'closed' THEN
            RAISE EXCEPTION 'cashier shifts cannot be deleted, and a closed shift cannot change';
        END IF;
        IF NEW.opening_float <> OLD.opening_float OR NEW.cashier_user_id <> OLD.cashier_user_id OR NEW.business_day_id <> OLD.business_day_id THEN
            RAISE EXCEPTION 'cashier shift opening values cannot change';
        END IF;
        RETURN NEW;
    END $$"""),
    ("fin_invoice_guard", """() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' OR OLD.status = 'void' THEN
            RAISE EXCEPTION 'invoices cannot be deleted, and a void invoice cannot change';
        END IF;
        IF NEW.status <> 'void' OR (NEW.invoice_number, NEW.ar_account_id, NEW.invoice_date, NEW.due_date, NEW.currency)
           IS DISTINCT FROM (OLD.invoice_number, OLD.ar_account_id, OLD.invoice_date, OLD.due_date, OLD.currency) THEN
            RAISE EXCEPTION 'an issued invoice can only be voided';
        END IF;
        IF EXISTS (SELECT 1 FROM ar_allocation WHERE ar_invoice_id = OLD.ar_invoice_id) THEN
            RAISE EXCEPTION 'an invoice with applied payments cannot be voided';
        END IF;
        RETURN NEW;
    END $$"""),
    ("fin_invoice_item_guard", """() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE inv record; p record;
    BEGIN
        IF TG_OP = 'UPDATE' THEN
            RAISE EXCEPTION 'invoice items cannot change';
        END IF;
        SELECT * INTO inv FROM ar_invoice WHERE ar_invoice_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.ar_invoice_id ELSE NEW.ar_invoice_id END;
        IF TG_OP = 'DELETE' THEN
            IF inv.status <> 'void' THEN
                RAISE EXCEPTION 'items leave an invoice only when it is void';
            END IF;
            RETURN OLD;
        END IF;
        SELECT * INTO p FROM finance_posting WHERE posting_id = NEW.posting_id;
        IF inv.status <> 'open' OR p.folio_type <> 'account' OR p.ar_account_id <> inv.ar_account_id
           OR p.transaction_type IN ('payment','refund') THEN
            RAISE EXCEPTION 'only non-payment postings of the invoiced account can be invoiced';
        END IF;
        RETURN NEW;
    END $$"""),
    ("fin_allocation_guard", """() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE inv record; credit record; invoiced numeric; applied numeric; used numeric;
    BEGIN
        IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'allocations are immutable';
        END IF;
        SELECT * INTO inv FROM ar_invoice WHERE ar_invoice_id = NEW.ar_invoice_id FOR UPDATE;
        SELECT * INTO credit FROM finance_posting WHERE posting_id = NEW.credit_posting_id;
        IF inv.status <> 'open' OR credit.folio_type <> 'account' OR credit.ar_account_id <> inv.ar_account_id OR credit.amount >= 0 THEN
            RAISE EXCEPTION 'only a credit on the invoiced account can be applied to an open invoice';
        END IF;
        IF EXISTS (SELECT 1 FROM finance_posting WHERE reverses_posting_id = credit.posting_id) OR credit.reverses_posting_id IS NOT NULL THEN
            RAISE EXCEPTION 'a reversed credit cannot be applied';
        END IF;
        SELECT SUM(p.amount) INTO invoiced FROM ar_invoice_item i JOIN finance_posting p ON p.posting_id = i.posting_id WHERE i.ar_invoice_id = inv.ar_invoice_id;
        SELECT COALESCE(SUM(amount), 0) INTO applied FROM ar_allocation WHERE ar_invoice_id = inv.ar_invoice_id;
        SELECT COALESCE(SUM(amount), 0) INTO used FROM ar_allocation WHERE credit_posting_id = credit.posting_id;
        IF applied + NEW.amount > invoiced OR used + NEW.amount > -credit.amount THEN
            RAISE EXCEPTION 'allocation exceeds the invoice balance or the credit';
        END IF;
        RETURN NEW;
    END $$"""),
    ("fin_tax_link_guard", """() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NOT (SELECT is_taxable FROM transaction_code WHERE transaction_code_id = NEW.transaction_code_id) THEN
            RAISE EXCEPTION 'taxes link only to taxable transaction codes';
        END IF;
        IF (SELECT hotel_id FROM transaction_code WHERE transaction_code_id = NEW.transaction_code_id)
           <> (SELECT hotel_id FROM tax_code WHERE tax_code_id = NEW.tax_code_id) THEN
            RAISE EXCEPTION 'transaction code and tax code belong to different hotels';
        END IF;
        RETURN NEW;
    END $$"""),
)

TRIGGERS = (
    ("finance_posting_guard", "finance_posting", "BEFORE INSERT", "fin_posting_guard"),
    ("finance_posting_immutable", "finance_posting", "BEFORE UPDATE OR DELETE", "fin_refuse_change"),
    ("daily_close_summary_immutable", "daily_close_summary", "BEFORE UPDATE OR DELETE", "fin_refuse_change"),
    ("business_day_guard", "business_day", "BEFORE UPDATE OR DELETE", "fin_business_day_guard"),
    ("cashier_shift_guard", "cashier_shift", "BEFORE UPDATE OR DELETE", "fin_cashier_shift_guard"),
    ("ar_invoice_guard", "ar_invoice", "BEFORE UPDATE OR DELETE", "fin_invoice_guard"),
    ("ar_invoice_item_guard", "ar_invoice_item", "BEFORE INSERT OR UPDATE OR DELETE", "fin_invoice_item_guard"),
    ("ar_allocation_guard", "ar_allocation", "BEFORE INSERT OR UPDATE OR DELETE", "fin_allocation_guard"),
    ("transaction_code_tax_guard", "transaction_code_tax", "BEFORE INSERT OR UPDATE", "fin_tax_link_guard"),
)

VIEWS = (
    ("fin_folio_balance", """
        SELECT hotel_id, folio_type, folio_ref, MAX(booking_id) AS booking_id, ar_account_id, currency,
               SUM(amount) AS balance,
               SUM(amount) FILTER (WHERE transaction_type IN ('charge','tax','adjustment')) AS charges,
               -SUM(amount) FILTER (WHERE transaction_type IN ('payment','refund')) AS payments,
               SUM(amount) FILTER (WHERE transaction_type = 'transfer') AS transfers,
               COUNT(*) AS posting_count
        FROM finance_posting GROUP BY hotel_id, folio_type, folio_ref, ar_account_id, currency"""),
    ("fin_invoice_balance", """
        SELECT i.ar_invoice_id, i.hotel_id, i.ar_account_id, i.invoice_number, i.invoice_date, i.due_date, i.currency, i.status,
               t.invoiced, COALESCE(a.applied, 0) AS applied, t.invoiced - COALESCE(a.applied, 0) AS open_amount
        FROM ar_invoice i
        JOIN (SELECT it.ar_invoice_id, SUM(p.amount) AS invoiced FROM ar_invoice_item it
              JOIN finance_posting p ON p.posting_id = it.posting_id GROUP BY it.ar_invoice_id) t ON t.ar_invoice_id = i.ar_invoice_id
        LEFT JOIN (SELECT ar_invoice_id, SUM(amount) AS applied FROM ar_allocation GROUP BY ar_invoice_id) a ON a.ar_invoice_id = i.ar_invoice_id"""),
    ("fin_ar_aging", """
        SELECT a.hotel_id, a.ar_account_id, a.account_name, a.account_type, a.currency, a.credit_limit, d.business_date AS as_of_business_date,
               COALESCE(SUM(b.open_amount) FILTER (WHERE d.business_date <= b.due_date), 0) AS not_due,
               COALESCE(SUM(b.open_amount) FILTER (WHERE d.business_date - b.due_date BETWEEN 1 AND 30), 0) AS overdue_1_30,
               COALESCE(SUM(b.open_amount) FILTER (WHERE d.business_date - b.due_date BETWEEN 31 AND 60), 0) AS overdue_31_60,
               COALESCE(SUM(b.open_amount) FILTER (WHERE d.business_date - b.due_date BETWEEN 61 AND 90), 0) AS overdue_61_90,
               COALESCE(SUM(b.open_amount) FILTER (WHERE d.business_date - b.due_date > 90), 0) AS overdue_90_plus,
               (SELECT COALESCE(SUM(p.amount), 0) FROM finance_posting p WHERE p.ar_account_id = a.ar_account_id
                  AND p.transaction_type NOT IN ('payment','refund')
                  AND NOT EXISTS (SELECT 1 FROM ar_invoice_item it JOIN ar_invoice i ON i.ar_invoice_id = it.ar_invoice_id
                                  WHERE it.posting_id = p.posting_id AND i.status = 'open')) AS uninvoiced,
               (SELECT COALESCE(SUM(-p.amount), 0) - COALESCE((SELECT SUM(x.amount) FROM ar_allocation x JOIN finance_posting c ON c.posting_id = x.credit_posting_id
                                                               WHERE c.ar_account_id = a.ar_account_id), 0)
                FROM finance_posting p WHERE p.ar_account_id = a.ar_account_id AND p.transaction_type IN ('payment','refund')) AS unapplied_credit,
               (SELECT COALESCE(SUM(p.amount), 0) FROM finance_posting p WHERE p.ar_account_id = a.ar_account_id) AS balance
        FROM ar_account a
        JOIN business_day d ON d.hotel_id = a.hotel_id AND d.status = 'open'
        LEFT JOIN fin_invoice_balance b ON b.ar_account_id = a.ar_account_id AND b.status = 'open' AND b.open_amount <> 0
        GROUP BY a.hotel_id, a.ar_account_id, a.account_name, a.account_type, a.currency, a.credit_limit, d.business_date"""),
    ("fin_cashier_shift_activity", """
        SELECT s.cashier_shift_id, s.hotel_id, s.business_day_id, s.cashier_user_id, s.status, s.currency, t.payment_method, t.transaction_code,
               COUNT(p.posting_id) AS posting_count, COALESCE(-SUM(p.amount), 0) AS net_received
        FROM cashier_shift s
        JOIN finance_posting p ON p.cashier_shift_id = s.cashier_shift_id
        JOIN transaction_code t ON t.transaction_code_id = p.transaction_code_id
        GROUP BY s.cashier_shift_id, s.hotel_id, s.business_day_id, s.cashier_user_id, s.status, s.currency, t.payment_method, t.transaction_code"""),
    ("fin_daily_revenue", """
        SELECT s.hotel_id, s.business_date, dp.department_name, t.transaction_code, s.transaction_type, s.posting_count, s.quantity_total, s.amount_total, s.currency
        FROM daily_close_summary s
        JOIN transaction_code t ON t.transaction_code_id = s.transaction_code_id
        JOIN department dp ON dp.department_id = s.department_id
        WHERE s.transaction_type IN ('charge','adjustment')"""),
    ("fin_daily_payments", """
        SELECT s.hotel_id, s.business_date, s.payment_method, t.transaction_code, s.transaction_type, s.posting_count, -s.amount_total AS net_received, s.currency
        FROM daily_close_summary s JOIN transaction_code t ON t.transaction_code_id = s.transaction_code_id
        WHERE s.transaction_type IN ('payment','refund')"""),
    ("fin_daily_tax", """
        SELECT s.hotel_id, s.business_date, x.tax_code, t.transaction_code, s.posting_count, s.amount_total AS tax_amount, s.currency
        FROM daily_close_summary s
        JOIN tax_code x ON x.tax_code_id = s.tax_code_id
        JOIN transaction_code t ON t.transaction_code_id = s.transaction_code_id
        WHERE s.transaction_type = 'tax'"""),
    ("fin_gl_export", """
        SELECT s.hotel_id, s.business_date, t.transaction_code, t.gl_export_code, dp.department_name, s.transaction_type, x.tax_code,
               s.amount_total, s.currency
        FROM daily_close_summary s
        JOIN transaction_code t ON t.transaction_code_id = s.transaction_code_id
        JOIN department dp ON dp.department_id = s.department_id
        LEFT JOIN tax_code x ON x.tax_code_id = s.tax_code_id"""),
)

# Columns added after a table was first created: (table, column, definition as in TABLES).
COLUMNS = (
    ("cashier_shift", "variance_reason", "TEXT CHECK (variance_reason IS NULL OR btrim(variance_reason) <> '')"),
)

# (constraint, table, columns, target table, target columns). Targets belong to the core (booking_system/core_schema.py) and F&B;
# ensure_links adds each FK once its target exists.
LINKS = (
    ("business_day_opened_by_fk", "business_day", "opened_by_user_id", "user_account", "user_id"),
    ("business_day_closed_by_fk", "business_day", "closed_by_user_id", "user_account", "user_id"),
    ("cashier_shift_cashier_fk", "cashier_shift", "cashier_user_id", "user_account", "user_id"),
    ("finance_posting_posted_by_fk", "finance_posting", "posted_by_user_id", "user_account", "user_id"),
    ("finance_posting_booking_fk", "finance_posting", "hotel_id, booking_id", "booking", "hotel_id, booking_id"),
    ("finance_posting_outlet_fk", "finance_posting", "outlet_id", "outlet", "outlet_id"),
    ("ar_invoice_issued_by_fk", "ar_invoice", "issued_by_user_id", "user_account", "user_id"),
    ("ar_invoice_voided_by_fk", "ar_invoice", "voided_by_user_id", "user_account", "user_id"),
    ("ar_allocation_allocated_by_fk", "ar_allocation", "allocated_by_user_id", "user_account", "user_id"),
)


def _exists(cur, name):
    cur.execute("SELECT to_regclass(%s) IS NOT NULL AS found", (name,))
    return cur.fetchone()["found"]


def _columns(cur, table):
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = %s", (table,))
    return {row["column_name"] for row in cur.fetchall()}


def ensure_schema():
    """Returns the objects created this call. Raises when the core tables hotel and department (core-owned) are missing or reshaped."""
    def run(cur):
        seen = schema_catalog.snapshot(cur)
        if not seen.exists("hotel"):
            raise RuntimeError("core table hotel is missing")
        if not {"department_id", "hotel_id", "department_name", "department_type"} <= seen.columns("department"):
            raise RuntimeError("core table department (department_id, hotel_id, department_name, department_type) is missing")
        created = []
        for name, body in TABLES:
            if not seen.exists(name):
                cur.execute("CREATE TABLE " + name + " (" + body + ")")
                created.append(name)
        for table, column, definition in COLUMNS:
            if seen.exists(table) and column not in seen.columns(table):
                cur.execute("ALTER TABLE " + table + " ADD COLUMN " + column + " " + definition)
                created.append(table + "." + column)
        for name, kind, target in INDEXES:
            if not seen.exists(name):
                cur.execute("CREATE " + kind + " " + name + " ON " + target)
                created.append(name)
        for name, body in FUNCTIONS:
            if name not in seen.functions:
                cur.execute("CREATE FUNCTION " + name + body)
                created.append(name)
        for name, table, timing, function in TRIGGERS:
            if name + " " + table not in seen.triggers:
                cur.execute("CREATE TRIGGER " + name + " " + timing + " ON " + table + " FOR EACH ROW EXECUTE FUNCTION " + function + "()")
                created.append(name)
        for name, body in VIEWS:
            if not seen.exists(name):
                cur.execute("CREATE VIEW " + name + " AS " + body)
                created.append(name)
        return created
    return transaction(run)


def _has_columns(cur, table, columns):
    return {name.strip() for name in columns.split(",")} <= _columns(cur, table)


def ensure_links():
    """Adds each LINKS foreign key whose target table and columns exist (NOT VALID, then VALIDATE).
    Each link is its own transaction. Returns {'added': [...], 'pending': [...], 'invalid': [...]}: pending waits for its target,
    invalid lists links whose existing rows violate the FK (the NOT VALID constraint stays, so new rows are already checked)."""
    report = {"added": [], "pending": [], "invalid": []}
    validated = transaction(lambda cur: {name for name, done in schema_catalog.snapshot(cur).constraints.items() if done})
    for name, table, columns, target, keys in LINKS:
        if name in validated:
            continue

        def run(cur):
            cur.execute("SELECT convalidated FROM pg_constraint WHERE conname = %s", (name,))
            row = cur.fetchone()
            if row and row["convalidated"]:
                return None
            if not _exists(cur, target) or not _has_columns(cur, target, keys):
                return "pending"
            if not row:
                cur.execute("ALTER TABLE " + table + " ADD CONSTRAINT " + name + " FOREIGN KEY (" + columns + ") REFERENCES "
                            + target + " (" + keys + ") NOT VALID")
            return "added"
        try:
            state = transaction(run)
        except psycopg2.errors.LockNotAvailable:
            report.setdefault("busy", []).append(name)
            continue
        if state == "pending":
            report["pending"].append(table + " (" + columns + ") -> " + target + " (" + keys + ")")
        if state == "added":
            try:
                transaction(lambda cur: cur.execute("ALTER TABLE " + table + " VALIDATE CONSTRAINT " + name))
                report["added"].append(name)
            except psycopg2.Error as error:
                report["invalid"].append(name + ": " + (error.diag.message_detail or error.diag.message_primary))
    return report
