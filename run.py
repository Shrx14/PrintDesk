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
from sqlalchemy import text

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
    duplicates = []
    logging.info(f"Starting insert_data_to_db with {len(df)} rows")
    conn = get_db_connection()
    cursor = conn.cursor()
    insert_sql = """
    INSERT INTO printer_logs 
    (document_name, user_name, hostname, pages_printed, date, month, week_1, printer_model, division, location)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    batch_size = 1000
    batch_params = []
    # Filter out rows with missing or empty hostname
    df_filtered = df[df['hostname'].notnull() & (df['hostname'] != '')]

    # Prepare a set of existing keys to check duplicates in bulk
    existing_keys = set()
    try:
        cursor.execute("SELECT user_name, document_name, hostname, date FROM printer_logs")
        rows = cursor.fetchall()
        for r in rows:
            # r[3] is datetime object
            existing_keys.add((r[0], r[1], r[2], r[3]))
    except Exception as e:
        logging.error(f"Error fetching existing keys: {e}")

    inserted_count = 0

    def execute_with_retry(func, max_retries=3, delay=5):
        for attempt in range(max_retries):
            try:
                return func()
            except Exception as e:
                if '08S01' in str(e) or 'Communication link failure' in str(e):
                    logging.warning(f"Transient connection error on attempt {attempt+1}/{max_retries}: {e}")
                    time.sleep(delay * (2 ** attempt))  # Exponential backoff
                else:
                    raise
        raise Exception("Max retries exceeded for database operation due to connection issues.")

    for idx, row in df_filtered.iterrows():
        user_name = row['user name']
        document_name = row['document name']
        hostname = row['hostname']
        pages_printed = int(row['pages printed'])
        date_val = row['date'] if pd.notnull(row['date']) else None
        month = row['month']
        week_1 = row['week_1']
        printer_model = row.get('printer_model', None)
        division = row.get('division', None)
        location = row.get('location', None)

        key = (user_name, document_name, hostname, date_val)
        if key in existing_keys:
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
            logging.info(f"Row {idx}: Duplicate found, skipping insert.")
        else:
            batch_params.append((
                document_name, user_name, hostname, 
                pages_printed, date_val,
                month, week_1, printer_model, division, location
            ))
            inserted_count += 1

        if len(batch_params) >= batch_size:
            try:
                execute_with_retry(lambda: cursor.executemany(insert_sql, batch_params))
                conn.commit()
                batch_params = []
            except Exception as e:
                # On batch insert error, fallback to row-by-row insert with duplicate skipping and retry
                logging.error(f"Batch insert error: {e}. Falling back to row-by-row insert.")
                for i, params in enumerate(batch_params):
                    try:
                        execute_with_retry(lambda: cursor.execute(insert_sql, params))
                        conn.commit()
                        inserted_count += 1
                    except Exception as ex:
                        if 'unique_all_columns' in str(ex):
                            logging.info(f"Row-by-row insert: Duplicate detected, skipping.")
                        else:
                            logging.error(f"Row-by-row insert error: {ex}")
                            raise ex
                batch_params = []

    # Insert remaining batch
    if batch_params:
        try:
            execute_with_retry(lambda: cursor.executemany(insert_sql, batch_params))
            conn.commit()
        except Exception as e:
            logging.error(f"Batch insert error: {e}. Falling back to row-by-row insert.")
            for i, params in enumerate(batch_params):
                try:
                    execute_with_retry(lambda: cursor.execute(insert_sql, params))
                    conn.commit()
                    inserted_count += 1
                except Exception as ex:
                    if 'unique_all_columns' in str(ex):
                        logging.info(f"Row-by-row insert: Duplicate detected, skipping.")
                    else:
                        logging.error(f"Row-by-row insert error: {ex}")
                        raise ex

    cursor.close()
    conn.close()
    logging.info(f"Inserted {inserted_count} rows into 'printer_logs' table successfully. {len(duplicates)} duplicates skipped.")
    return duplicates

@app.route('/')
def home():
    return render_template("home.html")

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        upload_type = request.form.get('upload_type')
        file = request.files.get('file')
        if not file:
            flash('No file uploaded.')
            return redirect(url_for('upload'))

        try:
            import logging
            # Read Excel file into DataFrame
            in_memory_file = BytesIO(file.read())
            df = pd.read_excel(in_memory_file)

            if upload_type == 'printer_logs':
                # Limit rows to 5000 to avoid connection issues
                max_rows = 5000
                if len(df) > max_rows:
                    flash(f"Upload file exceeds maximum allowed rows of {max_rows}. Please split your file and upload in parts.")
                    return redirect(url_for('upload'))

                # Normalize column names (strip and lowercase)
                df.columns = [col.strip().lower() for col in df.columns]
                # Expected columns for printer logs
                expected_cols = {
                    'document name', 'user name', 'hostname', 'pages printed', 'date'
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

                # Convert 'pages printed' to numeric
                df['pages printed'] = pd.to_numeric(df['pages printed'], errors='coerce')
                df = df.dropna(subset=['pages printed'])

                # Derive 'month' column formatted as abbreviated month name + apostrophe + last two digits of year (e.g., May'25)
                df['month'] = df['date'].dt.strftime("%b'%y")

                # Derive 'week_1' column formatted as "Week " + week number in the month (e.g., Week 2)
                def week_of_month(dt):
                    first_day = dt.replace(day=1)
                    dom = dt.day
                    adjusted_dom = dom + first_day.weekday()
                    return int((adjusted_dom - 1) / 7) + 1

                df['week_1'] = df['date'].apply(week_of_month)
                df['week_1'] = df['week_1'].apply(lambda x: f"Week {x}")

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
                    df.rename(columns={'week 1': 'week_1'}, inplace=True)

                # Replace NaN in printer_model, division, location with None for DB insertion
                df['printer_model'] = df['printer_model'].where(pd.notnull(df['printer_model']), None)
                df['division'] = df['division'].where(pd.notnull(df['division']), None)
                df['location'] = df['location'].where(pd.notnull(df['location']), None)

                # Log derived month and week_1 columns before insertion
                logging.info(f"Derived 'month' column sample data:\\n{df['month'].head()}")
                logging.info(f"Derived 'week_1' column sample data:\\n{df['week_1'].head()}")

                # Insert data into database
                try:
                    duplicates = insert_data_to_db(df)
                except Exception as e:
                    flash(f"Error inserting data into database: {e}")
                    logging.error(f"Error inserting data into database: {e}")
                    return redirect(url_for('upload'))

                if duplicates and len(duplicates) > 0:
                    duplicate_msgs = [f"Duplicate entry skipped: Document '{d['document_name']}', User '{d['user_name']}', Host '{d['hostname']}', Date '{d['date']}'" for d in duplicates]
                    for msg in duplicate_msgs:
                        flash(msg)

                flash('File uploaded and data saved to database successfully!')
                return redirect(url_for('view'))

            elif upload_type == 'printer_data':
                # Create printer_data table if not exists BEFORE validating file columns
                try:
                    create_printer_data_table_if_not_exists()
                except Exception as e:
                    flash(f"Error creating printer_data table: {e}")
                    logging.error(f"Error creating printer_data table: {e}")
                    return redirect(url_for('upload'))

                # Do NOT normalize columns to lowercase here to preserve original Excel column names
                # expected columns as in Excel file
                expected_cols = {
                    'HostName', 'Division', 'Asset_Model_Descr', 'Loca_Name'
                }

                if not expected_cols.issubset(set(df.columns)):
                    flash(f"Excel file must contain columns: {expected_cols}")
                    logging.error(f"Missing columns in printer_data upload: {set(df.columns)}")
                    return redirect(url_for('upload'))

                # Normalize hostname column
                df['HostName'] = df['HostName'].str.strip().str.lower()

                # Rename columns to match database schema
                df.rename(columns={
                    'HostName': 'hostname',
                    'Division': 'division',
                    'Asset_Model_Descr': 'printer_model',
                    'Loca_Name': 'location'
                }, inplace=True)

                # Insert printer data into database
                duplicates = []
                try:
                    logging.info(f"Starting insert of {len(df)} rows into printer_data")
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    check_sql = """
                    SELECT COUNT(*) FROM printer_data WHERE hostname = ?
                    """
                    insert_sql = """
                    INSERT INTO printer_data (hostname, division, printer_model, location)
                    VALUES (?, ?, ?, ?)
                    """
                    update_sql = """
                    UPDATE printer_data SET division = ?, printer_model = ?, location = ? WHERE hostname = ?
                    """
                    # Filter out rows with missing hostname
                    df_filtered = df[df['hostname'].notnull() & (df['hostname'] != '')]
                    for idx, row in df_filtered.iterrows():
                        logging.info(f"Row {idx}: {row.to_dict()}")
                        hostname = row['hostname']
                        division = row['division'] if pd.notnull(row['division']) and row['division'] != '' else None
                        printer_model = row['printer_model'] if pd.notnull(row['printer_model']) and row['printer_model'] != '' else None
                        location = row['location'] if pd.notnull(row['location']) and row['location'] != '' else None

                        # Log values before insert/update
                        logging.info(f"Inserting/updating printer_data: hostname={hostname}, division={division}, printer_model={printer_model}, location={location}")

                        cursor.execute(check_sql, (hostname,))
                        count = cursor.fetchone()[0]

                        if count > 0:
                            cursor.execute(update_sql, (division, printer_model, location, hostname))
                        else:
                            cursor.execute(insert_sql, (hostname, division, printer_model, location))
                    conn.commit()
                    cursor.close()
                    conn.close()
                    flash('Printer data uploaded and saved successfully!')
                    return redirect(url_for('upload'))
                except Exception as e:
                    flash(f"Error inserting printer data into database: {e}")
                    logging.error(f"Error inserting printer data into database: {e}")
                    return redirect(url_for('upload'))
            else:
                flash('Invalid upload type selected.')
                return redirect(url_for('upload'))

        except Exception as e:
            flash(f"Error processing file: {e}")
            return redirect(url_for('upload'))

    return render_template('upload.html')

from flask import request

from flask import request, render_template, flash
from sqlalchemy import text
import pandas as pd

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
        "month", "week_1", "printer_model", "division", "location"
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

        unique_values = {}
        with engine.connect() as conn:
            for col in columns:
                result = conn.execute(text(f"SELECT DISTINCT {col} FROM printer_logs ORDER BY {col}"))
                unique_values[col] = [str(row[0]) for row in result if row[0] is not None]

            count_query = f"SELECT COUNT(*) FROM printer_logs {where_clause}"
            total_rows = conn.execute(text(count_query), params).scalar()
            total_pages = (total_rows + per_page - 1) // per_page

            query = f"""
                SELECT document_name, user_name, hostname, pages_printed, date, month,
                       week_1, printer_model, division, location
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
        "month", "week_1", "printer_model", "division", "location"
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
                   week_1, printer_model, division, location
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
    time_filter = request.args.get('time_filter', 'all')
    location_filter = request.args.get('location_filter', 'all')
    date_input = request.args.get('date_input', None)

    # Set default date_input based on time_filter if not provided
    if not date_input:
        now = datetime.datetime.now()
        if time_filter == 'daily':
            date_input = now.strftime('%Y-%m-%d')
        elif time_filter == 'monthly':
            date_input = now.strftime('%Y-%m')
        elif time_filter == 'yearly':
            date_input = str(now.year)

    try:
        engine = get_sqlalchemy_engine()
        query = "SELECT user_name, printer_model, location, pages_printed, date FROM printer_logs"
        df = pd.read_sql_query(query, engine)
        engine.dispose()

        # Convert 'date' column to datetime
        df['date'] = pd.to_datetime(df['date'], errors='coerce')

        # Filter by location if applicable
        if location_filter != 'all':
            df = df[df['location'] == location_filter]

        # Filter by time
        if time_filter == 'daily' and date_input:
            filter_date = pd.to_datetime(date_input, errors='coerce')
            if not pd.isna(filter_date):
                df = df[df['date'].dt.date == filter_date.date()]
        elif time_filter == 'monthly' and date_input:
            filter_date = pd.to_datetime(date_input, errors='coerce')
            if not pd.isna(filter_date):
                df = df[(df['date'].dt.year == filter_date.year) & (df['date'].dt.month == filter_date.month)]
        elif time_filter == 'yearly' and date_input:
            filter_year = None
            try:
                filter_year = int(date_input)
            except:
                filter_year = None
            if filter_year:
                df = df[df['date'].dt.year == filter_year]
        # else 'all' no filter

        # Compute top users by pages printed
        top_users = df.groupby('user_name')['pages_printed'].sum().sort_values(ascending=False).head(10).to_dict()

        # Compute top printers by pages printed
        top_printers = df.groupby('printer_model')['pages_printed'].sum().sort_values(ascending=False).head(10).to_dict()

        # Compute least used printers by pages printed
        least_printers = df.groupby('printer_model')['pages_printed'].sum().sort_values(ascending=True).head(10).to_dict()

        # Get unique locations for filter dropdown
        locations = sorted(df['location'].dropna().unique())

        # Pass the filtered data to template
        data = df

    except Exception as e:
        top_users = {}
        top_printers = {}
        least_printers = {}
        locations = []
        data = None

    return render_template('dashboard.html',
                           top_users=top_users,
                           top_printers=top_printers,
                           least_printers=least_printers,
                           locations=locations,
                           data=data,
                           time_filter=time_filter,
                           location_filter=location_filter,
                           date_input=date_input)


if __name__ == '__main__':
    app.run(debug=True)
