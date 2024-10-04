import os
import psycopg2
from datetime import datetime
from dotenv import load_dotenv

def load_env_vars():
    """Load environment variables based on the current environment."""
    env = os.getenv('ENVIRONMENT', 'dev').lower()
    if env not in ['dev', 'prod']:
        raise ValueError(f"Invalid environment: {env}. Must be 'dev' or 'prod'.")
    
    if env == 'dev':
        env_file = ".env.dev"
        if not os.path.exists(env_file):
            raise FileNotFoundError(f"Environment file {env_file} not found.")
        load_dotenv(env_file)
        print(f"Loaded development environment variables from {env_file}")
    else:  # prod
        # In production, we assume the secrets are set as environment variables
        # typically done through GitHub Secrets and Actions
        required_vars = ['DB_HOST', 'DB_NAME', 'DB_USER', 'DB_PASSWORD']
        missing_vars = [var for var in required_vars if not os.getenv(var)]
        if missing_vars:
            raise EnvironmentError(f"Missing required environment variables: {', '.join(missing_vars)}")
        print("Using production environment variables from GitHub Secrets")

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

    def insert_data(self, table_name, data):
        """Insert data into the specified table."""
        if not self.conn:
            raise ConnectionError("Database connection not established. Call connect() first.")

        with self.conn.cursor() as cur:
            for row in data:
                cur.execute(
                    f"INSERT INTO {table_name} (data_json, load_dttm) VALUES (%s, %s)",
                    (row, datetime.now())
                )
        self.conn.commit()
    
    def execute_from_file(self,filepath):
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