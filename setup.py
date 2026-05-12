from setuptools import setup, find_packages

with open("requirements.txt") as f:
    install_requires = f.read().strip().split("\n")

# get version from __version__ variable in erc_payroll_automation/__init__.py
from erc_payroll_automation import __version__ as version

setup(
    name="erc_payroll_automation",
    version=version,
    description="Internal payroll import & reconciliation automation for ERC.",
    author="ERC",
    author_email="usamaabdullah.link@gmail.com",
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=install_requires,
)
