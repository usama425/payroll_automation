"""Four-tier employee matching engine.

    Tier 1: Iqama / National ID exact (digits-only IDs)  → Employee.iqama_national_id
    Tier 2: Passport exact (alphanumeric with letters)   → Employee.passport_number
    Tier 3: IBAN exact (cleaned, uppercase)              → Employee.bank_ac_no
    Tier 4: Fuzzy name (rapidfuzz WRatio, threshold from template.name_match_threshold)

In-memory indexes are scoped to template.project (+ optional custom_location filter).
Typical 1500-row file matches in well under a second.
"""

import frappe
from rapidfuzz import fuzz, process


EMPLOYEE_FIELDS = [
    "name", "employee_name", "status", "relieving_date", "date_of_joining",
    "iqama_national_id", "passport_number",
    "bank_ac_no", "bank_name",
    "nationality", "added_to_gosi", "custom_residence_type",
    "project", "department", "custom_location",
    "employee_id_from_client",
]


def build_indexes(template):
    """Return:
        {
          "by_iqama":   {"1005492093": "HR-EMP-00964", ...},
          "by_passport":{"M7950324": "HR-EMP-00002", ...},
          "by_iban":    {"SA8180...": "HR-EMP-00964", ...},
          "by_client_id": {"123": "HR-EMP-00964", ...},
          "names":      [("HR-EMP-00964", "Fatemah Ali ..."), ...],
          "employees":  {"HR-EMP-00964": {...}},
        }
    """
    filters = {"status": "Active"}
    if template.project:
        filters["project"] = template.project
    if template.location_strategy in ("Custom Field on Employee", "Mix") and template.filter_by_work_location:
        field = template.location_field_on_employee or "custom_location"
        filters[field] = template.filter_by_work_location

    employees = frappe.get_all("Employee", filters=filters, fields=EMPLOYEE_FIELDS)

    by_iqama, by_passport, by_iban, by_client_id = {}, {}, {}, {}
    names = []
    emps = {}
    for e in employees:
        emps[e.name] = e
        if e.iqama_national_id:
            by_iqama[str(e.iqama_national_id).strip()] = e.name
        if e.passport_number:
            by_passport[str(e.passport_number).strip().upper()] = e.name
        if e.bank_ac_no:
            cleaned = _clean_iban(e.bank_ac_no)
            if cleaned:
                by_iban[cleaned] = e.name
        if e.employee_id_from_client:
            by_client_id[str(e.employee_id_from_client).strip()] = e.name
        if e.employee_name:
            names.append((e.name, e.employee_name.strip()))

    return {
        "by_iqama": by_iqama,
        "by_passport": by_passport,
        "by_iban": by_iban,
        "by_client_id": by_client_id,
        "names": names,
        "employees": emps,
    }


def _clean_iban(iban):
    return "".join(c for c in str(iban) if c.isalnum()).upper() if iban else ""


def match_row(parsed_row, indexes, template):
    """Return:
        {"employee": "HR-EMP-...", "method": "iqama_exact"|..., "confidence": 0.0-1.0, "reason": "ok"}
    or:
        {"employee": None, "method": None, "confidence": 0.0, "reason": "...", "suggestions": [...]}
    """
    raw_id = parsed_row.get("raw_id_value")
    raw_iban = parsed_row.get("raw_iban")
    raw_name = parsed_row.get("raw_name")

    # Tier 1 + 2: ID field can be Iqama (all digits) or Passport (has letters)
    if raw_id:
        s = str(raw_id).strip()
        s_compact = s.replace(" ", "")
        is_digits_only = s_compact.isdigit() and len(s_compact) >= 8
        is_alphanumeric = any(c.isalpha() for c in s_compact)

        if is_digits_only:
            emp_id = indexes["by_iqama"].get(s_compact)
            if emp_id:
                return _hit(emp_id, "iqama_exact", 1.0)

        if is_alphanumeric:
            emp_id = indexes["by_passport"].get(s_compact.upper())
            if emp_id:
                return _hit(emp_id, "passport_exact", 1.0)

        # Also check passport with the raw form even if pure digits (some passports are all digits)
        emp_id = indexes["by_passport"].get(s_compact.upper())
        if emp_id:
            return _hit(emp_id, "passport_exact", 0.95)

    # Tier 3: IBAN
    if raw_iban:
        cleaned = _clean_iban(raw_iban)
        if cleaned:
            emp_id = indexes["by_iban"].get(cleaned)
            if emp_id:
                return _hit(emp_id, "iban_exact", 1.0)

    # Tier 4: Fuzzy name
    if raw_name and indexes["names"]:
        threshold_pct = (template.name_match_threshold or 0.85) * 100
        candidates = [n for _, n in indexes["names"]]
        best = process.extractOne(
            str(raw_name).strip(),
            candidates,
            scorer=fuzz.WRatio,
            score_cutoff=threshold_pct,
        )
        if best:
            _, score, idx = best
            emp_id, _ = indexes["names"][idx]
            return _hit(emp_id, "name_fuzzy", round(score / 100, 4))

        # Below threshold — return top 3 suggestions for the user to resolve
        suggestions_raw = process.extract(
            str(raw_name).strip(),
            candidates,
            scorer=fuzz.WRatio,
            limit=3,
        )
        suggestions = [
            {
                "employee": indexes["names"][i][0],
                "name": n,
                "score": round(s / 100, 4),
            }
            for n, s, i in suggestions_raw
        ]
        return _miss("no_match", suggestions)

    # Nothing to match against
    if not raw_id and not raw_iban and not raw_name:
        return _miss("empty_id_field", [])
    return _miss("no_match", [])


def _hit(emp_id, method, confidence):
    return {"employee": emp_id, "method": method, "confidence": confidence, "reason": "ok"}


def _miss(reason, suggestions):
    return {
        "employee": None,
        "method": None,
        "confidence": 0.0,
        "reason": reason,
        "suggestions": suggestions or [],
    }
