import os
import requests
import json
from dotenv import load_dotenv
from ..utils.db_connection import PostgreSQLDatabase

def get_census_data(api_key):
    """Fetch block group population data for Dane County, WI."""
    # Dane County FIPS: 55025
    base_url = "https://api.census.gov/data/2022/acs/acs5"  # Using 5-year for block group level
    
    # B01003_001E is total population estimate
    params = {
        "get": "NAME,B01003_001E",
        "for": "block group:*",
        "in": "state:55 county:025",
        "key": api_key
    }
    
    response = requests.get(base_url, params=params)
    if response.status_code != 200:
        raise Exception(f"API request failed: {response.status_code}")
    
    data = response.json()
    headers = data[0]
    rows = data[1:]
    
    # Transform into list of JSON objects
    results = []
    for row in rows:
        block_group_data = {
            "name": row[0],
            "population": row[1],
            "state": row[2],
            "county": row[3],
            "tract": row[4],
            "block_group": row[5]
        }
        results.append(json.dumps(block_group_data))
    
    return results

def main():
    load_dotenv()
    api_key = os.getenv('CENSUS_API_KEY')
    if not api_key:
        raise ValueError("Census API key not found in environment variables")
    
    try:
        # Fetch census data
        census_data = get_census_data(api_key)
        
        # Connect to database and insert data
        db = PostgreSQLDatabase()
        db.connect()
        
        # Assuming table exists. If not, you'd need to create it first
        db.insert_data('census_block_group_population', census_data)
        
        print(f"Successfully loaded {len(census_data)} block group records")
        
    except Exception as e:
        print(f"Error: {str(e)}")
    finally:
        db.disconnect()

if __name__ == "__main__":
    main()