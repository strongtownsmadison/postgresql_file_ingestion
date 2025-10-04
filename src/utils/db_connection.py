import os
import psycopg2
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

def load_env_vars():
    """Load environment variables based on the current environment."""
    env = os.getenv('ENVIRONMENT', 'dev').lower()
    if env not in ['dev', 'prod']:
        raise ValueError(f"Invalid environment: {env}. Must be 'dev' or 'prod'.")
    
    if env == 'dev':
        env_file = ".env.dev"
    if env == 'prod':
        env_file = ".env.prod"
    if not os.path.exists(env_file):
        raise FileNotFoundError(f"Environment file {env_file} not found.")
    load_dotenv(env_file)
    print(f"Loaded {env} environment variables from {env_file}")

class PostgreSQLDatabase:
    def __init__(self):
        load_env_vars()  # Ensure environment variables are loaded
        self.host = os.getenv('DB_HOST')
        self.name = os.getenv('DB_NAME')
        self.user = os.getenv('DB_USER')
        self.password = os.getenv('DB_PASSWORD')
        self.conn = None

    def connect(self):
        """Establish a connection to the database."""
        try:
            self.conn = psycopg2.connect(
                host=self.host,
                database=self.name,
                user=self.user,
                password=self.password
            )
            print("Connected to the database successfully.")
        except psycopg2.Error as e:
            print(f"Unable to connect to the database: {e}")
            raise

    def disconnect(self):
        """Close the database connection."""
        if self.conn:
            self.conn.close()
            print("Database connection closed.")

    def create_engine(self):
        self.pgengine = create_engine(
            f'postgresql://{self.user}:{self.password}@{self.host}/{self.name}'
        )

    def check_table_exists(self, table_name, schema=None):
        """
        Check if a table exists in the database.
        
        Args:
            table_name (str): Name of the table
            schema (str): Schema name (optional, defaults to public)
        
        Returns:
            bool: True if table exists, False otherwise
        """
        if not self.conn:
            raise ConnectionError("Database connection not established. Call connect() first.")
        
        with self.conn.cursor() as cursor:
            if schema:
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_schema = %s
                        AND table_name = %s
                    )
                """, (schema, table_name))
            else:
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = %s
                    )
                """, (table_name,))
            
            table_exists = cursor.fetchone()[0]
        
        return table_exists

    def insert_data(self, table_name, columns, data, schema=None, show_progress=True, 
                   batch_size=100, column_transforms=None):
        """
        Insert data into the specified table with flexible column support.
        
        Args:
            table_name (str): Name of the table
            columns (list): List of column names
            data: Either a single row (list/tuple) or multiple rows (list of lists/tuples)
            schema (str): Schema name (optional)
            show_progress (bool): Show progress bar for bulk inserts
            batch_size (int): Update progress every N records
            column_transforms (dict): Optional dict mapping column names to SQL functions
                                     e.g., {'geometry': 'ST_GeomFromGeoJSON'}
        
        Examples:
            # Single row insert
            db.insert_data('users', ['name', 'email'], ['John', 'john@email.com'])
            
            # Bulk insert
            db.insert_data('users', ['name', 'email'], [
                ['John', 'john@email.com'],
                ['Jane', 'jane@email.com']
            ])
            
            # With schema
            db.insert_data('features', ['feature_data'], [json_data], schema='staging')
            
            # With PostGIS geometry
            db.insert_data('geo_table', ['geometry', 'properties'], 
                          [geojson_str, properties_json],
                          column_transforms={'geometry': 'ST_GeomFromGeoJSON'})
        """
        if not self.conn:
            raise ConnectionError("Database connection not established. Call connect() first.")
        
        # Build the table reference with optional schema
        table_ref = f"{schema}.{table_name}" if schema else table_name
        
        # Build the INSERT statement with optional SQL function transforms
        if column_transforms:
            placeholders = []
            for col in columns:
                if col in column_transforms:
                    # Wrap placeholder with SQL function
                    placeholders.append(f"{column_transforms[col]}(%s)")
                else:
                    placeholders.append("%s")
            placeholders_str = ', '.join(placeholders)
        else:
            placeholders_str = ', '.join(['%s'] * len(columns))
        
        column_names = ', '.join(columns)
        insert_sql = f"INSERT INTO {table_ref} ({column_names}) VALUES ({placeholders_str})"
        
        # Determine if data is single row or multiple rows
        if not data:
            print("No data to insert.")
            return 0
        
        # Check if data is a single row (not a list of lists)
        is_single_row = False
        if not isinstance(data[0], (list, tuple)):
            is_single_row = True
            data = [data]  # Convert to list of lists for uniform processing
        
        total_records = len(data)
        inserted_count = 0
        
        if show_progress and total_records > 1:
            print(f"Inserting {total_records:,} records into {table_ref}...")
        
        try:
            with self.conn.cursor() as cursor:
                for i, row in enumerate(data, 1):
                    cursor.execute(insert_sql, row)
                    inserted_count += 1
                    
                    # Update progress bar
                    if show_progress and total_records > 1:
                        if i % batch_size == 0 or i == total_records:
                            percentage = (i / total_records) * 100
                            bar_length = 50
                            filled_length = int(bar_length * percentage / 100)
                            bar = '█' * filled_length + '░' * (bar_length - filled_length)
                            
                            print(f"\r[{bar}] {percentage:.1f}% ({i:,}/{total_records:,})", 
                                 end='', flush=True)
            
            # Single commit at the end - all-or-nothing
            self.conn.commit()
            
            if show_progress and total_records > 1:
                print(f"\nCompleted! {inserted_count:,} records inserted successfully.")
            elif is_single_row:
                print(f"1 record inserted into {table_ref}")
            
            return inserted_count
            
        except Exception as e:
            self.conn.rollback()
            print(f"\nError inserting data: {e}")
            raise

    def execute_from_file(self, filepath):
        """Execute SQL script from file."""
        if not self.conn:
            raise ConnectionError("Database connection not established. Call connect() first.")
        
        try:
            # Open the SQL file and execute its contents
            with open(filepath, 'r') as sql_file:
                sql_script = sql_file.read()

            with self.conn.cursor() as cursor:
                cursor.execute(sql_script)
                self.conn.commit()
                print("SQL script executed successfully.")

        except Exception as e:
            print(f"Something went wrong: {e}")
            self.conn.rollback()
            raise

    def execute_query(self, query, params=None):
        """
        Execute a SELECT query and return results.
        
        Args:
            query (str): SQL query to execute
            params (tuple): Query parameters (optional)
        
        Returns:
            list: Query results
        """
        if not self.conn:
            raise ConnectionError("Database connection not established. Call connect() first.")
        
        with self.conn.cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchall()

    def execute_command(self, command, params=None):
        """
        Execute a non-SELECT SQL command (INSERT, UPDATE, DELETE, CREATE, etc.).
        
        Args:
            command (str): SQL command to execute
            params (tuple): Command parameters (optional)
        
        Returns:
            int: Number of affected rows (for DML commands)
        """
        if not self.conn:
            raise ConnectionError("Database connection not established. Call connect() first.")
        
        with self.conn.cursor() as cursor:
            cursor.execute(command, params)
            self.conn.commit()
            return cursor.rowcount