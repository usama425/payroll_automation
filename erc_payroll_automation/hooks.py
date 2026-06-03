app_name = "erc_payroll_automation"
app_title = "ERC Payroll Automation"
app_publisher = "ERC"
app_description = "Internal payroll import & reconciliation automation for ERC."
app_email = "usamaabdullah.link@gmail.com"
app_license = "MIT"

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/erc_payroll_automation/css/erc_payroll_automation.css"
# app_include_js = "/assets/erc_payroll_automation/js/erc_payroll_automation.js"

# DocType-specific JS
doctype_js = {
    "Payroll Import Run": "public/js/payroll_import_run_form.js",
    "Payroll No File Run": "public/js/payroll_no_file_run_form.js",
    "Payroll Posting Run": "public/js/payroll_posting_run_form.js",
    "Payroll WPS Export Run": "public/js/payroll_wps_export_run_form.js",
}

# Installation
# ------------

after_install = "erc_payroll_automation.install.after_install"

# Fixtures
# --------
# Workflow + roles ship as fixtures so a fresh install gets the state machine.
fixtures = [
    {
        "dt": "Role",
        "filters": [
            ["name", "in", ["Payroll Import Manager", "Payroll Import User"]]
        ],
    },
    {
        "dt": "Payroll Import Template",
        "filters": [
            [
                "name",
                "in",
                [
                    # Original three
                    "RSG Misk School", "RSG Malqa", "Misk Sport",
                    # Independent customers
                    "Arabian Center", "Riyadh School", "JCD",
                    "Direct FN", "Yamama (YCC)", "Cummins",
                    # AP Hashim — one per location
                    "AP Hashim - AMJAD", "AP Hashim - Damam",
                    "AP Hashim - HO", "AP Hashim - JEDDAH-2",
                    "AP Hashim - JUBAIL", "AP Hashim - RIYADH2",
                    # RSG sub-projects (added in this push)
                    "RSG Eli", "RSG Football Academy", "RSG Nursery",
                    "RSG Khuzam", "RSG HQ", "RSG (Main)", "RSG Hittin",
                    # Delta-mode projects (base from system + file deltas)
                    "Datavolt", "Airproducts",
                ],
            ]
        ],
    },
    {
        "dt": "Workflow",
        "filters": [["name", "=", "Payroll Import Workflow"]],
    },
    {
        "dt": "Workflow State",
        "filters": [
            [
                "name",
                "in",
                [
                    "Draft",
                    "Parsing",
                    "Parsed",
                    "Reconciliation Pending",
                    "Reconciled",
                    "Outputs Generated",
                    "Closed",
                    "Cancelled",
                ],
            ]
        ],
    },
    {
        "dt": "Workflow Action Master",
        "filters": [
            [
                "name",
                "in",
                [
                    "Parse File",
                    "Auto: Parsed",
                    "Mark Reconciled",
                    "Generate Outputs",
                    "Submit (Close)",
                    "Cancel",
                ],
            ]
        ],
    },
]
