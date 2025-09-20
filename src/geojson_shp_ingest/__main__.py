import os
import json
import pandas as pd
import geopandas as gpd
import numpy as np
from pathlib import Path
from datetime import datetime
import shutil
import argparse
from dotenv import load_dotenv
from ..utils.db_connection import PostgreSQLDatabase
from ..utils.file_handler import archive_file

def load_environment(env_mode):
    """Load environment variables based on mode"""
    if env_mode == 'dev':
        load_dotenv('.env.dev')
    else:
        load_dotenv('.env.prd')
    
    # Source file directory
    SOURCE_DIR = os.getenv('SOURCE_DIR')
    GEO_DIR = 'geo_files'
    return SOURCE_DIR, GEO_DIR

def process_geo_file(file_path, target_srid):
    # Read the geographic file
    gdf = gpd.read_file(file_path)
    # Reproject if target SRID is specified and different from current
    if target_srid and gdf.crs.to_epsg() != target_srid:
        gdf = gdf.to_crs(epsg=target_srid)
    
    return gdf

def load_geo_to_postgres(file_path, table_name, db, target_srid=None):
    """
    Load shapefile or geojson to PostgreSQL/PostGIS with load timestamp tracking
    
    Args:
        file_path (str): Path to .shp or .geojson file
        table_name (str): Name for the destination table
        db (PostgreSQLDatabase): Instance of PostgreSQLDatabase class
        target_srid (int): Target SRID for the geometry (e.g., 4326 for WGS84)
    """
    
    gdf = process_geo_file(file_path, target_srid)
    
    # Ensure database connection
    if not db.conn:
        db.connect()
    
    with db.conn.cursor() as cursor:
        # Check if table exists
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = %s
            )
        """, (table_name,))
        table_exists = cursor.fetchone()[0]
        
        replace_or_append = 'replace' if not table_exists else 'append'
        engine = db.create_engine()
        # Convert GeoDataFrame to PostGIS table
        # We'll use GeoDataFrame's to_postgis but with our connection
        gdf.to_postgis(
            table_name,
            engine,
            if_exists=replace_or_append,
            schema='staging',
            index=False
        )

        if not table_exists:
            # Add the load_dttm column with default
            cursor.execute(f"""
                ALTER TABLE {table_name} 
                ADD COLUMN load_dttm TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            """)
        db.conn.commit()

def process_file_list(file_list, db, target_srid=None):
    """Process a list of files and their target tables"""
    for item in file_list:
        file_path = item.get('file_path')
        table_name = item.get('table_name')
        if file_path and table_name:
            print(f"Processing {file_path} to table {table_name}")
            load_geo_to_postgres(file_path, table_name, db, target_srid)

def main():
    # Set up argument parser
    parser = argparse.ArgumentParser(description='Load geographic files to PostgreSQL/PostGIS')
    parser.add_argument('--dev', action='store_true', help='Use development environment')
    parser.add_argument('--prd', action='store_true', help='Use production environment')
    parser.add_argument('--file', type=str, help='Path to the geographic file')
    parser.add_argument('--table', type=str, help='Target table name')
    parser.add_argument('--json', type=str, help='JSON file containing file paths and table names')
    parser.add_argument('--srid', type=int, help='Target SRID for geometry', default=4326)
    
    args = parser.parse_args()
    
    # Determine environment mode
    env_mode = 'prd' if args.prd else 'dev'
    SOURCE_DIR, GEO_DIR = load_environment(env_mode)
    
    # Initialize database connection
    db = PostgreSQLDatabase()
    db.connect()
    
    try:
        # Process based on input method
        if args.json:
            # Load file list from JSON
            with open(args.json, 'r') as f:
                file_list = json.load(f)
            process_file_list(file_list, db, args.srid)
        elif args.file and args.table:
            # Process single file
            load_geo_to_postgres(args.file, args.table, db, args.srid)
        else:
            print("Error: Either provide --file and --table or --json arguments")
            return 1
        
        return 0
    finally:
        db.disconnect()

if __name__ == "__main__":
    main()