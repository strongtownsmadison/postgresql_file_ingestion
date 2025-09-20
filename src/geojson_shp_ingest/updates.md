# Objectives
- Allow for validate-truncate-load approach, where we can compare the record count of the existing table with incoming geojson (also the option to validate other aggregates like totals).
    - If validation passes, we truncate the target table and load in new records.
    - Validation should be manual to start with user approval of truncate/load.
    - We'll also need to have a procedure that archives the current table contents when we enter a new year. This could be determined by the year of load_dttm for the current table being a year earlier.
- Allow geojson API pagination load instead of manual download
