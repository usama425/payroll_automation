"""Generate fixtures for the 16 new payroll import templates."""

import json
import os

# //asd
def cm(target, header=None, fallback=None, transform="trim_whitespace", required=0):
    """Build a single column-map row."""
    return {
        "doctype": "Payroll Import Column Map",
        "target_field": target,
        "source_column_header": header or "",
        "column_index_fallback": fallback if fallback is not None else None,
        "transform": transform,
        "is_required": required,
    }


def f(target, header=None, fallback=None, required=0):
    """Float-valued column."""
    return cm(target, header, fallback, "to_float", required)


def s(target, header=None, fallback=None, required=0):
    """String-trim column."""
    return cm(target, header, fallback, "trim_whitespace", required)


def iban(target, header=None, fallback=None):
    return cm(target, header, fallback, "iban_normalize", 0)


def nat(target, header=None, fallback=None):
    return cm(target, header, fallback, "nationality_normalize", 0)


def base_template(template_name, sheet_name, header_row, notes, column_map,
                  location_strategy="Custom Field on Employee",
                  input_mode="Full from File"):
    return {
        "doctype": "Payroll Import Template",
        "name": template_name,
        "template_name": template_name,
        "is_active": 1,
        "input_mode": input_mode,
        "location_strategy": location_strategy,
        "location_field_on_employee": "custom_location",
        "sheet_name_override": sheet_name,
        "header_row_index": header_row,
        "skip_blank_id_rows": 1,
        "name_match_threshold": 0.85,
        "gosi_rate_saudi": 9.75,
        "gosi_rate_non_saudi": 0.0,
        "output_format_override": "Default (37-column RSG-Malqa)",
        # Real Employee fields confirmed via SQL: the Salary Structure formula
        # pulls Housing/Transport/Food from these; Basic = SSA.base which
        # tracks Employee.basic_salary.
        "no_file_emp_field_basic": "basic_salary",
        "no_file_emp_field_housing": "housing_allowance",
        "no_file_emp_field_transportation": "transport_allowance",
        "no_file_emp_field_other_allowance": "food_allowance",
        "notes": f"<p>Auto-installed seed template. {notes}</p>",
        "column_map": column_map,
    }


# =============================================================================
# 1. Arabian Center
# =============================================================================
arabian_center = base_template(
    "Arabian Center",
    sheet_name="PAYROLL SHEET",
    header_row=1,
    notes="Customer file uses 29-column layout. Iqama on col 6, salary "
          "broken into BASIC / COLA / TRANSPORT / HOUSING / HTA / Other "
          "ALLOWANCE / GOSI. Fill in Customer + Project + Filter by Work "
          "Location before first use.",
    column_map=[
        s("raw_name", "EMP NAME", required=1),
        s("raw_id_value", "Iqama No.", required=1),
        nat("raw_nationality", "NATIONALITY"),
        f("basic_per_contract", "BASIC SALARY"),
        f("basic_per_working_days", "NEW BASIC SALARY"),
        f("housing_per_working_days", "HOUSING ALLOWANCE"),
        f("transportation_per_working_days", "TRANSPORT ALLOWANCE"),
        f("other_allowance_per_working_days", "Other  ALLOWANCE"),  # 2 spaces in source
        f("gosi_from_file", "GOSI"),
        f("total_per_contract", "GROSS SALARY"),
        f("overtime", "OT"),
        f("deductions", "DEDUCTION FOR ABSENT"),
        f("net_salary_from_file", "NET SALARY"),
    ],
)

# =============================================================================
# 2. Riyadh School (Elite bank-transfer format)
# =============================================================================
riyadh_school = base_template(
    "Riyadh School",
    sheet_name="Elite April 2026",
    header_row=1,
    notes="Bank-transfer style file: name / bank / IBAN / basic / housing / "
          "other / deductions / net / National_ID. No nationality column.",
    column_map=[
        s("raw_name", "EMPLOYEE_NAME", required=1),
        s("raw_id_value", "National_ID", required=1),
        iban("raw_iban", "ACCOUNT_NUMBER"),
        f("basic_per_working_days", "BASIC_SALARY"),
        f("housing_per_working_days", "HOUSING_ALLOWANCE"),
        f("other_allowance_per_working_days", "OTHER_EARNINGS"),
        f("deductions", "DEDUCTIONS"),
        f("net_salary_from_file", "NET_PAY"),
    ],
)

# =============================================================================
# 2b. Riyadh Schools 2 (newer Elite bank-transfer variant)
# =============================================================================
# Two-sheet customer file; we read the English "Elite <Month> <Year>" sheet
# (the Arabic detail sheet only carries a 5-6 digit client number, which can't
# Iqama-match). Header names differ from "Riyadh School": Employee Name / Saudi
# ID-Iqama / Account Number / Basic Salary / Housing Allowance / Other Earnings
# / Deductions / Net Pay. Sheet override is the stable prefix "Elite" (the
# parser matches "Elite Jun 2026" etc.). Match on Iqama, IBAN fallback.
riyadh_schools_2 = base_template(
    "Riyadh Schools 2",
    sheet_name="Elite",
    header_row=1,
    notes="Riyadh Schools second Elite bank-transfer variant. Reads the "
          "English 'Elite &lt;Month&gt; &lt;Year&gt;' sheet (override prefix "
          "'Elite'). Cols: Bank / Account Number(IBAN) / Net Pay / Transaction "
          "Reference(client emp #) / Employee Name / Saudi ID-Iqama / Address / "
          "Basic Salary / Housing Allowance / Other Earnings / Deductions. "
          "Matches on Iqama (Saudi ID / Iqama), IBAN fallback. Fill in Customer "
          "+ Project + Filter by Work Location before first use.",
    column_map=[
        s("raw_name", "Employee Name", fallback=5, required=1),
        s("raw_id_value", "Saudi ID / Iqama", fallback=6, required=1),
        iban("raw_iban", "Account Number", fallback=2),
        f("net_salary_from_file", "Net Pay", fallback=3),
        f("basic_per_working_days", "Basic Salary", fallback=8),
        f("housing_per_working_days", "Housing Allowance", fallback=9),
        f("other_allowance_per_working_days", "Other Earnings", fallback=10),
        f("deductions", "Deductions", fallback=11),
    ],
)

# =============================================================================
# 3. JCD (42-col, custom emp ID prefixed JCDxxx)
# =============================================================================
jcd = base_template(
    "JCD",
    sheet_name="Sheet1",
    header_row=1,
    notes="Custom Employee ID (JCD-prefixed e.g. JCD204J) on col 1; map to "
          "Employee.employee_id_from_client. Multiple overtime + allowance "
          "columns aggregated by source file.",
    column_map=[
        s("raw_id_value", "Employee ID", required=1),
        s("raw_name", "Employee Name", required=1),
        nat("raw_nationality", "Nationality"),
        f("basic_per_working_days", "Basic"),
        f("housing_per_working_days", "Housing Allowance"),
        f("transportation_per_working_days", "Transportation Allowance"),
        f("other_allowance_per_working_days", "Other Allowance"),
        f("total_per_contract", "Total Salary"),
        f("overtime", "Total Overtime Amount"),
        f("other_income", "Total Allowance"),
        f("gosi_from_file", "GOSI Deduction[Em]"),
        f("deductions", "Total Deduction"),
        f("net_salary_from_file", "Net Salary"),
    ],
)

# =============================================================================
# 4. Direct FN
# =============================================================================
direct_fn = base_template(
    "Direct FN",
    sheet_name="",  # first data sheet
    header_row=1,
    notes="13-column simple format: Iqama / Name / Bank / IBAN / total / "
          "basic / housing / other / deduction.",
    column_map=[
        s("raw_id_value", "Emp. ID. No.", required=1),
        s("raw_name", "Emp. Name", required=1),
        iban("raw_iban", "Emp. Acc. No."),
        f("total_per_contract", "Salary Amount"),
        f("basic_per_working_days", "Basic Salary"),
        f("housing_per_working_days", "Housing Allowance"),
        f("other_allowance_per_working_days", "Other Earnings"),
        f("deductions", "Deduction"),
    ],
)

# =============================================================================
# 5. Yamama (YCC) — Arabic headers; we use column indexes only
# =============================================================================
yamama = base_template(
    "Yamama (YCC)",
    sheet_name="",  # first data sheet
    header_row=3,
    notes="Arabic headers — column lookup by header text is unreliable. "
          "All fields use column_index_fallback. Layout: 1=ERP ID, 2=Name, "
          "3=Hire date, 4=Iqama, 5=Basic, 6=COLA?, 7=Housing, 8=Transport, "
          "9=Mobile, 10=Allowances, 11=Other, 12=Bonus, 13=Working days, "
          "14=OT, 15-18=Deductions, 19=Misc deduction, 20=Net.",
    column_map=[
        s("raw_id_value", fallback=4, required=1),
        s("raw_name", fallback=2, required=1),
        f("basic_per_working_days", fallback=5),
        f("housing_per_working_days", fallback=7),
        f("transportation_per_working_days", fallback=8),
        f("other_allowance_per_working_days", fallback=11),
        f("working_days", fallback=13),
        f("overtime", fallback=14),
        f("deductions", fallback=18),
        f("hq_deductions", fallback=17),
        f("net_salary_from_file", fallback=20),
    ],
)

# =============================================================================
# 6. Cummins (57-col, multi-row title bar)
# =============================================================================
cummins = base_template(
    "Cummins",
    sheet_name="",  # first data sheet
    header_row=4,
    notes="Multi-row title bar in source; real headers on row 4. WWID is the "
          "custom employee ID; map to Employee.employee_id_from_client. "
          "Many allowance + retro columns aggregated by source.",
    column_map=[
        s("raw_id_value", "WWID", required=1),
        s("raw_name", "Name", required=1),
        f("basic_per_working_days", "Basic Salary"),
        f("housing_per_working_days", "Housing Allowance"),
        f("transportation_per_working_days", "Transportation Allowance"),
        f("other_allowance_per_working_days", "Total Other Allowances"),
        f("total_per_contract", "TOTAL Salary & Allownces"),  # source misspelling
        f("overtime", "Total Overtime Pay"),
        f("other_income", "Total Other payment"),
        f("deductions", "Total Voluntary Deduction"),
        f("net_salary_from_file", "Grand Total(Net Pay)"),
    ],
)

# =============================================================================
# 7-12. AP Hashim — 6 location templates, same 72-col format
# =============================================================================
ap_hashim_columns = [
    s("raw_id_value", "Code", required=1),
    s("raw_name", "Employee Name", required=1),
    iban("raw_iban", "IBAN"),
    f("basic_per_contract", "Basic Salary (SAR)"),
    f("working_days", "Working Days"),
    f("basic_per_working_days", "Worth Basic Salary (SAR)"),
    f("other_allowance_per_working_days", "Worth Allowances (SAR)"),
    f("overtime", "Overtime (SAR)"),
    f("other_income", "Other Income (SAR)"),
    f("gosi_from_file", "Social Security (SAR)"),
    f("deductions", "Total Deduction (SAR)"),
    f("net_salary_from_file", "Net Salary (SAR)"),
]


def _ap(loc):
    return base_template(
        f"AP Hashim - {loc}",
        sheet_name="",  # first sheet (it's '&nbsp;' which we can't easily express)
        header_row=1,
        notes=f"AP Hashim 72-column format. Set Filter by Work Location to "
              f"the '{loc}' location. 'Code' is the customer-side numeric "
              f"employee ID — map to Employee.employee_id_from_client.",
        column_map=ap_hashim_columns,
    )


ap_hashim_locations = ["AMJAD", "Damam", "HO", "JEDDAH-2", "JUBAIL", "RIYADH2"]
ap_hashim_templates = [_ap(loc) for loc in ap_hashim_locations]

# =============================================================================
# 13-18. RSG sub-projects sharing the common 27-col layout
# =============================================================================
rsg_common_columns_baisc = [   # variant A: source uses 'Baisc' typo (Eli, Football, Nursery)
    s("raw_name", "Name", required=1),
    s("raw_id_value", "ID/Iqama/ passprot", required=1),
    iban("raw_iban", "IBAN/ Account number"),
    nat("raw_nationality", "Nationl"),
    f("basic_per_contract", "Basic per contract"),
    f("working_days", "Working Days"),
    f("basic_per_working_days", "Baisc"),
    f("housing_per_working_days", "Housing"),
    f("transportation_per_working_days", "Transportaion"),
    f("other_allowance_per_working_days", "Other Allowances"),
    f("total_per_contract", "Total Salary"),
    f("overtime", "Overtime"),
    f("other_income", "Other Income"),
    f("gosi_from_file", "GOSI Contribution"),
    f("deductions", "Deductions"),
    f("net_salary_from_file", "Emp Net Salary"),
]

rsg_common_columns_basic = [   # variant B: source uses 'Basic' (Khuzam, RSG HQ, RSG main)
    s("raw_name", "Name", required=1),
    s("raw_id_value", "ID/Iqama/ passprot", required=1),
    iban("raw_iban", "IBAN/ Account number"),
    nat("raw_nationality", "Nationality"),
    f("basic_per_contract", "Basic per contract"),
    f("working_days", "Working Days"),
    f("basic_per_working_days", "Basic"),
    f("housing_per_working_days", "Housing"),
    f("transportation_per_working_days", "Transportaion"),
    f("other_allowance_per_working_days", "Other Allowances"),
    f("total_per_contract", "Total Salary"),
    f("overtime", "Overtime"),
    f("other_income", "Other Income"),
    f("gosi_from_file", "GOSI Contribution"),
    f("deductions", "Deductions"),
    f("net_salary_from_file", "Emp Net Salary"),
]


def _rsg(name, columns, sheet="Sheet1", header_row=2, note_extra=""):
    return base_template(
        name,
        sheet_name=sheet,
        header_row=header_row,
        notes=f"RSG sub-project template. {note_extra} Fill in Customer + "
              f"Project + Filter by Work Location before first use.",
        column_map=columns,
    )


rsg_eli = _rsg("RSG Eli", rsg_common_columns_baisc,
               note_extra="Layout uses 'Baisc' (typo) and 'Nationl' headers.")
rsg_football = _rsg("RSG Football Academy", rsg_common_columns_baisc,
                    note_extra="Layout uses 'Baisc' (typo) and 'Nationl' headers.")
rsg_nursery = _rsg("RSG Nursery", rsg_common_columns_baisc,
                   note_extra="Layout uses 'Baisc' (typo) and 'Nationl' headers.")
rsg_khuzam = _rsg("RSG Khuzam", rsg_common_columns_basic,
                  note_extra="Layout uses 'Basic' (correct) and 'Nationality' headers.")
rsg_hq = _rsg("RSG HQ", rsg_common_columns_basic,
              note_extra="Layout uses 'Basic' (correct) and 'Nationality' headers.")
rsg_main = _rsg("RSG (Main)", rsg_common_columns_basic,
                note_extra="Layout uses 'Basic' (correct) and 'Nationality' headers.")

# =============================================================================
# 19. RSG Hittin — 38-col with Location + Bank Name + duplicate per-contract/per-WD
# =============================================================================
rsg_hittin_columns = [
    s("raw_name", "Name", required=1),
    s("raw_id_value", "ID/Iqama/ passprot", required=1),
    iban("raw_iban", "IBAN/ Account number"),
    nat("raw_nationality", "Nationl"),
    # Per-contract block (cols 10-14 below Hiring etc.)
    f("basic_per_contract", "Basic per contract"),
    f("working_days", "Working Days"),
    # Hittin has TWO blocks: cols 14-18 = per-contract amounts, 19-23 = per-WD
    # Both blocks have the same header text — use column_index_fallback.
    f("housing_per_contract", fallback=15),
    f("transportation_per_contract", fallback=16),
    f("other_allowance_per_contract", fallback=17),
    f("basic_per_working_days", fallback=19),
    f("housing_per_working_days", fallback=20),
    f("transportation_per_working_days", fallback=21),
    f("other_allowance_per_working_days", fallback=22),
    f("total_per_contract", fallback=24),
    f("overtime", "Overtime"),
    f("other_income", "Other Income"),
    f("gosi_from_file", "GOSI Contribution"),
    f("deductions", "Deductions"),
    f("net_salary_from_file", "Emp Net Salary"),
]
rsg_hittin = base_template(
    "RSG Hittin",
    sheet_name="Sheet1",
    header_row=1,
    notes="RSG Hittin uses an extended 38-column layout (no title row; "
          "includes Location + Bank Name + duplicate per-contract / "
          "per-working-day blocks). Per-WD columns are resolved via "
          "column_index_fallback because the headers are duplicated.",
    column_map=rsg_hittin_columns,
)

# =============================================================================
# Delta-mode templates (base salary from system, file supplies only deltas).
# These ignore column_map entirely — delta_parser.py reads the file by mode.
# Fill in Customer + Project + Filter by Work Location after install.
# =============================================================================
datavolt = base_template(
    "Datavolt",
    sheet_name=None,
    header_row=2,
    notes="DELTA mode. File carries only variable earnings/deductions; base "
          "salary comes from system Employee data for all project employees. "
          "Reads sheets 'Variable Earnings' (Amount -> Other Income) and "
          "'Variable Deductions' (Amount -> Deductions). Matches on Employee "
          "ID / employee_id_from_client. Fill in Customer + Project + Filter "
          "by Work Location before first use.",
    column_map=[],
    input_mode="Deltas (Datavolt)",
)

airproducts = base_template(
    "Airproducts",
    sheet_name=None,
    header_row=1,
    notes="DELTA mode. File carries only allowances/deductions; base salary "
          "comes from system Employee data. Matches on IQAMA. Overtime Amount "
          "-> Overtime; other allowance columns -> Other Income; DEDUCTION "
          "AMOUNT -> Deductions. Fill in Customer + Project + Filter by Work "
          "Location before first use.",
    column_map=[],
    input_mode="Deltas (Airproducts)",
)

# =============================================================================
# Combine + write
# =============================================================================
new_templates = [
    arabian_center, riyadh_school, riyadh_schools_2, jcd, direct_fn, yamama, cummins,
    *ap_hashim_templates,
    rsg_eli, rsg_football, rsg_nursery, rsg_khuzam, rsg_hq, rsg_main,
    rsg_hittin,
    datavolt, airproducts,
]


FIXTURES_PATH = (
    "/home/usama-abdullah/frappe/asd/erc_payroll_automation/"
    "erc_payroll_automation/fixtures/payroll_import_template.json"
)
with open(FIXTURES_PATH) as fh:
    existing = json.load(fh)

# Idempotent: keep ONLY the three hand-written originals as the base, then
# (re)append the generated set. Re-running this script must not duplicate.
ORIGINALS = {"RSG Misk School", "RSG Malqa", "Misk Sport"}
base = [t for t in existing if t.get("template_name") in ORIGINALS]
combined = base + new_templates

# Strip None values from column_index_fallback for clean JSON
for tpl in combined:
    for cmrow in tpl.get("column_map", []):
        if cmrow.get("column_index_fallback") in (None, ""):
            cmrow.pop("column_index_fallback", None)

with open(FIXTURES_PATH, "w") as fh:
    json.dump(combined, fh, indent=4, ensure_ascii=False)

print(f"Wrote {len(combined)} templates ({len(base)} originals + {len(new_templates)} generated):")
for t in combined:
    print(f"  - {t['template_name']}")
