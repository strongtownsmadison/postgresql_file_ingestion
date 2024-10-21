import os
import json
import pandas as pd
import numpy as np
from pathlib import Path
#Bandaid until I figure out how to make the utils stuff a working package
#sys.path.append(str(Path(__file__).parent.parent))
from datetime import datetime
import shutil

# Capture the output of a command
#import utils
#from src.utils.db_connection.db_connection import PostgreSQLDatabase
from ..utils.db_connection import PostgreSQLDatabase

# Source file directory
SOURCE_DIR = os.getenv('SOURCE_DIR')

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

def archive_file(file_path):
    """
    Archive the processed file with a timestamp in its filename.
    Returns the path of the archived file.
    """
    source_path = Path(file_path)
    archive_dir = source_path.parent / 'archive'
    
    # Create archive directory if it doesn't exist
    archive_dir.mkdir(exist_ok=True)
    
    # Generate new filename with timestamp
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    new_filename = f"{source_path.stem}_{timestamp}{source_path.suffix}"
    archive_path = archive_dir / new_filename
    
    # Move the file to archive directory
    shutil.move(str(source_path), str(archive_path))
    print(f"Archived file to: {archive_path}")
    return archive_path

def main():
    db = PostgreSQLDatabase()
    
    try:
        db.connect()
        
        #Make sure staging table exists
        db.execute_from_file('./src/tax_roll_excel_ingest/create_staging_tax_roll_xlsx.sql')

        # Ensure SOURCE_DIR is set after environment variables are loaded
        source_dir = os.getenv('SOURCE_DIR')
        if not source_dir:
            raise EnvironmentError("SOURCE_DIR environment variable is not set")
        
        # Process all XLSX files in the source directory
        for file in Path(source_dir).glob('*.xlsx'):
            print(f"Processing file: {file}")
            data = process_file(file)
            db.insert_data('staging.tax_roll_xlsx', data)
            print(f"Inserted {len(data)} rows from {file}")

            # Archive the file after successful processing
            archived_path = archive_file(file)
            print(f"Successfully processed and archived: {file} -> {archived_path}")

    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        db.disconnect()

if __name__ == "__main__":
    main()