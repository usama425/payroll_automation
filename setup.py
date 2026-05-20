import re
from setuptools import setup, find_packages

with open("requirements.txt") as f:
    install_requires = [
        line.strip() for line in f
        if line.strip() and not line.startswith("#")
    ]

# Read __version__ from __init__.py without importing the package
# (avoids chicken-and-egg during pip install on Frappe Cloud builders).
with open("erc_payroll_automation/__init__.py") as f:
    version = re.search(r"__version__\s*=\s*['\"](.+?)['\"]", f.read()).group(1)

setup(
    name="erc_payroll_automation",
    version=version,
    description="Internal payroll import & reconciliation automation for ERC.",
    author="ERC",
    author_email="usamaabdullah.link@gmail.com",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    python_requires=">=3.10,<3.11",
    install_requires=install_requires,
)
