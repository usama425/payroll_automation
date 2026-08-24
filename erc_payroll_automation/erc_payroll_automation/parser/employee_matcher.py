"""Identifier-first employee matching engine.

Match order for a file row:

    Tier 1: Iqama / National ID exact (digits-only IDs)  → Employee.iqama_national_id
    Tier 2: Passport exact (alphanumeric with letters)   → Employee.passport_number
    Tier 3: IBAN exact (cleaned, uppercase)              → Employee.bank_ac_no
    Tier 4: Name — ONLY for rows that carry no identifier at all, i.e. files
            whose template has no ID and no IBAN column.

Two hard rules:

    * Identifiers win.  Every row that can be matched on an identifier is
      resolved before any name matching happens, so a name guess can never
      steal an employee from a row that knows their Iqama or IBAN.
    * One employee, one row.  An employee already claimed by another row is
      never handed to a second row; the later row is reported as unmatched.

Name matching uses token_sort_ratio over normalised names and additionally
requires a margin over the runner-up plus shared name tokens, so a shared
family name on its own can no longer produce a match.

In-memory indexes are scoped to template.project (+ optional custom_location
filter).  Typical 1500-row file matches in well under a second.
"""

import re
import unicodedata

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

# A name match must beat the runner-up by at least this much (0-1 scale),
# otherwise the two candidates are too close to call.
NAME_MARGIN = 0.05

# Minimum informative tokens the file name must share with the employee name.
MIN_SHARED_NAME_TOKENS = 2

# Name particles that carry no identifying weight on their own.
NAME_STOPWORDS = {
    "mr", "mrs", "ms", "miss", "dr", "eng", "engr", "prof",
    "bin", "bint", "ibn", "abd", "abdul", "abu", "al", "el", "la", "le",
    "de", "da", "del", "van", "von", "jr", "sr",
}


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _clean_iban(iban):
    return "".join(c for c in str(iban) if c.isalnum()).upper() if iban else ""


def _norm_id(value):
    """Strip spaces, dashes and punctuation from an ID/passport value.

    Numeric cells in .xls/.xlsx arrive as floats, so an Iqama reads as
    ``2426275224.0``; drop that trailing ``.0`` before stripping punctuation,
    otherwise the zero is glued onto the number and nothing ever matches.
    """
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    s = str(value).strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return "".join(c for c in s if c.isalnum()).upper()


def _normalize_name(value):
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    if not value:
        return ""
    s = unicodedata.normalize("NFKD", str(value))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("\xa0", " ")
    # keep latin letters, digits and the Arabic block
    s = re.sub(r"[^a-z0-9\u0600-\u06ff]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _name_tokens(normalised):
    """Informative tokens of an already-normalised name."""
    return {t for t in normalised.split() if len(t) > 1 and t not in NAME_STOPWORDS}


def _add(index, key, emp_id):
    if not key:
        return
    bucket = index.setdefault(key, [])
    if emp_id not in bucket:
        bucket.append(emp_id)


# ---------------------------------------------------------------------------
# Index building
# ---------------------------------------------------------------------------

def build_indexes(template):
    """Return:
        {
          "by_iqama":   {"1005492093": ["HR-EMP-00964"], ...},
          "by_passport":{"M7950324":   ["HR-EMP-00002"], ...},
          "by_iban":    {"SA8180...":  ["HR-EMP-00964"], ...},
          "by_client_id": {"123":      ["HR-EMP-00964"], ...},
          "names":      [(emp_id, display_name, normalised, {tokens}), ...],
          "employees":  {"HR-EMP-00964": {...}},
        }

    Identifier indexes map to a *list* of employee ids: when two employees
    share an Iqama or an IBAN the row is reported as ambiguous instead of
    silently matching whichever record was read last.
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
        _add(by_iqama, _norm_id(e.iqama_national_id), e.name)
        _add(by_passport, _norm_id(e.passport_number), e.name)
        _add(by_iban, _clean_iban(e.bank_ac_no), e.name)
        _add(by_client_id, _norm_id(e.employee_id_from_client), e.name)
        if e.employee_name:
            normalised = _normalize_name(e.employee_name)
            if normalised:
                names.append((e.name, e.employee_name.strip(), normalised, _name_tokens(normalised)))

    return {
        "by_iqama": by_iqama,
        "by_passport": by_passport,
        "by_iban": by_iban,
        "by_client_id": by_client_id,
        "names": names,
        "employees": emps,
    }


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _lookup(index, key):
    """-> (employee_id, None) | (None, [ids]) when ambiguous | (None, None)."""
    if not key:
        return None, None
    bucket = index.get(key) or []
    if len(bucket) == 1:
        return bucket[0], None
    if len(bucket) > 1:
        return None, bucket
    return None, None


def _match_identifier(parsed_row, indexes):
    """Resolve a row on Iqama / passport / IBAN only.

    Returns ``(result, had_identifier)``.  ``result`` is a hit, an ambiguous
    miss, or None when the identifiers simply did not resolve.
    """
    raw_id = _norm_id(parsed_row.get("raw_id_value"))
    raw_iban = _clean_iban(parsed_row.get("raw_iban"))
    had_identifier = bool(raw_id or raw_iban)

    if raw_id:
        if raw_id.isdigit() and len(raw_id) >= 8:
            emp_id, clash = _lookup(indexes["by_iqama"], raw_id)
            if emp_id:
                return _hit(emp_id, "iqama_exact", 1.0), had_identifier
            if clash:
                return _miss("ambiguous_id", _employee_suggestions(indexes, clash)), had_identifier

        emp_id, clash = _lookup(indexes["by_passport"], raw_id)
        if emp_id:
            return _hit(emp_id, "passport_exact", 1.0), had_identifier
        if clash:
            return _miss("ambiguous_id", _employee_suggestions(indexes, clash)), had_identifier

    if raw_iban:
        emp_id, clash = _lookup(indexes["by_iban"], raw_iban)
        if emp_id:
            return _hit(emp_id, "iban_exact", 1.0), had_identifier
        if clash:
            return _miss("ambiguous_id", _employee_suggestions(indexes, clash)), had_identifier

    return None, had_identifier


def _match_name(parsed_row, indexes, template, claimed=None):
    """Fuzzy-match on name, excluding employees already claimed by other rows."""
    claimed = claimed or {}
    normalised = _normalize_name(parsed_row.get("raw_name"))
    if not normalised:
        return _miss("empty_id_field", [])

    pool = [n for n in indexes["names"] if n[0] not in claimed]
    if not pool:
        return _miss("no_match", [])

    threshold = (template.name_match_threshold or 0.85) * 100
    scored = process.extract(
        normalised,
        [n[2] for n in pool],
        scorer=fuzz.token_sort_ratio,
        limit=3,
    )
    if not scored:
        return _miss("no_match", [])

    suggestions = [
        {"employee": pool[i][0], "name": pool[i][1], "score": round(s / 100, 4)}
        for _, s, i in scored
    ]

    _, best_score, best_idx = scored[0]
    if best_score < threshold:
        return _miss("no_match", suggestions)

    # too close to the runner-up to be trusted
    if len(scored) > 1 and (best_score - scored[1][1]) < NAME_MARGIN * 100:
        return _miss("ambiguous_name", suggestions)

    # a shared family name alone is not a match
    row_tokens = _name_tokens(normalised)
    shared = row_tokens & pool[best_idx][3]
    if len(shared) < min(MIN_SHARED_NAME_TOKENS, len(row_tokens) or 1):
        return _miss("ambiguous_name", suggestions)

    return _hit(pool[best_idx][0], "name_fuzzy", round(best_score / 100, 4))


def match_all(parsed_rows, indexes, template):
    """Match a whole file at once.  Returns a list of results aligned with
    ``parsed_rows``.

    Pass 1 resolves every row that carries an identifier (Iqama / passport /
    IBAN).  Pass 2 falls back to names, but only for rows that carry no
    identifier at all.  An employee matched in either pass is claimed and
    cannot be matched again.
    """
    results = [None] * len(parsed_rows)
    claimed = {}
    name_fallback_rows = []

    # ---- pass 1: identifiers -------------------------------------------------
    for i, p in enumerate(parsed_rows):
        result, had_identifier = _match_identifier(p, indexes)

        if result and result.get("employee"):
            emp_id = result["employee"]
            if emp_id in claimed:
                # the same employee is already taken by an earlier file row
                other = parsed_rows[claimed[emp_id]].get("_row_index")
                results[i] = _miss(
                    "duplicate_in_file",
                    _employee_suggestions(indexes, [emp_id]),
                    detail=f"already matched to file row {other}",
                )
            else:
                claimed[emp_id] = i
                results[i] = result
            continue

        if result:                      # ambiguous identifier
            results[i] = result
            continue

        if had_identifier:
            # Row has an Iqama/IBAN that is not in the system.  Deliberately
            # NOT falling back to the name: an unknown ID means an unknown
            # employee, and finance resolves it by hand.
            results[i] = _miss("no_match", _name_only_suggestions(p, indexes))
            continue

        name_fallback_rows.append(i)

    # ---- pass 2: name fallback for rows without any identifier ---------------
    for i in name_fallback_rows:
        result = _match_name(parsed_rows[i], indexes, template, claimed)
        if result.get("employee"):
            claimed[result["employee"]] = i
        results[i] = result

    return results


def match_row(parsed_row, indexes, template):
    """Single-row entry point (no cross-row duplicate protection).

    Kept for callers that match one row in isolation; the file parser uses
    :func:`match_all` so duplicates across the file are caught.
    """
    result, had_identifier = _match_identifier(parsed_row, indexes)
    if result:
        return result
    if had_identifier:
        return _miss("no_match", _name_only_suggestions(parsed_row, indexes))
    return _match_name(parsed_row, indexes, template)


# ---------------------------------------------------------------------------
# Suggestions shown on the unmatched row so finance can resolve by hand
# ---------------------------------------------------------------------------

def _name_only_suggestions(parsed_row, indexes, limit=3):
    normalised = _normalize_name(parsed_row.get("raw_name"))
    if not normalised or not indexes["names"]:
        return []
    scored = process.extract(
        normalised,
        [n[2] for n in indexes["names"]],
        scorer=fuzz.token_sort_ratio,
        limit=limit,
    )
    return [
        {
            "employee": indexes["names"][i][0],
            "name": indexes["names"][i][1],
            "score": round(s / 100, 4),
        }
        for _, s, i in scored
    ]


def _employee_suggestions(indexes, emp_ids):
    out = []
    for emp_id in emp_ids[:3]:
        e = indexes["employees"].get(emp_id)
        out.append({
            "employee": emp_id,
            "name": (e.employee_name if e else emp_id) or emp_id,
            "score": 1.0,
        })
    return out


def _hit(emp_id, method, confidence):
    return {"employee": emp_id, "method": method, "confidence": confidence, "reason": "ok"}


def _miss(reason, suggestions, detail=None):
    return {
        "employee": None,
        "method": None,
        "confidence": 0.0,
        "reason": reason,
        "detail": detail or "",
        "suggestions": suggestions or [],
    }
