from setuptools import setup, find_packages

setup(
    name="file_ingestion",
    version="0.1.0",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    install_requires=[
        "psycopg2-binary",
        "utils"
    ],
    entry_points={
        'console_scripts': [
            'ingest-tax-rolls=tax_roll_excel_ingest.tax_roll_excel_ingest:main',
        ],
    }
)