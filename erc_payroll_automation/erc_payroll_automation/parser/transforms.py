"""Per-cell transform functions referenced by Payroll Import Column Map.transform.

Each function takes one raw cell value (str / int / float / datetime / None)
and returns a normalized value. `apply()` dispatches by transform name.
"""
import re
from datetime import datetime, date

from openpyxl.utils.datetime import from_excel


def trim_whitespace(value):
    if value is None:
        return None
    return str(value).strip()


def to_float(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    s = str(value).strip().replace(",", "")
    s = re.sub(r"[^\d.\-]", "", s)
    if not s or s in ("-", "."):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_int(value):
    f = to_float(value)
    return int(f) if f is not None else None


def excel_date_to_iso(value):
    """Accept datetime, date, or Excel serial number; return python datetime."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return from_excel(value)
        except Exception:
            return None
    # String dates: try a couple of common formats
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def iban_normalize(value):
    """Strip spaces/dashes, uppercase. Returns clean IBAN or None."""
    if value is None:
        return None
    s = re.sub(r"[\s\-]", "", str(value)).upper()
    return s or None


def nationality_normalize(value):
    """Normalize 'British ', 'British / London', 'british' → 'British'."""
    if value is None:
        return None
    s = str(value).strip()
    s = re.split(r"[/,]", s)[0].strip()
    return s.title() if s else None


def strip_arabic_diacritics(value):
    """Remove Arabic diacritics (tashkeel) and tatweel."""
    if value is None:
        return None
    return re.sub(r"[ً-ٰٟـ]", "", str(value))


def uppercase(value):
    return str(value).upper() if value is not None else None


def lowercase(value):
    return str(value).lower() if value is not None else None


def abs_value(value):
    """Absolute value of a number.

    Oracle payroll registers book deductions and employee GOSI as negative
    amounts (Total GOSI -46,211.86).  The Elite sheet adds employee GOSI back
    onto Net to reach the Charge Base, so it needs the magnitude, not the sign.
    """
    v = to_float(value)
    return abs(v) if v is not None else None


def _noop(value):
    return value


REGISTRY = {
    None: _noop,
    "": _noop,
    "none": _noop,
    "trim_whitespace": trim_whitespace,
    "to_float": to_float,
    "to_int": to_int,
    "excel_date_to_iso": excel_date_to_iso,
    "iban_normalize": iban_normalize,
    "nationality_normalize": nationality_normalize,
    "strip_arabic_diacritics": strip_arabic_diacritics,
    "uppercase": uppercase,
    "lowercase": lowercase,
    "abs_value": abs_value,
}


def apply(transform_name, value):
    fn = REGISTRY.get(transform_name, _noop)
    return fn(value)
