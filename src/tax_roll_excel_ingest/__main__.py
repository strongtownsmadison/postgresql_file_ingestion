import os
import json
import argparse
import pandas as pd
from pathlib import Path
from ..utils.db_connection import PostgreSQLDatabase
from ..utils.file_handler import archive_file

# Source file directory
SOURCE_DIR = os.getenv('SOURCE_DIR')
XLSX_DIR = 'tax_roll_xlsx/'

def find_header_row(df):
    """Find the header row in the DataFrame."""
    for i, row in df.iterrows():
        non_empty_cells = row.notna().sum()
        if non_empty_cells >= 3:
            return i
    raise ValueError("Header row not found in the file.")

def process_file(file_path):
    """Process a single XLSX file and return the data as a list of JSON strings."""
    df = pd.read_excel(file_path, header=None, dtype=str)
    header_row = find_header_row(df)
    
    # Set the header and remove rows above it
    df.columns = df.iloc[header_row]
    df = df.iloc[header_row + 1:]
    df = df.replace({pd.NA: None})
    # Convert DataFrame to list of JSON strings
    return [json.dumps(row.to_dict()) for _, row in df.iterrows()]


def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Process tax roll XLSX files')
    parser.add_argument('--prod', action='store_true', 
                       help='Run in production environment')
    args = parser.parse_args()
    
    # Set environment based on argument BEFORE creating database instance
    if args.prod:
        os.environ['ENVIRONMENT'] = 'prod'
        print("Running in production environment")
    else:
        # Explicitly set to 'dev' to ensure it's set
        os.environ['ENVIRONMENT'] = 'dev'
        print("Running in development environment")
    
    db = PostgreSQLDatabase()
    
    try:
        db.connect()
        
        #Make sure staging table exists
        db.execute_from_file('./src/tax_roll_excel_ingest/create_staging_tax_roll_xlsx.sql')

        # Ensure SOURCE_DIR is set after environment variables are loaded
        source_dir = os.getenv('SOURCE_DIR')
        if not source_dir:
            raise EnvironmentError("SOURCE_DIR environment variable is not set")
        source_dir = Path(source_dir) / XLSX_DIR
        # Process all XLSX files in the source directory
        for file in Path(source_dir).glob('*.xlsx'):
            print(f"Processing file: {file}")
            data = process_file(file)
            db.insert_data('staging.tax_roll_xlsx', data)
            print(f"Inserted {len(data)} rows from {file}")

            # Archive the file after successful processing
            _ = archive_file(file)
            

    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        db.disconnect()

if __name__ == "__main__":
    main()