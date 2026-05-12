"""Saudi bank short-code → display name mapping.

`Employee.bank_name` is stored as a free-text code (mostly 4-letter SWIFT codes like
"RJHI", "NCBK", "SABB") but the data contains variants ("Rajhi", "Al Rajhi", "SNB",
"Riyadh Bank", etc.). This module is the single source of truth for translating any
of these variants into the clean display name used in the internal sheet.

Lookups are case-insensitive. If a code isn't recognized, `bank_display_for()`
returns None and the generator flags the row as a YELLOW warning.
"""

# Confirmed from `SELECT bank_name, COUNT(*) FROM tabEmployee GROUP BY bank_name`
# (covers all 19 distinct values observed). Keys are NORMALIZED (uppercased,
# spaces collapsed) — see `_normalize()`.
BANK_CODE_TO_DISPLAY = {
    # Al Rajhi Bank (RJHI is the SWIFT code; "Rajhi" / "Al Rajhi" appear as user entries)
    "RJHI":      "Al Rajhi Bank",
    "RAJHI":     "Al Rajhi Bank",
    "ALRAJHI":   "Al Rajhi Bank",

    # Saudi National Bank (formerly NCB / Samba)
    "NCBK":      "Saudi National Bank",
    "SNB":       "Saudi National Bank",

    # Saudi British Bank / Saudi Awwal Bank
    "SABB":      "Saudi British Bank",
    "SAB":       "Saudi British Bank",

    # Alinma Bank
    "INMA":      "Alinma Bank",

    # Riyad Bank ("Riyadh" / "Riyadh Bank" are common user typos)
    "RIBL":      "Riyad Bank",
    "RIYADH":    "Riyad Bank",
    "RIYADHBANK":"Riyad Bank",

    # Bank Albilad
    "ALBI":      "Bank Albilad",

    # Bank Al-Jazira
    "BJAZ":      "Bank Al-Jazira",

    # Arab National Bank
    "ARNB":      "Arab National Bank",

    # Banque Saudi Fransi
    "BSFR":      "Banque Saudi Fransi",

    # STC Bank
    "STC":       "STC Bank",

    # Saudi Investment Bank
    "SIBC":      "Saudi Investment Bank",

    # D360 Bank (SWIFT: DBAKSARI)
    "DBAKSARI":  "D360 Bank",
    "D360":      "D360 Bank",

    # Gulf International Bank
    "GULF":      "Gulf International Bank",
    "GIB":       "Gulf International Bank",
}


# Fallback: derive bank short code from IBAN positions 5-6 (2-digit SAMA bank code).
# Only used when Employee.bank_name is blank.
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


def _normalize(code):
    if code is None:
        return None
    return "".join(str(code).strip().upper().split())


def lookup_by_short_code(short_code):
    n = _normalize(short_code)
    if not n:
        return None
    return BANK_CODE_TO_DISPLAY.get(n)


def derive_short_code_from_iban(iban):
    """Saudi IBAN: SA + 2 check + 2 bank + 18 account. Returns short code or None."""
    if not iban:
        return None
    cleaned = "".join(c for c in str(iban) if c.isalnum()).upper()
    if not cleaned.startswith("SA") or len(cleaned) < 8:
        return None
    return IBAN_PREFIX_TO_BANK_CODE.get(cleaned[4:6])


def bank_display_for(employee_bank_code, iban):
    """Resolution order:
        1. Employee.bank_name (any of the 19 known variants)
        2. IBAN bank-code prefix → short code → display
        3. None (generator will leave blank + flag warning)
    """
    display = lookup_by_short_code(employee_bank_code)
    if display:
        return display
    derived = derive_short_code_from_iban(iban)
    return lookup_by_short_code(derived)
