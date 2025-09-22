import os
import json
import fiona
from fiona.crs import CRS
from fiona.transform import transform_geom
from pathlib import Path
from datetime import datetime, date
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

def build_file_path(source_dir, geo_dir, file_path):
    """
    Build full file path using source_dir and geo_dir
    
    Args:
        source_dir (str): Base source directory from environment
        geo_dir (str): Geographic files subdirectory
        file_path (str): Relative or absolute file path
    
    Returns:
        str: Full file path
    """
    # If file_path is already absolute, use it as-is
    if os.path.isabs(file_path):
        return file_path
    
    # Build path: SOURCE_DIR/GEO_DIR/file_path
    full_path = os.path.join(source_dir, geo_dir, file_path)
    return os.path.normpath(full_path)

def json_serializable(obj):
    """Convert non-serializable objects to JSON-serializable format"""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    elif hasattr(obj, '__dict__'):
        return str(obj)
    return obj

def clean_feature_properties(feature):
    """Clean feature properties to ensure JSON serializability"""
    if 'properties' in feature:
        cleaned_properties = {}
        for key, value in feature['properties'].items():
            if value is not None:
                cleaned_properties[key] = json_serializable(value)
            else:
                cleaned_properties[key] = None
        feature['properties'] = cleaned_properties
    return feature

def read_geo_file(file_path, target_srid=None):
    """
    Read geographic file and return GeoJSON features
    
    Args:
        file_path (str): Path to geographic file
        target_srid (int): Target SRID for reprojection (optional)
    
    Returns:
        list: List of GeoJSON feature dictionaries
    """
    features = []
    
    # Check if it's a GeoJSON file
    if file_path.lower().endswith(('.geojson', '.json')):
        # For GeoJSON, we can read directly
        with open(file_path, 'r') as f:
            data = json.load(f)
            
        # Extract features from GeoJSON
        if 'features' in data:
            features = data['features']
        elif 'type' in data and data['type'] == 'Feature':
            features = [data]
        else:
            # Assume it's a single feature without explicit type
            features = [{'type': 'Feature', 'geometry': data.get('geometry', data), 'properties': data.get('properties', {})}]
            
        # If reprojection is needed for GeoJSON (requires fiona)
        if target_srid and target_srid != 4326:  # GeoJSON is typically in WGS84
            with fiona.open(file_path) as src:
                dst_crs = CRS.from_epsg(target_srid)
                features = []
                for feat in src:
                    # Transform geometry
                    transformed_geom = transform_geom(
                        src.crs,
                        dst_crs,
                        feat['geometry']
                    )
                    feat['geometry'] = transformed_geom
                    features.append(clean_feature_properties(feat))
    else:
        # For other formats (shp, gpkg, etc.), use fiona
        with fiona.open(file_path) as src:
            # Check if reprojection is needed
            if target_srid and src.crs and 'init' in src.crs:
                src_epsg = int(src.crs['init'].split(':')[1]) if 'epsg:' in src.crs['init'].lower() else None
                if src_epsg != target_srid:
                    # Need to reproject
                    dst_crs = CRS.from_epsg(target_srid)
                    for feat in src:
                        # Transform geometry
                        transformed_geom = transform_geom(
                            src.crs,
                            dst_crs,
                            feat['geometry']
                        )
                        feat['geometry'] = transformed_geom
                        features.append(clean_feature_properties(feat))
                else:
                    # No reprojection needed
                    features = [clean_feature_properties(feat) for feat in src]
            else:
                # No reprojection or no CRS info
                features = [clean_feature_properties(feat) for feat in src]
    
    return features

def create_table_and_indexes(db, table_name, schema='staging'):
    """
    Create table with JSONB schema and indexes
    
    Args:
        db (PostgreSQLDatabase): Database connection instance
        table_name (str): Name of the table to create
        schema (str): Schema name (default: 'staging')
    """
    # Create table with JSONB schema
    create_table_sql = f"""
    CREATE TABLE IF NOT EXISTS {schema}.{table_name} (
        feature_data JSONB,
        load_dttm TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """
    db.execute_command(create_table_sql)
    
    # Create GIN index on feature_data
    gin_index_sql = f"""
    CREATE INDEX IF NOT EXISTS idx_{table_name}_feature_data 
    ON {schema}.{table_name} USING gin(feature_data)
    """
    db.execute_command(gin_index_sql)
    
    # Create B-tree index on load_dttm
    btree_index_sql = f"""
    CREATE INDEX IF NOT EXISTS idx_{table_name}_load_dttm 
    ON {schema}.{table_name}(load_dttm)
    """
    db.execute_command(btree_index_sql)
    
    print(f"Created table {schema}.{table_name} with indexes")

def load_geo_to_postgres(file_path, table_name, db, target_srid=None, schema='staging'):
    """
    Load shapefile or geojson to PostgreSQL with JSONB schema and load timestamp tracking
    
    Args:
        file_path (str): Path to .shp or .geojson file
        table_name (str): Name for the destination table
        db (PostgreSQLDatabase): Instance of PostgreSQLDatabase class
        target_srid (int): Target SRID for the geometry (e.g., 4326 for WGS84)
        schema (str): Database schema (default: 'staging')
    
    Returns:
        bool: True if successful, False otherwise
    """
    
    try:
        # Verify file exists before processing
        if not os.path.exists(file_path):
            print(f"Error: File not found: {file_path}")
            return False
            
        # Read features from geographic file
        features = read_geo_file(file_path, target_srid)
        
        if not features:
            print(f"Warning: No features found in {file_path}")
            return False
        
        # Ensure database connection
        if not db.conn:
            db.connect()
        
        # Check if schema exists, create if not
        db.execute_command(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        
        # Check if table exists using the utility method
        table_exists = db.check_table_exists(table_name, schema)
        
        # Create table and indexes if it doesn't exist
        if not table_exists:
            create_table_and_indexes(db, table_name, schema)
        
        # Prepare data for bulk insert
        # Convert features to JSONB strings and add timestamps
        current_time = datetime.now()
        insert_data = []
        
        for feature in features:
            # Serialize feature to JSON string
            feature_json = json.dumps(feature, default=json_serializable)
            # Add row with feature_data and load_dttm
            insert_data.append([feature_json, current_time])
        
        # Use the generalized insert_data method
        inserted_count = db.insert_data(
            table_name=table_name,
            columns=['feature_data', 'load_dttm'],
            data=insert_data,
            schema=schema,
            show_progress=True,
            batch_size=100,
            commit_interval=1000
        )
        
        print(f"Successfully loaded {inserted_count} features into {schema}.{table_name}")
        return True
        
    except Exception as e:
        print(f"Error processing {file_path}: {e}")
        if db.conn:
            db.conn.rollback()
        return False

def process_file_list(file_list, db, source_dir, geo_dir, target_srid=None, schema='staging'):
    """Process a list of files and their target tables"""
    successful_files = []
    failed_files = []
    
    for item in file_list:
        relative_file_path = item.get('file_path')
        table_name = item.get('table_name')
        
        if relative_file_path and table_name:
            # Build full file path using directories
            full_file_path = build_file_path(source_dir, geo_dir, relative_file_path)
            print(f"\nProcessing {full_file_path} to table {table_name}")
            
            success = load_geo_to_postgres(full_file_path, table_name, db, target_srid, schema)
            
            if success:
                successful_files.append(full_file_path)
                # Archive the file after successful processing
                try:
                    archived_path = archive_file(full_file_path)
                    print(f"Archived {full_file_path} to {archived_path}")
                except Exception as e:
                    print(f"Warning: Failed to archive {full_file_path}: {e}")
            else:
                failed_files.append(full_file_path)
                print(f"Failed to process {full_file_path}, file not archived")
    
    # Print summary
    print("\n" + "="*60)
    print("PROCESSING SUMMARY")
    print("="*60)
    print(f"Successfully processed: {len(successful_files)} files")
    print(f"Failed to process: {len(failed_files)} files")
    
    if failed_files:
        print("\nFailed files:")
        for f in failed_files:
            print(f"  - {f}")

def main():
    # Set up argument parser
    parser = argparse.ArgumentParser(description='Load geographic files to PostgreSQL with JSONB schema')
    parser.add_argument('--dev', action='store_true', help='Use development environment', default=True)
    parser.add_argument('--prd', action='store_true', help='Use production environment', default=False)
    parser.add_argument('--file', type=str, help='Path to the geographic file (relative to SOURCE_DIR/geo_files or absolute)')
    parser.add_argument('--table', type=str, help='Target table name')
    parser.add_argument('--json', type=str, help='JSON file containing file paths and table names (file paths in JSON will be relative to SOURCE_DIR/geo_files)')
    parser.add_argument('--srid', type=int, help='Target SRID for geometry', default=4326)
    parser.add_argument('--schema', type=str, help='Database schema', default='staging')
    
    args = parser.parse_args()
    
    # Determine environment mode
    env_mode = 'prd' if args.prd else 'dev'
    SOURCE_DIR, GEO_DIR = load_environment(env_mode)
    
    # Validate that SOURCE_DIR is set
    if not SOURCE_DIR:
        print("Error: SOURCE_DIR environment variable is not set")
        return 1
    
    print(f"Using environment: {env_mode.upper()}")
    print(f"Source directory: {SOURCE_DIR}")
    print(f"Geo files directory: {GEO_DIR}")
    print(f"Target schema: {args.schema}")
    print(f"Target SRID: {args.srid}")
    print("-" * 60)
    
    # Initialize database connection
    db = PostgreSQLDatabase()
    db.connect()
    
    try:
        # Process based on input method
        if args.json:
            # JSON file path is used as-is (not in the data directory)
            if not os.path.exists(args.json):
                print(f"Error: JSON file not found: {args.json}")
                return 1
                
            # Load file list from JSON
            with open(args.json, 'r') as f:
                file_list = json.load(f)
            
            print(f"Processing {len(file_list)} files from JSON configuration")
            process_file_list(file_list, db, SOURCE_DIR, GEO_DIR, args.srid, args.schema)
            
        elif args.file and args.table:
            # Build full file path using directories
            full_file_path = build_file_path(SOURCE_DIR, GEO_DIR, args.file)
            print(f"Processing single file: {full_file_path}")
            print(f"Target table: {args.table}")
            
            success = load_geo_to_postgres(full_file_path, args.table, db, args.srid, args.schema)
            
            if success:
                # Archive the file after successful processing
                try:
                    archived_path = archive_file(full_file_path)
                    print(f"Archived {full_file_path} to {archived_path}")
                except Exception as e:
                    print(f"Warning: Failed to archive {full_file_path}: {e}")
            else:
                print(f"Failed to process {full_file_path}, file not archived")
                return 1
        else:
            print("Error: Either provide --file and --table or --json arguments")
            parser.print_help()
            return 1
        
        print("\nProcess completed successfully!")
        return 0
        
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        return 1
    finally:
        db.disconnect()

if __name__ == "__main__":
    exit(main())
    