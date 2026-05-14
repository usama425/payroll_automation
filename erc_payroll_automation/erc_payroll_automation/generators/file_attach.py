"""Bulletproof file-attach helper for the generators.

We write the file to disk ourselves, create a File record by hand, and update
the parent Attach field via direct SQL.  This bypasses `save_file()` which
on Frappe Cloud has been silently dropping the parent-field link in some
worker contexts, leaving the Run's `internal_sheet_file` / `additional_salary_file`
empty even after `save_file()` returns a valid `file_url`.
"""

import io
import os
import re

import frappe


def save_excel_and_link(wb, run_name: str, fieldname: str, filename: str, label: str) -> str:
    """Persist an openpyxl Workbook as a private file and set ``fieldname`` on
    the Run to the resulting `/private/files/...` URL.

    Returns the file URL.  Raises on any failure.
    """
    # Step 1: serialise workbook
    buf = io.BytesIO()
    wb.save(buf)
    content_bytes = buf.getvalue()
    buf.close()

    # Step 2: sanitise filename + ensure uniqueness on disk
    safe_name = _safe_filename(filename)
    private_dir = frappe.get_site_path("private", "files")
    os.makedirs(private_dir, exist_ok=True)

    final_name = _unique_filename(private_dir, safe_name)
    full_path = os.path.join(private_dir, final_name)

    # Step 3: write bytes to disk
    with open(full_path, "wb") as f:
        f.write(content_bytes)

    file_url = f"/private/files/{final_name}"

    frappe.log_error(
        title=f"Payroll Import generate: wrote {label} to disk",
        message=(
            f"run={run_name} field={fieldname} "
            f"path={full_path!r} file_url={file_url!r} bytes={len(content_bytes)}"
        ),
    )

    # Step 4: create File record (so it shows up in Attachments)
    try:
        file_doc = frappe.get_doc({
            "doctype": "File",
            "file_name": final_name,
            "file_url": file_url,
            "is_private": 1,
            "folder": "Home/Attachments",
            "attached_to_doctype": "Payroll Import Run",
            "attached_to_name": run_name,
            "attached_to_field": fieldname,
        })
        file_doc.flags.ignore_permissions = True
        file_doc.flags.ignore_mandatory = True
        file_doc.insert(ignore_permissions=True)
        frappe.db.commit()

        frappe.log_error(
            title=f"Payroll Import generate: File record created for {label}",
            message=f"run={run_name} file_doc={file_doc.name!r} file_url={file_url!r}",
        )
    except Exception:
        frappe.log_error(
            title=f"Payroll Import generate: File record creation FAILED for {label}",
            message=frappe.get_traceback(),
        )
        # Non-fatal — the file is on disk and we'll still set the field below

    # Step 5: update the parent Attach field directly (this is the critical step
    # that was being missed before — `save_file(df=...)` does NOT update the
    # parent doc field, only the File record's `attached_to_field`).
    frappe.db.set_value(
        "Payroll Import Run", run_name,
        fieldname, file_url,
        update_modified=False,
    )
    frappe.db.commit()

    # Step 6: verify the field actually persisted
    verify = frappe.db.get_value("Payroll Import Run", run_name, fieldname)
    frappe.log_error(
        title=f"Payroll Import generate: linked {label} to Run",
        message=(
            f"run={run_name} field={fieldname} "
            f"set_to={file_url!r} db_now_has={verify!r}"
        ),
    )

    return file_url


def _safe_filename(name: str) -> str:
    """Replace characters that can break filesystem paths or URLs."""
    cleaned = re.sub(r"[^\w\s\-.()]+", "_", name, flags=re.UNICODE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "output.xlsx"


def _unique_filename(directory: str, name: str) -> str:
    """If ``name`` already exists in ``directory``, append a counter."""
    if not os.path.exists(os.path.join(directory, name)):
        return name
    base, ext = os.path.splitext(name)
    counter = 1
    while True:
        candidate = f"{base}_{counter}{ext}"
        if not os.path.exists(os.path.join(directory, candidate)):
            return candidate
        counter += 1
