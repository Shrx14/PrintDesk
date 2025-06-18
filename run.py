from flask import Flask, request, render_template, redirect, url_for, flash
import pandas as pd
from io import BytesIO
import pyodbc

SQL_DRIVER = '{ODBC Driver 18 for SQL Server}'
SQL_SERVER = '10.3.2.121,63973'
SQL_DATABASE = 'PrintDesk'
SQL_USERNAME = 'sa'
SQL_PASSWORD = 'Sql@2025'

def get_db_connection():
    conn_str = (
        f'DRIVER={SQL_DRIVER};'
        f'SERVER={SQL_SERVER};'
        f'DATABASE={SQL_DATABASE};'
        f'UID={SQL_USERNAME};'
        f'PWD={SQL_PASSWORD};'
        'TrustServerCertificate=yes;'
    )
    return pyodbc.connect(conn_str)

app = Flask(__name__)
app.secret_key = 'supersecretkey'

import logging

def create_table_if_not_exists():
    create_table_sql = """
    IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='printer_logs' AND xtype='U')
    CREATE TABLE printer_logs (
        id INT IDENTITY(1,1) PRIMARY KEY,
        user_name NVARCHAR(255),
        document_name NVARCHAR(255),
        hostname NVARCHAR(255),
        pages_printed INT,
        date DATETIME,
        month NVARCHAR(50),
        week_1 NVARCHAR(50),
        printer_model NVARCHAR(255),
        division NVARCHAR(255),
        location NVARCHAR(255),
        CONSTRAINT unique_all_columns UNIQUE (
            user_name, document_name, hostname, pages_printed, date, month, week_1, printer_model, division, location
        )
    )
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(create_table_sql)
        conn.commit()
        cursor.close()
        conn.close()
        logging.info("Table 'printer_logs' checked/created successfully.")
    except Exception as e:
        logging.error(f"Error creating table: {e}")

def insert_data_to_db(df):
    duplicates = []
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        check_sql = """
        SELECT COUNT(*) FROM printer_logs 
        WHERE user_name = ? AND document_name = ? AND hostname = ? AND pages_printed = ? AND date = ? AND month = ? AND week_1 = ? AND printer_model = ? AND division = ? AND location = ?
        """
        insert_sql = """
        INSERT INTO printer_logs 
        (document_name, user_name, hostname, pages_printed, date, month, week_1, printer_model, division, location)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        for _, row in df.iterrows():
            user_name = row['user name']
            document_name = row['document name']
            hostname = row['hostname']
            pages_printed = int(row['pages printed'])
            date_val = row['date'].strftime('%Y-%m-%d %H:%M:%S') if pd.notnull(row['date']) else None
            month = row['month']
            week_1 = row['week 1']
            printer_model = row['printer model']
            division = row['division']
            location = row['location']

            cursor.execute(check_sql, (user_name, document_name, hostname, pages_printed, date_val, month, week_1, printer_model, division, location))
            count = cursor.fetchone()[0]

            if count > 0:
                duplicates.append({
                    'user_name': user_name,
                    'document_name': document_name,
                    'hostname': hostname,
                    'pages_printed': pages_printed,
                    'date': date_val,
                    'month': month,
                    'week_1': week_1,
                    'printer_model': printer_model,
                    'division': division,
                    'location': location
                })
            else:
                cursor.execute(insert_sql, (
                    document_name, user_name, hostname, 
                    pages_printed, date_val,
                    month, week_1, printer_model, division, location
                ))
        conn.commit()
        cursor.close()
        conn.close()
        logging.info(f"Inserted {len(df) - len(duplicates)} rows into 'printer_logs' table successfully. {len(duplicates)} duplicates skipped.")
        return duplicates
    except Exception as e:
        logging.error(f"Error inserting data into database: {e}")
        return None

@app.route('/')
def home():
    return render_template("home.html")

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        file = request.files.get('file')
        if not file:
            flash('No file uploaded.')
            return redirect(url_for('upload'))

        try:
            import logging
            # Read Excel file into DataFrame
            in_memory_file = BytesIO(file.read())
            df = pd.read_excel(in_memory_file)

            # Normalize column names (strip and lowercase)
            df.columns = [col.strip().lower() for col in df.columns]

            # Log DataFrame head and dtypes for debugging
            logging.info(f"DataFrame head after reading Excel:\\n{df.head()}")
            logging.info(f"DataFrame dtypes:\\n{df.dtypes}")

            # Expected columns based on your Excel sheet
            expected_cols = {
                'document name', 'user name', 'hostname', 'pages printed',
                'date', 'month', 'week 1', 'printer model', 'division', 'location'
            }

            if not expected_cols.issubset(set(df.columns)):
                flash(f"Excel file must contain columns: {expected_cols}")
                return redirect(url_for('upload'))

            # Preprocess date column: strip whitespace and convert to string
            df['date'] = df['date'].astype(str).str.strip()

            # Define a list of date formats to try
            date_formats = [
                '%Y-%m-%d %H:%M:%S',
                '%d-%m-%Y %H:%M:%S',
                '%Y-%m-%d',
                '%d-%m-%Y',
                '%m/%d/%Y',
                '%d/%m/%Y',
                '%b %d %Y',
                '%B %d %Y',
                '%b %d, %Y',
                '%B %d, %Y',
                '%Y/%m/%d',
                '%m-%d-%Y',
                '%d %b %Y',
                '%d %B %Y',
                '%Y.%m.%d',
                '%d.%m.%Y',
                '%Y%m%d',
            ]

            def try_parsing_date(text):
                from datetime import datetime
                for fmt in date_formats:
                    try:
                        return datetime.strptime(text, fmt)
                    except Exception:
                        continue
                return pd.NaT

            # Apply custom date parsing
            df['date'] = df['date'].apply(try_parsing_date)

            # Log date column after parsing
            logging.info(f"Date column after custom parsing:\\n{df['date'].head()}")

            # Identify invalid dates
            initial_row_count = len(df)
            invalid_dates = df[df['date'].isna()]
            if not invalid_dates.empty:
                invalid_date_values = invalid_dates['date'].astype(str).tolist()
                logging.warning(f"Rows with invalid dates:\\n{invalid_date_values[:20]}")
                flash(f"{len(invalid_date_values)} rows with invalid dates were skipped. Invalid dates: {invalid_date_values[:5]} ...")
            df = df.dropna(subset=['date'])
            dropped_rows = initial_row_count - len(df)

            # Removed redundant flash for dropped_rows since detailed flash above
            # if dropped_rows > 0:
            #     flash(f"{dropped_rows} rows with invalid dates were skipped.")

            # Convert 'pages printed' to numeric
            df['pages printed'] = pd.to_numeric(df['pages printed'], errors='coerce')
            df = df.dropna(subset=['pages printed'])

            # Create table if not exists
            create_table_if_not_exists()

            # Insert data into database
            duplicates = insert_data_to_db(df)

            if duplicates and len(duplicates) > 0:
                duplicate_msgs = [f"Duplicate entry skipped: Document '{d['document_name']}', User '{d['user_name']}', Host '{d['hostname']}', Date '{d['date']}'" for d in duplicates]
                for msg in duplicate_msgs:
                    flash(msg)

            flash('File uploaded and data saved to database successfully!')
            return redirect(url_for('view'))

        except Exception as e:
            flash(f"Error processing file: {e}")
            return redirect(url_for('upload'))

    return render_template('upload.html')

@app.route('/view')
def view():
    try:
        conn = get_db_connection()
        query = "SELECT document_name, user_name, hostname, pages_printed, date, month, week_1, printer_model, division, location FROM printer_logs"
        df = pd.read_sql_query(query, conn)
        conn.close()
        # Format date column as string for display with date and time
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        flash(f"Error retrieving data from database: {e}")
        df = None

    return render_template('view.html', data=df)

@app.route('/dashboard')
def dashboard():
    # Keeping existing dashboard logic unchanged for now
    return render_template('dashboard.html')

if __name__ == '__main__':
    app.run(debug=True)
