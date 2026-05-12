"""Saudi bank short-code → display name mapping.

`Employee.bank_name` is stored as a 4-letter SWIFT/short code (e.g. "RJHI", "NCBK").
The customer-facing internal sheet uses a clean display name (e.g. "Al Rajhi Bank").
This module is the single source of truth for the translation.

If a short code is not in the table, `bank_display_for()` returns None and the
generator will leave the cell blank + flag it as a YELLOW warning on the row.
"""

# Top 10 codes confirmed from `SELECT bank_name, COUNT(*) FROM tabEmployee GROUP BY bank_name`
# (covers ~96% of employees). Remaining 9 to be added.
BANK_CODE_TO_DISPLAY = {
    "RJHI": "Al Rajhi Bank",
    "NCBK": "Saudi National Bank",
    "SABB": "Saudi British Bank",
    "INMA": "Alinma Bank",
    "RIBL": "Riyad Bank",
    "ALBI": "Bank Albilad",
    "BJAZ": "Bank Al-Jazira",
    "ARNB": "Arab National Bank",
    "BSFR": "Banque Saudi Fransi",
    "STC":  "STC Bank",
}


# Optional fallback: derive bank short code from IBAN positions 5-6 (2-digit bank code).
# Used only when `Employee.bank_name` is empty. Per SAMA published mappings.
IBAN_PREFIX_TO_BANK_CODE = {
    "05": "RIBL",
    "10": "NCBK",
    "15": "SABB",
    "20": "BSFR",
    "30": "ARNB",
    "40": "SIBC",
    "45": "SABB",
    "55": "SABB",
    "60": "ALBI",
    "65": "INMA",
    "80": "RJHI",
    "90": "ANB",
}


def lookup_by_short_code(short_code):
    if not short_code:
        return None
    return BANK_CODE_TO_DISPLAY.get(str(short_code).strip().upper())


def derive_short_code_from_iban(iban):
    """Saudi IBAN: SA + 2 check + 2 bank + 18 account. Returns short code or None."""
    if not iban:
        return None
    cleaned = "".join(c for c in str(iban) if c.isalnum()).upper()
    if not cleaned.startswith("SA") or len(cleaned) < 8:
        return None
    return IBAN_PREFIX_TO_BANK_CODE.get(cleaned[4:6])


def bank_display_for(employee_bank_code, iban):
    """Try employee's stored short-code first; fall back to IBAN-derived code."""
    display = lookup_by_short_code(employee_bank_code)
    if display:
        return display
    derived = derive_short_code_from_iban(iban)
    return lookup_by_short_code(derived)
