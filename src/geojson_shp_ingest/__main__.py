import os
import json
import pandas as pd
import geopandas as gpd
import numpy as np
from pathlib import Path
from datetime import datetime
import shutil
from ..utils.db_connection import PostgreSQLDatabase
from ..utils.file_handler import archive_file

# Source file directory
SOURCE_DIR = os.getenv('SOURCE_DIR')
GEO_DIR = 'geo_files'

def process_geo_file(file_path,target_srid):
    # Read the geographic file
    gdf = gpd.read_file(file_path)
    # Reproject if target SRID is specified and different from current
    if target_srid and gdf.crs.to_epsg() != target_srid:
        gdf = gdf.to_crs(epsg=target_srid)
    
    return(gdf)

def load_geo_to_postgres(file_path, table_name, db, target_srid=None):
    """
    Load shapefile or geojson to PostgreSQL/PostGIS with load timestamp tracking
    
    Args:
        file_path (str): Path to .shp or .geojson file
        table_name (str): Name for the destination table
        db (PostgreSQLDatabase): Instance of PostgreSQLDatabase class
        target_srid (int): Target SRID for the geometry (e.g., 4326 for WGS84)
    """
    
    
    gdf = process_geo_file(file_path,target_srid)
    
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

# Example usage
if __name__ == "__main__":
    db = PostgreSQLDatabase()
    db.connect()
    
    try:
        # Example: Load and convert to WGS84
        load_geo_to_postgres(
            'path/to/your/file.shp',
            'my_geo_table',
            db,
            target_srid=4326
        )
    finally:
        
        db.disconnect()