from flask import Flask, request, render_template, redirect, url_for, flash
import pandas as pd
from io import BytesIO
import pyodbc
from sqlalchemy import create_engine, text
import urllib

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

from sqlalchemy.pool import QueuePool

def get_sqlalchemy_engine():
    params = urllib.parse.quote_plus(
        f'DRIVER={SQL_DRIVER};'
        f'SERVER={SQL_SERVER};'
        f'DATABASE={SQL_DATABASE};'
        f'UID={SQL_USERNAME};'
        f'PWD={SQL_PASSWORD};'
        'TrustServerCertificate=yes;'
    )
    engine = create_engine(
        f'mssql+pyodbc:///?odbc_connect={params}',
        poolclass=QueuePool,
        pool_size=10,
        max_overflow=20,
        pool_timeout=30,
        pool_recycle=1800,
        connect_args={'timeout': 30}
    )
    return engine

import logging

logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
app.secret_key = 'supersecretkey'

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
        week NVARCHAR(50),
        printer_model NVARCHAR(255),
        division NVARCHAR(255),
        location NVARCHAR(255),
        CONSTRAINT unique_all_columns UNIQUE (
            user_name, document_name, hostname, pages_printed, date, month, week, printer_model, division, location
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

def create_printer_data_table_if_not_exists():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Check if table exists
        cursor.execute("SELECT * FROM sysobjects WHERE name='printer_data' AND xtype='U'")
        result = cursor.fetchone()
        if not result:
            create_table_sql = """
            CREATE TABLE printer_data (
                id INT IDENTITY(1,1) PRIMARY KEY,
                hostname NVARCHAR(255) UNIQUE,
                division NVARCHAR(255),
                printer_model NVARCHAR(255),
                location NVARCHAR(255)
            )
            """
            cursor.execute(create_table_sql)
            conn.commit()
            logging.info("Table 'printer_data' created successfully.")
        else:
            logging.info("Table 'printer_data' already exists.")
        cursor.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error creating printer_data table: {e}")
        from flask import flash
        flash(f"Error creating printer_data table: {e}")

import time

def insert_data_to_db(df):
    insert_sql = """
    INSERT INTO printer_logs (
        document_name, user_name, hostname, pages_printed, date, month, week, printer_model, division, location
    ) VALUES (
        :document_name, :user_name, :hostname, :pages_printed, :date, :month, :week, :printer_model, :division, :location
    )
    """
    duplicates = []
    logging.info(f"Starting insert_data_to_db with {len(df)} rows")
    engine = get_sqlalchemy_engine()
    batch_size = 1000
    batch_params = []
    # Filter out rows with missing or empty hostname
    df_filtered = df[df['hostname'].notnull() & (df['hostname'] != '')]

    # Prepare a set of existing keys to check duplicates in bulk
    existing_keys = set()
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT user_name, document_name, hostname, pages_printed, date, month, week, printer_model, division, location FROM printer_logs"))
            rows = result.fetchall()
            for r in rows:
                # r contains all columns in unique constraint
                existing_keys.add((
                    r[0],  # user_name
                    r[1],  # document_name
                    r[2],  # hostname
                    r[3],  # pages_printed
                    r[4],  # date
                    r[5],  # month
                    r[6],  # week
                    r[7],  # printer_model
                    r[8],  # division
                    r[9],  # location
                ))
    except Exception as e:
        logging.error(f"Error fetching existing keys: {e}")

    inserted_count = 0
    last_inserted_idx = -1  # Track last inserted row index for resume on failure

    def execute_with_retry(func, max_retries=5, delay=10):
        for attempt in range(max_retries):
            try:
                return func()
            except Exception as e:
                if '08S01' in str(e) or 'Communication link failure' in str(e):
                    logging.warning(f"Transient connection error on attempt {attempt+1}/{max_retries}: {e}")
                    time.sleep(delay * (2 ** attempt))  # Exponential backoff with longer base delay
                else:
                    raise
        raise Exception("Max retries exceeded for database operation due to connection issues.")

    def truncate_string(s, max_length):
        if s is None:
            return None
        s = str(s)
        if len(s) > max_length:
            logging.warning(f"Truncating string from length {len(s)} to {max_length}: {s}")
            return s[:max_length]
        return s

    idx = 0
    total_rows = len(df_filtered)
    while idx < total_rows:
        batch_params = []
        batch_end = min(idx + batch_size, total_rows)
        for i in range(idx, batch_end):
            row = df_filtered.iloc[i]
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

            # Calculate original Excel row number (considering header rows)
            original_row_num = row.name + 2  # +2 to account for header row and 0-based index

            # Log lengths of string fields to detect truncation issues
            logging.debug(f"Row {i} (Excel row {original_row_num}) lengths: user_name={len(str(user_name))}, document_name={len(str(document_name))}, hostname={len(str(hostname))}, month={len(str(month))}, week={len(str(week))}, printer_model={len(str(printer_model))}, division={len(str(division))}, location={len(str(location))}")

            key = (
                user_name,
                document_name,
                hostname,
                pages_printed,
                date_val,
                month,
                week,
                printer_model,
                division,
                location,
            )
            if key in existing_keys:
                duplicates.append({
                    'user_name': user_name,
                    'document_name': document_name,
                    'hostname': hostname,
                    'pages_printed': pages_printed,
                    'date': date_val,
                    'month': month,
                    'week': week,
                    'printer_model': printer_model,
                    'division': division,
                    'location': location
                })
                logging.info(f"Row {i} (Excel row {original_row_num}): Duplicate found, skipping insert.")
            else:
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
                    'location': location
                })

        try:
            def insert_batch():
                with engine.begin() as conn:
                    conn.execute(text(insert_sql), batch_params)
            execute_with_retry(insert_batch)
            inserted_count += len(batch_params)
            last_inserted_idx = batch_end - 1
            idx = batch_end
        except Exception as e:
            logging.error(f"Batch insert error: {e}. Falling back to row-by-row insert.")
            # Row-by-row insert with resume support
            for i in range(idx, total_rows):
                row = df_filtered.iloc[i]
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

                original_row_num = row.name + 2

                key = (
                    user_name,
                    document_name,
                    hostname,
                    pages_printed,
                    date_val,
                    month,
                    week,
                    printer_model,
                    division,
                    location,
                )
                if key in existing_keys:
                    duplicates.append({
                        'user_name': user_name,
                        'document_name': document_name,
                        'hostname': hostname,
                        'pages_printed': pages_printed,
                        'date': date_val,
                        'month': month,
                        'week': week,
                        'printer_model': printer_model,
                        'division': division,
                        'location': location
                    })
                    logging.info(f"Row {i} (Excel row {original_row_num}): Duplicate found, skipping insert.")
                    continue

                try:
                    def insert_row():
                        with engine.begin() as conn:
                            conn.execute(text(insert_sql), {
                                'document_name': document_name,
                                'user_name': user_name,
                                'hostname': hostname,
                                'pages_printed': pages_printed,
                                'date': date_val,
                                'month': month,
                                'week': week,
                                'printer_model': printer_model,
                                'division': division,
                                'location': location
                            })
                    execute_with_retry(insert_row)
                    inserted_count += 1
                    last_inserted_idx = i
                except Exception as ex:
                    if 'unique_all_columns' in str(ex):
                        logging.info(f"Row-by-row insert: Duplicate detected, skipping.")
                    elif '22001' in str(ex) or 'String or binary data would be truncated' in str(ex):
                        logging.warning(f"Row-by-row insert: Data too long, skipping row {i}. Data: {(document_name, user_name, hostname, pages_printed, date_val, month, week, printer_model, division, location)}")
                    else:
                        logging.error(f"Row-by-row insert error: {ex}")
                        raise ex
            break

    logging.info(f"Inserted {inserted_count} rows into 'printer_logs' table successfully. {len(duplicates)} duplicates skipped.")
    return duplicates

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
            # Read Excel file into DataFrame with flexible header row detection
            in_memory_file = BytesIO(file.read())

            import logging

            expected_cols = {'document name', 'user name', 'hostname', 'pages printed', 'date'}

            # Try reading with multiple header rows to find expected columns
            for header_row in [0, 1, 2]:
                in_memory_file.seek(0)
                df = pd.read_excel(in_memory_file, header=header_row)
                logging.info(f"Columns read from Excel (header={header_row}): {df.columns.tolist()}")
                df.columns = [col.strip().lower() for col in df.columns]
                if expected_cols.issubset(set(df.columns)):
                    break
            else:
                flash(f"Excel file must contain columns: {expected_cols}")
                return redirect(url_for('upload'))

            # Removed limit rows to 5000 to avoid connection issues
            # max_rows = 5000
            # if len(df) > max_rows:
            #     flash(f"Upload file exceeds maximum allowed rows of {max_rows}. Please split your file and upload in parts.")
            #     return redirect(url_for('upload'))

            # Normalize column names (strip and lowercase)
            df.columns = [col.strip().lower() for col in df.columns]
            import logging
            logging.info(f"Normalized columns: {df.columns.tolist()}")
            # Expected columns for printer logs
            expected_cols = {
                'document name', 'user name', 'hostname', 'pages printed', 'date'
            }

            if not expected_cols.issubset(set(df.columns)):
                flash(f"Excel file must contain columns: {expected_cols}")
                return redirect(url_for('upload'))

            # Preprocess date column: strip whitespace and convert to string
            df['date'] = df['date'].astype(str).str.strip()

            # Clean string columns to remove/normalize special or hidden characters
            import re
            def clean_string(s):
                if pd.isna(s):
                    return None
                # Remove non-printable characters
                s = re.sub(r'[\x00-\x1F\x7F-\x9F]', '', str(s))
                # Normalize whitespace
                s = re.sub(r'\s+', ' ', s).strip()
                return s

            for col in ['user name', 'document name', 'hostname', 'month', 'week', 'printer_model', 'division', 'location']:
                if col in df.columns:
                    df[col] = df[col].apply(clean_string)

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

            # Convert 'pages printed' to numeric
            df['pages printed'] = pd.to_numeric(df['pages printed'], errors='coerce')
            df = df.dropna(subset=['pages printed'])

            # Derive 'month' column formatted as abbreviated month name + apostrophe + last two digits of year (e.g., May'25)
            try:
                if 'month' not in df.columns or df['month'].isnull().all():
                    df['month'] = df['date'].dt.strftime("%b'%y")
            except Exception as e:
                import logging
                logging.error(f"Error deriving 'month' column: {e}")
                flash(f"Error deriving 'month' column: {e}")
                return redirect(url_for('upload'))

            # Derive 'week_1' column formatted as "Week " + week number in the month (e.g., Week 2)
            def week_of_month(dt):
                first_day = dt.replace(day=1)
                dom = dt.day
                adjusted_dom = dom + first_day.weekday()
                return int((adjusted_dom - 1) / 7) + 1

            df['week'] = df['date'].apply(week_of_month)
            df['week'] = df['week'].apply(lambda x: f"Week {x}")

            # Query printer_data table from database to get printer model, division, location by matching hostname
            engine = get_sqlalchemy_engine()
            query = "SELECT hostname, division, printer_model, location FROM printer_data"
            printer_data_db = pd.read_sql_query(query, engine)
            engine.dispose()

            # Normalize hostname columns for matching
            printer_data_db['hostname'] = printer_data_db['hostname'].str.strip().str.lower()
            df['hostname'] = df['hostname'].str.strip().str.lower()

            # Merge uploaded data with printer data from database on hostname
            df = df.merge(printer_data_db, on='hostname', how='left')

            # Allow missing printer data without blocking upload
            # Suppress warning about missing printer data for hostnames
            # missing_printer_data = df[df['printer_model'].isna()]
            # if not missing_printer_data.empty:
            #     missing_hosts = missing_printer_data['hostname'].unique()
            #     logging.warning(f"Missing printer data for hostnames: {missing_hosts}")
            #     flash(f"Warning: Missing printer data for hostnames: {', '.join(missing_hosts[:5])}...")

            # Remove fillna calls to avoid error, keep NaN for missing values
            # df['printer_model'] = df['printer_model'].fillna(value=None)
            # df['division'] = df['division'].fillna(value=None)
            # df['location'] = df['location'].fillna(value=None)

            # Create table if not exists
            create_table_if_not_exists()

            # Rename 'week 1' column to 'week_1' to match database schema
            if 'week 1' in df.columns:
                df.rename(columns={'week 1': 'week'}, inplace=True)

            # Replace NaN in printer_model, division, location with None for DB insertion
            df['printer_model'] = df['printer_model'].where(pd.notnull(df['printer_model']), None)
            df['division'] = df['division'].where(pd.notnull(df['division']), None)
            df['location'] = df['location'].where(pd.notnull(df['location']), None)

            # Log derived month and week_1 columns before insertion
            logging.info(f"Derived 'month' column sample data:\\n{df['month'].head()}")
            logging.info(f"Derived 'week' column sample data:\\n{df['week'].head()}")

            # Insert data into database in chunks of 5000 rows
            try:
                duplicates = []
                chunk_size = 5000
                for start in range(0, len(df), chunk_size):
                    chunk = df.iloc[start:start+chunk_size]
                    chunk_duplicates = insert_data_to_db(chunk)
                    duplicates.extend(chunk_duplicates)
            except Exception as e:
                flash(f"Error inserting data into database: {e}")
                logging.error(f"Error inserting data into database: {e}")
                return redirect(url_for('upload'))

            if duplicates and len(duplicates) > 0:
                flash(f"{len(duplicates)} duplicate entries skipped.")

            flash('File uploaded and data saved to database successfully!')
            return redirect(url_for('view'))

        except Exception as e:
            flash(f"Error processing file: {e}")
            return redirect(url_for('upload'))

    return render_template('upload.html')


from flask import send_file
import io

@app.route('/view')
def view():
    page = request.args.get('page', default=1, type=int)
    per_page = 100
    offset = (page - 1) * per_page

    # Define valid filterable columns
    columns = [
        "document_name", "user_name", "hostname", "pages_printed", "date",
        "month", "week", "printer_model", "division", "location"
    ]

    filters = []
    params = {}

    # Handle global search across all columns
    search_term = request.args.get("search", "").strip()
    if search_term:
        search_clauses = [f"{col} LIKE :search" for col in columns]
        filters.append("(" + " OR ".join(search_clauses) + ")")
        params["search"] = f"%{search_term}%"

    # Handle column-specific filters
    for col in columns:
        val = request.args.get(col)
        if val:
            if col == 'date':
                # Partial match with LIKE for dates to catch substrings like '2025-06-18'
                filters.append(f"{col} LIKE :{col}")
                params[col] = f"%{val}%"
            else:
                # Exact match for other columns
                filters.append(f"{col} = :{col}")
                params[col] = val


    where_clause = "WHERE " + " AND ".join(filters) if filters else ""

    try:
        engine = get_sqlalchemy_engine()

        unique_values = {}
        with engine.connect() as conn:
            # Get unique values for filter dropdowns
            for col in columns:
                result = conn.execute(text(f"SELECT DISTINCT {col} FROM printer_logs ORDER BY {col}"))
                unique_values[col] = [str(row[0]) for row in result if row[0] is not None]

            # Get total row count for pagination
            count_query = f"SELECT COUNT(*) FROM printer_logs {where_clause}"
            total_rows = conn.execute(text(count_query), params).scalar()
            total_pages = (total_rows + per_page - 1) // per_page

            # Fetch paginated data
            query = f"""
                SELECT document_name, user_name, hostname, pages_printed, date, month,
                       week, printer_model, division, location
                FROM printer_logs
                {where_clause}
                ORDER BY date DESC
                OFFSET {offset} ROWS FETCH NEXT {per_page} ROWS ONLY
            """
            df = pd.read_sql_query(text(query), conn, params=params)

        engine.dispose()

        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.strftime('%Y-%m-%d %H:%M:%S')

    except Exception as e:
        flash(f"Error retrieving data: {e}")
        df = None
        unique_values = {col: [] for col in columns}
        total_pages = 0

    return render_template(
        'view.html',
        data=df,
        page=page,
        total_pages=total_pages,
        unique_values=unique_values,
        filters_applied=bool(params)
    )

@app.route('/download')
def download_excel():
    # Same columns and filter logic
    columns = [
        "document_name", "user_name", "hostname", "pages_printed", "date",
        "month", "week", "printer_model", "division", "location"
    ]

    filters = []
    params = {}

    for col in columns:
        val = request.args.get(col)
        if val:
            filters.append(f"{col} = :{col}")
            params[col] = val

    where_clause = "WHERE " + " AND ".join(filters) if filters else ""

    try:
        engine = get_sqlalchemy_engine()
        query = f"""
            SELECT document_name, user_name, hostname, pages_printed, date, month,
                   week, printer_model, division, location
            FROM printer_logs
            {where_clause}
            ORDER BY date DESC
        """
        df = pd.read_sql_query(text(query), engine, params=params)
        engine.dispose()

        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.strftime('%Y-%m-%d %H:%M:%S')

        # Write to Excel in memory
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, sheet_name='FilteredData', index=False)
        output.seek(0)

        return send_file(
            output,
            as_attachment=True,
            download_name='printer_logs_filtered.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        flash(f"Error generating Excel: {e}")
        return redirect(url_for('view'))

@app.route('/dashboard')
def dashboard():
    import datetime
    import traceback
    import logging
    from flask import flash, request, render_template

    # Parse filters from query parameters
    time_filter = request.args.get('time_filter')
    location_filter = request.args.get('location_filter')
    division_filter = request.args.get('division_filter')
    date_input = request.args.get('date_input')
    month_input = request.args.get('month_input')
    year_input = request.args.get('year_input')
    week_select = request.args.get('week_select')

    # If no filters provided (i.e., first visit), set default: last month + PT + monthly
    if not request.args:
        now = datetime.datetime.now()
        last_month_date = now.replace(day=1) - datetime.timedelta(days=1)
        month_input = last_month_date.strftime('%Y-%m')
        time_filter = 'monthly'
        division_filter = 'PT'

    try:
        engine = get_sqlalchemy_engine()
        query = "SELECT user_name, printer_model, hostname, location, division, pages_printed, date, month, week FROM printer_logs"
        df = pd.read_sql_query(query, engine)
        engine.dispose()

        df['date'] = pd.to_datetime(df['date'], errors='coerce')

        logging.info(f"Filters => time: {time_filter}, date: {date_input}, month: {month_input}, year: {year_input}, week: {week_select}, location: {location_filter}, division: {division_filter}")

        # Apply location filter
        if location_filter and location_filter != 'all':
            df = df[df['location'] == location_filter]

        # Apply division filter
        if division_filter and division_filter != 'all':
            df = df[df['division'] == division_filter]

        # Apply time filters
        if time_filter == 'daily' and date_input:
            filter_date = pd.to_datetime(date_input, errors='coerce')
            if not pd.isna(filter_date):
                df = df[df['date'].dt.date == filter_date.date()]
        elif time_filter == 'weekly' and month_input and week_select:
            filter_date = pd.to_datetime(month_input, errors='coerce')
            if not pd.isna(filter_date):
                month_str = filter_date.strftime("%b'%y")
                week_str = f"Week {week_select}"
                df = df[(df['month'] == month_str) & (df['week'] == week_str)]
        elif time_filter == 'monthly' and month_input:
            filter_date = pd.to_datetime(month_input, errors='coerce')
            if not pd.isna(filter_date):
                month_str = filter_date.strftime("%b'%y")
                df = df[df['month'] == month_str]
        elif time_filter == 'yearly' and year_input:
            try:
                year_int = int(year_input)
                df = df[df['date'].dt.year == year_int]
            except ValueError:
                pass

        # Top users (same as before)
        top_users = df.groupby('user_name')['pages_printed'].sum().sort_values(ascending=False).head(10).to_dict()

        # Helper to build printer info dict with hostname and location
        def get_printer_info(printer_series):
            info = {}
            for printer_model, pages in printer_series.items():
                subset = df[df['printer_model'] == printer_model]
                hostname_mode = subset['hostname'].mode()
                hostname = hostname_mode.iloc[0] if not hostname_mode.empty else ''
                location_mode = subset['location'].mode()
                location = location_mode.iloc[0] if not location_mode.empty else ''
                info[printer_model] = {
                    'pages_printed': pages,
                    'hostname': hostname,
                    'location': location
                }
            return info

        # Get top printers and least printers with additional info
        printer_pages_desc = df.groupby('printer_model')['pages_printed'].sum().sort_values(ascending=False).head(10)
        printer_pages_asc = df.groupby('printer_model')['pages_printed'].sum().sort_values(ascending=True).head(10)

        top_printers = get_printer_info(printer_pages_desc)
        least_printers = get_printer_info(printer_pages_asc)

        locations = sorted(df['location'].dropna().unique())
        data = df

        if data.empty:
            flash("No data found for the selected filters.")

    except Exception:
        logging.error("Error in dashboard route:")
        logging.error(traceback.format_exc())
        top_users = {}
        top_printers = {}
        least_printers = {}
        locations = []
        data = pd.DataFrame()

    return render_template('dashboard.html',
                           top_users=top_users,
                           top_printers=top_printers,
                           least_printers=least_printers,
                           locations=locations,
                           data=data,
                           time_filter=time_filter,
                           location_filter=location_filter or 'all',
                           division_filter=division_filter or 'PT',
                           date_input=date_input,
                           month_input=month_input,
                           year_input=year_input,
                           week_select=week_select)



from flask import send_file
import io

@app.route('/dashboard/export')
def dashboard_export():
    import datetime

    # Get filter values
    time_filter = request.args.get('time_filter', 'all')
    location_filter = request.args.get('location_filter', 'all')
    division_filter = request.args.get('division_filter', 'all')
    date_input = request.args.get('date_input')
    month_input = request.args.get('month_input')
    year_input = request.args.get('year_input')
    week_select = request.args.get('week_select')

    try:
        # Load data
        engine = get_sqlalchemy_engine()
        query = """
            SELECT user_name, printer_model, hostname, location, division, pages_printed, date, month, week
            FROM printer_logs
        """
        df = pd.read_sql_query(query, engine)
        engine.dispose()

        df['date'] = pd.to_datetime(df['date'], errors='coerce')

        # Apply filters
        if location_filter != 'all':
            df = df[df['location'] == location_filter]

        if division_filter != 'all':
            df = df[df['division'] == division_filter]

        if time_filter == 'daily' and date_input:
            filter_date = pd.to_datetime(date_input, errors='coerce')
            if not pd.isna(filter_date):
                df = df[df['date'].dt.date == filter_date.date()]
        elif time_filter == 'weekly' and month_input and week_select:
            filter_date = pd.to_datetime(month_input, errors='coerce')
            if not pd.isna(filter_date):
                month_str = filter_date.strftime("%b'%y")
                week_str = f"Week {week_select}"
                df = df[(df['month'] == month_str) & (df['week'] == week_str)]
        elif time_filter == 'monthly' and month_input:
            filter_date = pd.to_datetime(month_input, errors='coerce')
            if not pd.isna(filter_date):
                month_str = filter_date.strftime("%b'%y")
                df = df[df['month'] == month_str]
        elif time_filter == 'yearly' and year_input:
            try:
                filter_year = int(year_input)
                df = df[df['date'].dt.year == filter_year]
            except ValueError:
                pass

        # Group printers
        printer_group = df.groupby(['printer_model', 'hostname', 'location'])['pages_printed'].sum().reset_index()
        top_printers = printer_group.sort_values(by='pages_printed', ascending=False).head(10)
        least_printers = printer_group.sort_values(by='pages_printed', ascending=True).head(10)

        # Top users
        top_users = df.groupby('user_name')['pages_printed'].sum().sort_values(ascending=False).head(10).reset_index()

        # Prepare filter summary rows
        filter_summary = [
            ['Applied Filters'],
            ['Time Filter', time_filter],
            ['Location Filter', location_filter],
            ['Division Filter', division_filter],
            ['Date Input', date_input],
            ['Month Input', month_input],
            ['Week Select', week_select],
            ['Year Input', year_input],
            []
        ]

        # Export to Excel
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            for sheet_name, data in [
                ('Top Users', top_users),
                ('Top Printers', top_printers),
                ('Least Used Printers', least_printers),
            ]:
                # Create filter header as a DataFrame
                filter_df = pd.DataFrame(filter_summary)
                filter_df.to_excel(writer, sheet_name=sheet_name, index=False, header=False, startrow=0)

                # Write data starting below the filters (at row 10)
                data.to_excel(writer, sheet_name=sheet_name, index=False, startrow=len(filter_summary))

        output.seek(0)

        return send_file(
            output,
            as_attachment=True,
            download_name='dashboard_summary.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        flash(f"Error generating Excel: {e}")
        return redirect(url_for('dashboard'))  # Use correct route name for your app



if __name__ == '__main__':
    app.run(debug=True)
