CREATE INDEX IF NOT EXISTS idx_tax_roll_xlsx_data_json 
    ON staging.tax_roll_xlsx USING gin(data_json);