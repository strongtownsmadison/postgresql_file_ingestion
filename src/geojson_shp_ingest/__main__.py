import os
import json
import fiona
from fiona.crs import CRS
from fiona.transform import transform_geom
from pathlib import Path
from datetime import datetime, date
from decimal import Decimal
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

def check_year_boundary(db, table_name, schema):
    """
    Check if current year is different from the year of max(load_dttm) in the table
    
    Args:
        db (PostgreSQLDatabase): Database connection
        table_name (str): Table name
        schema (str): Schema name
    
    Returns:
        tuple: (crosses_year_boundary: bool, max_load_year: int or None)
    """
    try:
        query = f"SELECT MAX(load_dttm) FROM {schema}.{table_name}"
        result = db.execute_query(query)
        
        if result and result[0][0]:
            max_load_dttm = result[0][0]
            max_load_year = max_load_dttm.year
            current_year = datetime.now().year
            
            return (current_year > max_load_year, max_load_year)
        else:
            # No data in table
            return (False, None)
    except Exception as e:
        print(f"Error checking year boundary: {e}")
        return (False, None)

def ensure_history_table(db, source_table, schema):
    """
    Ensure history table exists with same structure as source but no DEFAULT on load_dttm
    
    Args:
        db (PostgreSQLDatabase): Database connection
        source_table (str): Source table name
        schema (str): Schema name
    
    Returns:
        str: History table name
    """
    history_table = f"{source_table}_history"
    
    # Check if history table exists
    if db.check_table_exists(history_table, schema):
        print(f"History table {schema}.{history_table} already exists")
        return history_table
    
    print(f"Creating history table {schema}.{history_table}...")
    
    try:
        # Create history table with same structure but no DEFAULT on load_dttm
        create_history_sql = f"""
        CREATE TABLE {schema}.{history_table} (
            feature_data JSONB,
            load_dttm TIMESTAMP  -- No DEFAULT clause to preserve original timestamps
        )
        """
        db.execute_command(create_history_sql)
        
        # Create same indexes as source table
        gin_index_sql = f"""
        CREATE INDEX IF NOT EXISTS idx_{history_table}_feature_data 
        ON {schema}.{history_table} USING gin(feature_data)
        """
        db.execute_command(gin_index_sql)
        
        btree_index_sql = f"""
        CREATE INDEX IF NOT EXISTS idx_{history_table}_load_dttm 
        ON {schema}.{history_table}(load_dttm)
        """
        db.execute_command(btree_index_sql)
        
        print(f"Created history table {schema}.{history_table} with indexes")
        return history_table
        
    except Exception as e:
        print(f"Error creating history table: {e}")
        raise

def archive_to_history(db, source_table, history_table, schema, year):
    """
    Archive current table contents to history table
    
    Args:
        db (PostgreSQLDatabase): Database connection
        source_table (str): Source table name
        history_table (str): History table name
        schema (str): Schema name
        year (int): Year being archived
    
    Returns:
        bool: Success status
    """
    try:
        # Get count of records to archive
        count_query = f"SELECT COUNT(*) FROM {schema}.{source_table}"
        result = db.execute_query(count_query)
        source_count = result[0][0] if result else 0
        
        print(f"Archiving {source_count:,} records from year {year}...")
        
        # Insert all records from source to history
        insert_sql = f"""
        INSERT INTO {schema}.{history_table} 
        SELECT * FROM {schema}.{source_table}
        """
        
        db.execute_command(insert_sql)
        
        # Verify insert count
        verify_query = f"""
        SELECT COUNT(*) FROM {schema}.{history_table} 
        WHERE EXTRACT(YEAR FROM load_dttm) = {year}
        """
        result = db.execute_query(verify_query)
        archived_count = result[0][0] if result else 0
        
        if archived_count > 0:
            print(f"✓ Archived {archived_count:,} records from year {year}")
            return True
        else:
            print(f"Warning: No records were archived")
            return False
            
    except Exception as e:
        print(f"Error during archival: {e}")
        return False

def get_nested_value(data, key_path):
    """
    Navigate any dot-separated path in JSON
    
    Args:
        data (dict): JSON data to navigate
        key_path (str): Dot-separated path (e.g., 'properties.CurrentTotal')
    
    Returns:
        Any: Value at the path or None if not found
    """
    keys = key_path.split('.')
    value = data
    for key in keys:
        if isinstance(value, dict):
            value = value.get(key)
        else:
            return None
        if value is None:
            break
    return value

def calculate_aggregate(values, aggregate_type):
    """
    Apply any aggregate function to a list of values
    
    Args:
        values (list): List of values to aggregate
        aggregate_type (str): Type of aggregation (sum, avg, min, max, count)
    
    Returns:
        float: Aggregated value or None if no valid values
    """
    # Filter to numeric values only
    numeric_values = []
    for v in values:
        if v is not None:
            try:
                numeric_val = float(v)
                numeric_values.append(numeric_val)
            except (TypeError, ValueError):
                continue
    
    if not numeric_values:
        return None
    
    if aggregate_type == 'sum':
        return sum(numeric_values)
    elif aggregate_type == 'avg':
        return sum(numeric_values) / len(numeric_values)
    elif aggregate_type == 'min':
        return min(numeric_values)
    elif aggregate_type == 'max':
        return max(numeric_values)
    elif aggregate_type == 'count':
        return len(numeric_values)
    else:
        raise ValueError(f"Unsupported aggregate type: {aggregate_type}")

def calculate_incoming_metrics(features, validation_config):
    """
    Calculate validation metrics from incoming features
    
    Args:
        features (list): List of GeoJSON features
        validation_config (dict): Validation configuration
    
    Returns:
        dict: Dictionary of calculated metrics
    """
    metrics = {}
    
    # Calculate record count
    metrics['record_count'] = len(features)
    
    # Calculate aggregates if configured
    if 'aggregates' in validation_config:
        for agg_config in validation_config['aggregates']:
            key_path = agg_config['key_path']
            aggregate_type = agg_config.get('aggregate_type', 'sum')
            display_name = agg_config.get('display_name', key_path)
            
            # Extract values from all features
            values = []
            for feature in features:
                value = get_nested_value(feature, key_path)
                if value is not None:
                    values.append(value)
            
            # Calculate aggregate
            agg_value = calculate_aggregate(values, aggregate_type)
            metrics[display_name] = agg_value
    
    return metrics

def query_existing_metrics(db, table_name, schema, validation_config):
    """
    Query existing table for current metrics
    
    Args:
        db (PostgreSQLDatabase): Database connection
        table_name (str): Table name
        schema (str): Schema name
        validation_config (dict): Validation configuration
    
    Returns:
        dict: Dictionary of current metrics or None if table doesn't exist
    """
    # Check if table exists
    if not db.check_table_exists(table_name, schema):
        return None
    
    metrics = {}
    
    # Build dynamic query based on validation config
    select_parts = ["COUNT(*) as record_count"]
    
    if 'aggregates' in validation_config:
        for i, agg_config in enumerate(validation_config['aggregates']):
            key_path = agg_config['key_path']
            aggregate_type = agg_config.get('aggregate_type', 'sum')
            display_name = agg_config.get('display_name', key_path)
            
            # Build JSONB path for query
            json_path_parts = key_path.split('.')
            json_accessor = "feature_data"
            for j, part in enumerate(json_path_parts[:-1]):
                json_accessor += f"->'{part}'"
            # Last part uses ->> to get text value
            json_accessor += f"->>'{json_path_parts[-1]}'"
            
            # Add aggregate to query
            if aggregate_type == 'sum':
                select_parts.append(f"SUM(({json_accessor})::numeric) as agg_{i}")
            elif aggregate_type == 'avg':
                select_parts.append(f"AVG(({json_accessor})::numeric) as agg_{i}")
            elif aggregate_type == 'min':
                select_parts.append(f"MIN(({json_accessor})::numeric) as agg_{i}")
            elif aggregate_type == 'max':
                select_parts.append(f"MAX(({json_accessor})::numeric) as agg_{i}")
            elif aggregate_type == 'count':
                select_parts.append(f"COUNT({json_accessor}) as agg_{i}")
    
    query = f"SELECT {', '.join(select_parts)} FROM {schema}.{table_name}"
    
    try:
        result = db.execute_query(query)
        if result and len(result) > 0:
            row = result[0]
            # Convert count to int
            metrics['record_count'] = int(row[0]) if row[0] is not None else 0
            
            # Map aggregate results to display names, converting Decimal to float
            if 'aggregates' in validation_config:
                for i, agg_config in enumerate(validation_config['aggregates']):
                    display_name = agg_config.get('display_name', agg_config['key_path'])
                    value = row[i + 1]
                    # Convert Decimal to float for consistency
                    if value is not None:
                        try:
                            metrics[display_name] = float(value)
                        except (TypeError, ValueError):
                            metrics[display_name] = None
                    else:
                        metrics[display_name] = None
        
        return metrics
    except Exception as e:
        print(f"Error querying existing metrics: {e}")
        return None

def format_number(value):
    """Format number with thousand separators"""
    if value is None:
        return "N/A"
    
    # Convert Decimal to float for consistent handling
    if isinstance(value, Decimal):
        value = float(value)
    
    if isinstance(value, float):
        # Check if it's effectively an integer
        if value.is_integer():
            return f"{int(value):,}"
        else:
            return f"{value:,.2f}"
    elif isinstance(value, int):
        return f"{value:,}"
    
    # Try to convert to float as a fallback
    try:
        value = float(value)
        if value.is_integer():
            return f"{int(value):,}"
        else:
            return f"{value:,.2f}"
    except (TypeError, ValueError):
        return str(value)

def calculate_change(current, incoming):
    """Calculate change and percentage change"""
    if current is None or incoming is None:
        return None, None
    
    # Convert to float to handle decimal.Decimal from PostgreSQL
    try:
        current_float = float(current)
        incoming_float = float(incoming)
    except (TypeError, ValueError):
        return None, None
    
    change = incoming_float - current_float
    if current_float != 0:
        pct_change = (change / current_float) * 100
    else:
        pct_change = 100 if incoming_float > 0 else 0
    
    return change, pct_change

def display_validation_report(table_name, current_metrics, incoming_metrics, validation_config):
    """
    Display a formatted validation report
    
    Args:
        table_name (str): Name of the table
        current_metrics (dict): Current table metrics (None if table doesn't exist)
        incoming_metrics (dict): Incoming data metrics
        validation_config (dict): Validation configuration
    
    Returns:
        bool: True if changes were detected, False if identical
    """
    print("\n" + "=" * 60)
    print(f"VALIDATION REPORT: {table_name}")
    print("=" * 60)
    
    if current_metrics is None:
        print("Table Status: New table - no existing data to compare")
        print(f"\nIncoming Data:")
        print(f"  Record Count: {format_number(incoming_metrics.get('record_count'))}")
        
        if 'aggregates' in validation_config:
            for agg_config in validation_config['aggregates']:
                display_name = agg_config.get('display_name', agg_config['key_path'])
                value = incoming_metrics.get(display_name)
                print(f"  {display_name}: {format_number(value)}")
        
        return True  # Changes detected (new table)
    else:
        print("Table Status: Existing data found")
        
        changes_detected = False
        
        # Display record count comparison
        print(f"\nRecord Count:")
        current_count = current_metrics.get('record_count', 0)
        incoming_count = incoming_metrics.get('record_count', 0)
        count_change, count_pct = calculate_change(current_count, incoming_count)
        
        print(f"  Current:    {format_number(current_count)}")
        print(f"  Incoming:   {format_number(incoming_count)}")
        
        if count_change is not None:
            sign = "+" if count_change >= 0 else ""
            print(f"  Change:     {sign}{format_number(count_change)} ({sign}{count_pct:.2f}%)")
            if count_change != 0:
                changes_detected = True
        
        # Display aggregate comparisons
        if 'aggregates' in validation_config:
            for agg_config in validation_config['aggregates']:
                display_name = agg_config.get('display_name', agg_config['key_path'])
                current_value = current_metrics.get(display_name)
                incoming_value = incoming_metrics.get(display_name)
                
                print(f"\n{display_name}:")
                print(f"  Current:    {format_number(current_value)}")
                print(f"  Incoming:   {format_number(incoming_value)}")
                
                value_change, value_pct = calculate_change(current_value, incoming_value)
                if value_change is not None:
                    sign = "+" if value_change >= 0 else ""
                    print(f"  Change:     {sign}{format_number(value_change)} ({sign}{value_pct:.2f}%)")
                    if value_change != 0:
                        changes_detected = True
        
        return changes_detected

def prompt_user_approval(auto_approve=False, dry_run=False):
    """
    Prompt user for approval to proceed
    
    Args:
        auto_approve (bool): Skip prompt and return True
        dry_run (bool): If True, mention this is a dry run
    
    Returns:
        bool: True if approved, False otherwise
    """
    if auto_approve:
        print("\n[AUTO-APPROVED]")
        return True
    
    if dry_run:
        print("\n[DRY RUN - No changes will be made]")
        return True
    
    print("\n" + "=" * 60)
    while True:
        response = input("Do you want to proceed with replacing the data? (yes/no): ").strip().lower()
        if response in ['yes', 'y']:
            return True
        elif response in ['no', 'n']:
            return False
        else:
            print("Please enter 'yes' or 'no'")

def load_geo_to_postgres_with_validation(file_path, table_name, db, target_srid=None, schema='staging', 
                                        validation_config=None, auto_approve=False, dry_run=False, 
                                        no_archive=False, no_history=False, force_history=False):
    """
    Load shapefile or geojson to PostgreSQL with validation and approval
    
    Args:
        file_path (str): Path to .shp or .geojson file
        table_name (str): Name for the destination table
        db (PostgreSQLDatabase): Instance of PostgreSQLDatabase class
        target_srid (int): Target SRID for the geometry (e.g., 4326 for WGS84)
        schema (str): Database schema (default: 'staging')
        validation_config (dict): Validation configuration
        auto_approve (bool): Skip manual approval
        dry_run (bool): Show what would change without making changes
        no_archive (bool): Don't archive files after processing
        no_history (bool): Skip history archival even if year boundary detected
        force_history (bool): Force archival regardless of year boundary
    
    Returns:
        tuple: (success: bool, year_archived: bool)
    """
    
    try:
        # Verify file exists before processing
        if not os.path.exists(file_path):
            print(f"Error: File not found: {file_path}")
            return (False, False)
            
        # Read features from geographic file
        print(f"Reading features from {file_path}...")
        features = read_geo_file(file_path, target_srid)
        
        if not features:
            print(f"Warning: No features found in {file_path}")
            return (False, False)
        
        print(f"Found {len(features)} features")
        
        # Ensure database connection
        if not db.conn:
            db.connect()
        
        # Check if schema exists, create if not
        db.execute_command(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        
        # Check if table exists first
        table_exists = db.check_table_exists(table_name, schema)
        
        # STEP 1: VALIDATION
        changes_detected = True  # Default to true for backward compatibility
        
        if validation_config:
            # Calculate metrics from incoming data
            print("Calculating validation metrics...")
            incoming_metrics = calculate_incoming_metrics(features, validation_config)
            
            # Query existing table metrics
            current_metrics = query_existing_metrics(db, table_name, schema, validation_config)
            
            # Display validation report
            changes_detected = display_validation_report(table_name, current_metrics, incoming_metrics, validation_config)
            
            # Note: We proceed even if no changes detected - validation is just a gut check
            # The commented out section has been removed as requested
            
            # Prompt for approval
            if not prompt_user_approval(auto_approve, dry_run):
                print("\nUpdate cancelled by user")
                return (False, False)
        elif not auto_approve and not dry_run:
            # Even without validation config, show basic info and ask for confirmation
            if not table_exists:
                print("\n" + "=" * 60)
                print(f"LOADING SUMMARY: {table_name}")
                print("=" * 60)
                print(f"Table Status: New table will be created")
                print(f"Incoming Records: {len(features):,}")
            else:
                print("\n" + "=" * 60)
                print(f"LOADING SUMMARY: {table_name}")
                print("=" * 60)
                print(f"Table Status: Existing table will be replaced")
                print(f"Incoming Records: {len(features):,}")
            
            if not prompt_user_approval(auto_approve, dry_run):
                print("\nUpdate cancelled by user")
                return (False, False)
        
        # If dry run, stop here
        if dry_run:
            print("\n[DRY RUN COMPLETED - No changes were made]")
            return (True, False)
        
        # STEP 2: YEARLY ARCHIVAL (after validation approval)
        year_archived = False
        if table_exists and not no_history:
            # Check for year boundary crossing
            crosses_year, last_year = check_year_boundary(db, table_name, schema)
            
            # Force history if requested
            if force_history and not crosses_year:
                crosses_year = True
                last_year = datetime.now().year  # Use current year for forced archive
                print(f"\n[FORCE HISTORY: Archiving current data as year {last_year}]")
            
            if crosses_year and last_year is not None:
                print(f"\n{'='*60}")
                print(f"YEAR BOUNDARY DETECTED: {last_year} → {datetime.now().year}")
                print(f"Proceeding with yearly snapshot archival...")
                print(f"{'='*60}")
                
                try:
                    # Start transaction for archive
                    db.execute_command("BEGIN")
                    
                    # Ensure history table exists
                    history_table = ensure_history_table(db, table_name, schema)
                    print(f"History table: {schema}.{history_table}")
                    
                    # Archive current table contents
                    print(f"Archiving {last_year} data before replacement...")
                    success = archive_to_history(db, table_name, history_table, schema, last_year)
                    
                    if success:
                        print(f"✓ Successfully archived year {last_year} snapshot")
                        db.execute_command("COMMIT")
                        year_archived = True
                    else:
                        raise Exception("Archive operation failed")
                    
                except Exception as e:
                    db.execute_command("ROLLBACK")
                    print(f"✗ Archive failed: {e}")
                    # Since we've already validated and approved, ask if they want to continue
                    if not auto_approve:
                        response = input("Continue without archiving? (yes/no): ").strip().lower()
                        if response not in ['yes', 'y']:
                            print("Operation cancelled due to archive failure")
                            return (False, False)
                    else:
                        # In auto-approve mode, fail the operation if archival fails
                        print("Operation cancelled due to archive failure (auto-approve mode)")
                        return (False, False)
        
        # STEP 3: REPLACE DATA
        if table_exists:
            print(f"Truncating existing table {schema}.{table_name}...")
            truncate_sql = f"TRUNCATE TABLE {schema}.{table_name}"
            db.execute_command(truncate_sql)
            print("Table truncated successfully")
        else:
            # Create table and indexes if it doesn't exist
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
        print(f"Loading {len(features)} features into {schema}.{table_name}...")
        inserted_count = db.insert_data(
            table_name=table_name,
            columns=['feature_data', 'load_dttm'],
            data=insert_data,
            schema=schema,
            show_progress=True,
            batch_size=100,
            commit_interval=1000
        )
        
        print(f"✓ Successfully loaded {inserted_count} features into {schema}.{table_name}")
        
        # Archive the file after successful processing (unless disabled)
        if not no_archive:
            try:
                archived_path = archive_file(file_path)
                print(f"✓ Archived {file_path} to {archived_path}")
            except Exception as e:
                print(f"Warning: Failed to archive {file_path}: {e}")
        
        # Return success and whether a year was archived
        return (True, year_archived)
        
    except Exception as e:
        print(f"Error processing {file_path}: {e}")
        if db.conn:
            db.conn.rollback()
        return (False, False)

def process_file_list(file_list, db, source_dir, geo_dir, target_srid=None, schema='staging', 
                     auto_approve=False, dry_run=False, no_archive=False, no_history=False, force_history=False):
    """Process a list of files and their target tables with validation"""
    successful_files = []
    failed_files = []
    skipped_files = []
    archived_tables = []  # Track actual archives that happened
    
    total_files = len(file_list)
    
    for idx, item in enumerate(file_list, 1):
        relative_file_path = item.get('file_path')
        table_name = item.get('table_name')
        validation_config = item.get('validation')
        
        # Check for history configuration in the JSON
        history_config = item.get('history', {})
        item_no_history = no_history or not history_config.get('enabled', True)
        
        if relative_file_path and table_name:
            # Build full file path using directories
            full_file_path = build_file_path(source_dir, geo_dir, relative_file_path)
            
            print(f"\n{'='*60}")
            print(f"Processing file {idx}/{total_files}")
            print(f"File: {full_file_path}")
            print(f"Table: {schema}.{table_name}")
            if item_no_history:
                print(f"History: Disabled")
            print(f"{'='*60}")
            
            result = load_geo_to_postgres_with_validation(
                full_file_path, table_name, db, target_srid, schema,
                validation_config, auto_approve, dry_run, no_archive, 
                item_no_history, force_history
            )
            
            # Handle the tuple return value
            if isinstance(result, tuple):
                success, year_archived = result
                if success and year_archived:
                    # Get the actual year that was archived
                    if db.check_table_exists(table_name, schema):
                        # Query to get the year that was just archived to history
                        history_table = f"{table_name}_history"
                        if db.check_table_exists(history_table, schema):
                            query = f"""
                            SELECT DISTINCT EXTRACT(YEAR FROM load_dttm) as year 
                            FROM {schema}.{history_table} 
                            ORDER BY year DESC 
                            LIMIT 1
                            """
                            try:
                                result_year = db.execute_query(query)
                                if result_year and result_year[0][0]:
                                    archived_year = int(result_year[0][0])
                                    archived_tables.append((table_name, archived_year))
                            except:
                                pass  # Silently ignore if we can't get the year
            else:
                # Backward compatibility if function returns boolean
                success = result
            
            if success:
                successful_files.append(full_file_path)
            else:
                # Check if it was user-cancelled vs error
                if validation_config and not auto_approve:
                    # Might have been cancelled by user
                    skipped_files.append(full_file_path)
                else:
                    failed_files.append(full_file_path)
    
    # Print summary
    print("\n" + "="*60)
    print("PROCESSING SUMMARY")
    print("="*60)
    print(f"Total files processed: {total_files}")
    print(f"✓ Successfully loaded: {len(successful_files)} files")
    if archived_tables:
        print(f"📁 Yearly snapshots created: {len(archived_tables)}")
        for table, year in archived_tables:
            print(f"   - {table}: archived year {year}")
    if skipped_files:
        print(f"⊘ Skipped (user choice): {len(skipped_files)} files")
    if failed_files:
        print(f"✗ Failed to process: {len(failed_files)} files")
    
    if skipped_files:
        print("\nSkipped files:")
        for f in skipped_files:
            print(f"  - {f}")
    
    if failed_files:
        print("\nFailed files:")
        for f in failed_files:
            print(f"  - {f}")

def main():
    # Set up argument parser
    parser = argparse.ArgumentParser(description='Load geographic files to PostgreSQL with validation and yearly snapshot support')
    parser.add_argument('--dev', action='store_true', help='Use development environment', default=True)
    parser.add_argument('--prd', action='store_true', help='Use production environment', default=False)
    parser.add_argument('--file', type=str, help='Path to the geographic file (relative to SOURCE_DIR/geo_files or absolute)')
    parser.add_argument('--table', type=str, help='Target table name')
    parser.add_argument('--json', type=str, help='JSON file containing file paths, table names, and validation rules')
    parser.add_argument('--srid', type=int, help='Target SRID for geometry', default=4326)
    parser.add_argument('--schema', type=str, help='Database schema', default='staging')
    parser.add_argument('--auto-approve', action='store_true', help='Skip manual confirmation (for automation)')
    parser.add_argument('--dry-run', action='store_true', help='Show what would change without making changes')
    parser.add_argument('--no-archive', action='store_true', help="Don't archive files after processing")
    parser.add_argument('--no-history', action='store_true', help='Skip history archival even if year boundary detected')
    parser.add_argument('--force-history', action='store_true', help='Force archival regardless of year boundary (useful for testing)')
    
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
    
    if args.no_history:
        print("History archival: DISABLED")
    elif args.force_history:
        print("History archival: FORCED")
    else:
        print("History archival: Enabled (on year boundary)")
    
    if args.dry_run:
        print("Mode: DRY RUN (no changes will be made)")
    elif args.auto_approve:
        print("Mode: AUTO-APPROVE (no manual confirmation)")
    else:
        print("Mode: INTERACTIVE (manual approval required)")
    
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
            process_file_list(file_list, db, SOURCE_DIR, GEO_DIR, args.srid, args.schema,
                            args.auto_approve, args.dry_run, args.no_archive, args.no_history, args.force_history)
            
        elif args.file and args.table:
            # Build full file path using directories
            full_file_path = build_file_path(SOURCE_DIR, GEO_DIR, args.file)
            print(f"Processing single file: {full_file_path}")
            print(f"Target table: {args.table}")
            
            result = load_geo_to_postgres_with_validation(
                full_file_path, args.table, db, args.srid, args.schema,
                None,  # No validation config for single file mode
                args.auto_approve, args.dry_run, args.no_archive, args.no_history, args.force_history
            )
            
            # Handle tuple or boolean return
            if isinstance(result, tuple):
                success, year_archived = result
            else:
                success = result
            
            if not success:
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