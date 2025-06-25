import logging
import pandas as pd
from sqlalchemy import text
from config import get_sqlalchemy_engine

def insert_data_to_db(df, uploaded_by='system', upload_date=None):
    import datetime
    if upload_date is None:
        upload_date = datetime.datetime.now()
    insert_sql = """
    INSERT INTO printer_logs (
        document_name, user_name, hostname, pages_printed, date, month, week, printer_model, division, location, upload_date, uploaded_by
    ) VALUES (
        :document_name, :user_name, :hostname, :pages_printed, :date, :month, :week, :printer_model, :division, :location, :upload_date, :uploaded_by
    )
    """
    logging.info(f"Starting insert_data_to_db with {len(df)} rows")
    engine = get_sqlalchemy_engine()
    batch_size = 1000
    idx = 0
    total_rows = len(df)

    def truncate_string(s, max_length):
        if s is None:
            return None
        s = str(s)
        if len(s) > max_length:
            logging.warning(f"Truncating string from length {len(s)} to {max_length}: {s}")
            return s[:max_length]
        return s

    while idx < total_rows:
        batch_params = []
        batch_end = min(idx + batch_size, total_rows)
        for i in range(idx, batch_end):
            row = df.iloc[i]
            user_name = truncate_string(row['user name'], 255)
            document_name = truncate_string(row['document name'], 255)
            hostname = truncate_string(row['hostname'], 255)
            pages_printed = int(row['pages printed'])
            date_val = row['date'] if pd.notnull(row['date']) else None
            month = truncate_string(row['month'], 50)
            week = truncate_string(row['week'], 50)
            printer_model = truncate_string(row.get('printer_model', None), 255)
            division = truncate_string(row.get('division', None), 255)
            location = truncate_string(row.get('location', None), 255)

            batch_params.append({
                'document_name': document_name,
                'user_name': user_name,
                'hostname': hostname,
                'pages_printed': pages_printed,
                'date': date_val,
                'month': month,
                'week': week,
                'printer_model': printer_model,
                'division': division,
                'location': location,
                'upload_date': upload_date,
                'uploaded_by': uploaded_by
            })

        with engine.begin() as conn:
            conn.execute(text(insert_sql), batch_params)
        idx = batch_end

    engine.dispose()
    logging.info(f"Inserted {total_rows} rows into 'printer_logs' table successfully.")
    return total_rows
