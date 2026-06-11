# ERC payroll — template column-map audit
#
# Verifies, for every Payroll Import Template, that each project's input-file
# columns the Elite-sheet BILLING needs are actually mapped. The billing code
# (generators/internal_sheet.py) is identical for all projects; only the
# per-template column_map varies. This finds the gaps.
#
# Run on the bench (cloud or local):
#   bench --site <your-site> execute erc_payroll_automation.scripts.template_audit.run
# or paste the body into:  bench --site <your-site> console
#
# Copy the whole printed report back.

import frappe

# target_fields the Elite-sheet BILLING depends on (internal_sheet.py)
BILLING_FIELDS = {
    "net_salary_from_file":     "Net  -> charge base",
    "gosi_from_file":           "Employee GOSI  -> charge-base add-back",
    "hq_deductions":            "HQ/ERC Ded  -> charge-base add-back",
    "working_days":             "Working days  -> ERC fee x months",
    "basic_per_contract":       "Basic (contract)  -> GOSI base + employed",
    "total_per_contract":       "Total (contract)  -> employed",
    "basic_per_working_days":   "Basic (worked days) -> GOSI new-joiner + employed fallback",
    "housing_per_contract":     "Housing (contract) -> GOSI base",
    "housing_per_working_days": "Housing (worked days) -> GOSI new-joiner base",
}

# keyword -> reason, to flag UNMAPPED file columns that look billing-relevant
KW = [
    (("gosi", "تأمين", "التأمينات"),               "GOSI?"),
    (("hq", "erc", "head office", "مكتب"),          "HQ/ERC?"),
    (("net", "صافي"),                               "NET?"),
    (("working day", "work day", "wd", "أيام"),     "WORKDAYS?"),
    (("basic", "اساسي", "أساسي"),                   "BASIC?"),
    (("housing", "سكن"),                            "HOUSING?"),
    (("deduct", "خصم", "استقطاع"),                  "DEDUCTION?"),
]


def _flags(h):
    s = (h or "").strip().lower()
    out = []
    for keys, tag in KW:
        if any(k in s for k in keys):
            out.append(tag)
    return out


def _is_label(c):
    return isinstance(c, str) and any(ch.isalpha() for ch in c)


def _read_headers(run):
    """Best-effort: find spreadsheet(s) attached to the run, return {sheet: [labels]}."""
    urls = []
    for df in frappe.get_meta(run.doctype).fields:
        if df.fieldtype in ("Attach", "Attach Image", "Data", "Small Text", "Text", "Long Text"):
            v = run.get(df.fieldname)
            if isinstance(v, str) and "/files/" in v and v.lower().split("?")[0].endswith((".xlsx", ".xls", ".csv")):
                urls.append(v)
    headers = {}
    for url in urls:
        try:
            fdoc = frappe.get_doc("File", {"file_url": url})
            path = fdoc.get_full_path()
        except Exception as e:
            headers["<file-doc-error> " + url] = [str(e)]
            continue
        try:
            if path.lower().endswith(".csv"):
                import csv
                with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
                    rows = list(csv.reader(fh))[:8]
                best, score = [], -1
                for r in rows:
                    labels = [c.strip() for c in r if _is_label(c)]
                    if len(labels) > score:
                        best, score = labels, len(labels)
                headers[url.split("/")[-1]] = best
            else:
                import openpyxl
                wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
                for ws in wb.worksheets:
                    best, score = [], -1
                    for r in ws.iter_rows(min_row=1, max_row=8, values_only=True):
                        labels = [str(c).strip() for c in r if _is_label(c)]
                        if len(labels) > score:
                            best, score = labels, len(labels)
                    headers[ws.title] = best
                wb.close()
        except Exception as e:
            headers["<read-error> " + url] = [str(e)]
    return headers


def run():
    print("=" * 90)
    print("ERC TEMPLATE COLUMN-MAP AUDIT")
    print("=" * 90)

    templates = frappe.get_all(
        "Payroll Import Template",
        fields=["name", "project", "customer", "input_mode"],
        order_by="project asc, name asc",
    )
    template_projects = set()

    for t in templates:
        doc = frappe.get_doc("Payroll Import Template", t.name)
        cmap = {}
        for r in (doc.get("column_map") or []):
            src = (r.get("source_column") or "").strip()
            cmap[src] = r.get("target_field")
        mapped_targets = set(cmap.values())
        mode = t.input_mode or "Full from File"
        is_delta = str(mode).lower().startswith("delta")
        if t.project:
            template_projects.add(t.project)

        print("\n" + "-" * 90)
        print(f"TEMPLATE: {t.name}  |  project={t.project}  |  customer={t.customer}  |  mode={mode}")
        if is_delta:
            print("  (delta mode — rows are built in code from system data; empty column_map is EXPECTED)")

        have = [f for f in BILLING_FIELDS if f in mapped_targets]
        miss = [f for f in BILLING_FIELDS if f not in mapped_targets]
        print("  mapped billing fields :", ", ".join(have) or "(none)")
        print("  unmapped billing fields:", ", ".join(miss) or "(none)")

        runs = frappe.get_all(
            "Payroll Import Run",
            filters={"template": t.name},
            fields=["name"],
            order_by="creation desc",
            limit=1,
        )
        if not runs:
            print("  NO Payroll Import Run yet -> cannot inspect a real file for this template.")
            continue

        run_doc = frappe.get_doc("Payroll Import Run", runs[0].name)
        hdrs = _read_headers(run_doc)
        if not hdrs:
            print(f"  (latest run {runs[0].name}: no readable spreadsheet attachment found)")
            continue

        for sheet, labels in hdrs.items():
            suspicious = []
            for h in labels:
                if h in cmap:
                    continue  # already mapped
                fl = _flags(h)
                if fl:
                    suspicious.append(f"{h!r} -> {','.join(fl)}")
            if suspicious:
                print(f"  [sheet '{sheet}'] UNMAPPED billing-like columns ({runs[0].name}):")
                for s in suspicious:
                    print("       -", s)
            else:
                print(f"  [sheet '{sheet}'] no unmapped billing-like columns. ({len(labels)} headers seen)")

    # --- project billing config (affects the sheet + auto invoice routing) ---
    print("\n\n" + "=" * 90)
    print("PROJECT BILLING CONFIG (for templates above)")
    print("=" * 90)
    for proj in sorted(template_projects):
        p = frappe.db.get_value(
            "Project", proj,
            ["customer", "erc_fee", "bt_charges", "invoice_type", "custom_allow_po_management"],
            as_dict=True,
        ) or {}
        print(f"{proj}: erc_fee={p.get('erc_fee')}  bt_charges={p.get('bt_charges')}  "
              f"invoice_type={p.get('invoice_type')!r}  customer={p.get('customer')}  "
              f"PO_mgmt={p.get('custom_allow_po_management')}")

    print("\n" + "=" * 90)
    print("END OF REPORT — paste everything above back to Claude.")
    print("=" * 90)


if __name__ == "__main__":
    run()
