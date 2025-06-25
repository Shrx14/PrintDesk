from flask import Flask, request, render_template, redirect, url_for, flash , jsonify, abort
import pandas as pd
from io import BytesIO
import pyodbc
import datetime
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
        upload_date DATETIME,
        uploaded_by NVARCHAR(255),
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

def create_printer_exceptions_table_if_not_exists():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sysobjects WHERE name='printer_exceptions' AND xtype='U'")
        result = cursor.fetchone()
        if not result:
            create_table_sql = """
            CREATE TABLE printer_exceptions (
                id INT IDENTITY(1,1) PRIMARY KEY,
                hostname NVARCHAR(255) UNIQUE,
                printer_model NVARCHAR(255),
                location NVARCHAR(255),
                division NVARCHAR(255)
            )
            """
            cursor.execute(create_table_sql)
            conn.commit()
            logging.info("Table 'printer_exceptions' created successfully.")
        else:
            logging.info("Table 'printer_exceptions' already exists.")
        cursor.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error creating printer_exceptions table: {e}")
        from flask import flash
        flash(f"Error creating printer_exceptions table: {e}")

def create_roles_table_if_not_exists():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM sysobjects WHERE name='roles' AND xtype='U'")
        result = cursor.fetchone()
        if not result:
            create_table_sql = """
            CREATE TABLE roles (
                id INT IDENTITY(1,1) PRIMARY KEY,
                user_name NVARCHAR(255) UNIQUE,
                roles NVARCHAR(255),
            )
            """
            cursor.execute(create_table_sql)
            conn.commit()
            logging.info("Table 'roles' created successfully.")
        else:
            logging.info("Table 'roles' already exists.")
        cursor.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error creating roles table: {e}")
        from flask import flash
        flash(f"Error creating roles table: {e}")

import time

def insert_data_to_db(df):
    import datetime
    from sqlalchemy import text
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
                'upload_date': datetime.datetime.now(),
                'uploaded_by': 'system'  # or get from session/user context
            })

        with engine.begin() as conn:
            conn.execute(text(insert_sql), batch_params)
        idx = batch_end

    engine.dispose()
    logging.info(f"Inserted {total_rows} rows into 'printer_logs' table successfully.")
    return total_rows

@app.route('/')
def home():
    return render_template("home.html")
@app.route('/api/home/html')
def api_home_html():
    return render_template('home.html') 
@app.route('/api/home', methods=['GET'])
def api_home():
    return jsonify({
        "message": "Welcome to Printer Desk",
        "status": "success",
        "info": "Use this API to integrate with the Printer Desk platform."
    })

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

            # Query max date from printer_logs
            engine = get_sqlalchemy_engine()
            with engine.connect() as conn:
                result = conn.execute(text("SELECT MAX(date) FROM printer_logs"))
                max_date_in_db = result.scalar()
            engine.dispose()

            if max_date_in_db is not None:
                min_date_in_upload = df['date'].min()
                if min_date_in_upload <= max_date_in_db:
                    flash(f"Please upload data from after {max_date_in_db}. Current file contains duplicate data.")
                    return redirect(url_for('upload'))

            # Insert data into database in chunks of 5000 rows
            try:
                chunk_size = 5000
                for start in range(0, len(df), chunk_size):
                    chunk = df.iloc[start:start+chunk_size]
                    insert_data_to_db(chunk)
            except Exception as e:
                flash(f"Error inserting data into database: {e}")
                logging.error(f"Error inserting data into database: {e}")
                return redirect(url_for('upload'))

            flash('File uploaded and data saved to database successfully!')
            return redirect(url_for('view'))

        except Exception as e:
            flash(f"Error processing file: {e}")
            return redirect(url_for('upload'))

    return render_template('upload.html')

@app.route('/api/upload', methods=['GET', 'POST'])
def api_upload_excel():
    upload_type = request.form.get('upload_type')
    file = request.files.get('file')

    if not file:
        return jsonify({'success': False, 'message': 'No file uploaded.'}), 400

    try:
        in_memory_file = BytesIO(file.read())
        df = pd.read_excel(in_memory_file)

        # Process as you did before
        # Add validation, parsing, etc. as in your existing upload logic

        return jsonify({'success': True, 'message': 'File uploaded and processed successfully.'})

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500



from flask import send_file
import io

from datetime import datetime, timedelta

@app.route('/view')
def view():
    page = request.args.get('page', default=1, type=int)
    per_page = 100
    offset = (page - 1) * per_page

    columns = [
        "document_name", "user_name", "hostname", "pages_printed", "date",
        "month", "week", "printer_model", "division", "location"
    ]

    filters = []
    params = {}

    search_term = request.args.get("search", "").strip()
    from_date = request.args.get("from_date", "").strip()
    to_date = request.args.get("to_date", "").strip()

    # Handle global search across all columns
    if search_term:
        search_clauses = [f"{col} LIKE :search" for col in columns]
        filters.append("(" + " OR ".join(search_clauses) + ")")
        params["search"] = f"%{search_term}%"

    # Handle date range filtering
    # If both dates are provided, filter between them
    if from_date and to_date:
        filters.append("CAST(date AS DATE) BETWEEN :from_date AND :to_date")
        params["from_date"] = from_date
        params["to_date"] = to_date
    elif from_date:
        filters.append("CAST(date AS DATE) >= :from_date")
        params["from_date"] = from_date
    elif to_date:
        filters.append("CAST(date AS DATE) <= :to_date")
        params["to_date"] = to_date
    else:
        # No date filters provided, default to last month
        # Calculate last month date range
        today = datetime.today()
        first_day_this_month = today.replace(day=1)
        last_month_end = first_day_this_month - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        filters.append("CAST(date AS DATE) BETWEEN :from_date AND :to_date")
        params["from_date"] = last_month_start.strftime('%Y-%m-%d')
        params["to_date"] = last_month_end.strftime('%Y-%m-%d')

    # Handle other column-specific filters
    for col in columns:
        if col in ('date',):
            continue  # date handled above
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
        query = """
            SELECT user_name, printer_model, hostname, location, division, pages_printed, date, month, week
            FROM printer_logs
            WHERE hostname NOT IN (SELECT hostname FROM printer_exceptions)
        """
        df = pd.read_sql_query(query, engine)
        engine.dispose()

        df['date'] = pd.to_datetime(df['date'], errors='coerce')

        logging.info(f"Filters => time: {time_filter}, date: {date_input}, month: {month_input}, year: {year_input}, week: {week_select}, location: {location_filter}, division: {division_filter}")

        # Set default year_input as the year of the last entry in printer_logs if not provided

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
                year_int = filter_date.year
                # For graph 1, filter to entire year to show all months
                df_time_graph1 = df[df['date'].dt.year == year_int].groupby('month')['pages_printed'].sum()
                # For other data, filter to selected month
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



from flask import request

@app.route('/exceptions', methods=['GET', 'POST'])
def exceptions():
    engine = get_sqlalchemy_engine()
    message = None
    error = None

    # Fetch all printers from printer_data to show in UI
    try:
        with engine.connect() as conn:
            printers_result = conn.execute(text("SELECT id, hostname, printer_model, location, division FROM printer_data ORDER BY hostname"))
            printers_list = [dict(row._mapping) for row in printers_result]

            # Fetch distinct divisions and locations for filters
            divisions_result = conn.execute(text("SELECT DISTINCT division FROM printer_data WHERE division IS NOT NULL ORDER BY division"))
            divisions = [row[0] for row in divisions_result]

            locations_result = conn.execute(text("SELECT DISTINCT location FROM printer_data WHERE location IS NOT NULL ORDER BY location"))
            locations = [row[0] for row in locations_result]

    except Exception as e:
        printers_list = []
        divisions = []
        locations = []
        error = f"Error fetching printers or filters: {e}"

    if request.method == 'POST':
        # Check if adding all zero pages printed printers
        if request.form.get('add_zero_pages'):
            try:
                with engine.begin() as conn:
                    # Query printers with zero pages printed
                    zero_pages_printers = conn.execute(text("""
                        SELECT pd.id, pd.hostname, pd.printer_model, pd.location, pd.division
                        FROM printer_data pd
                        LEFT JOIN (
                            SELECT hostname, SUM(pages_printed) AS total_pages
                            FROM printer_logs
                            GROUP BY hostname
                        ) pl ON pd.hostname = pl.hostname
                        WHERE ISNULL(pl.total_pages, 0) = 0
                    """)).fetchall()

                    added_count = 0
                    for printer in zero_pages_printers:
                        existing = conn.execute(
                            text("SELECT id FROM printer_exceptions WHERE hostname = :hostname"),
                            {'hostname': printer.hostname}
                        ).fetchone()
                        if not existing:
                            conn.execute(
                                text("""
                                    INSERT INTO printer_exceptions (hostname, printer_model, location, division)
                                    VALUES (:hostname, :printer_model, :location, :division)
                                """),
                                {
                                    'hostname': printer.hostname,
                                    'printer_model': printer.printer_model,
                                    'location': printer.location,
                                    'division': printer.division
                                }
                            )
                            added_count += 1
                message = f"Added {added_count} zero pages printed printer(s) to exceptions."
            except Exception as e:
                error = f"Error adding zero pages printed exceptions: {e}"
        else:
            # Handle adding multiple selected printers as exceptions
            selected_printer_ids = request.form.getlist('selected_printers')
            if not selected_printer_ids:
                error = "No printers selected to add as exceptions."
            else:
                try:
                    with engine.begin() as conn:
                        added_count = 0
                        for pid in selected_printer_ids:
                            # Fetch printer details by id
                            printer = conn.execute(
                                text("SELECT hostname, printer_model, location, division FROM printer_data WHERE id = :id"),
                                {'id': pid}
                            ).fetchone()
                            if printer:
                                # Check if exception already exists
                                existing = conn.execute(
                                    text("SELECT id FROM printer_exceptions WHERE hostname = :hostname"),
                                    {'hostname': printer.hostname}
                                ).fetchone()
                                if not existing:
                                    conn.execute(
                                        text("""
                                            INSERT INTO printer_exceptions (hostname, printer_model, location, division)
                                            VALUES (:hostname, :printer_model, :location, :division)
                                        """),
                                        {
                                            'hostname': printer.hostname,
                                            'printer_model': printer.printer_model,
                                            'location': printer.location,
                                            'division': printer.division
                                        }
                                    )
                                    added_count += 1
                        message = f"Added {added_count} printer(s) to exceptions."
                except Exception as e:
                    error = f"Error adding exceptions: {e}"

    # Fetch all exceptions to display
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT id, hostname, printer_model, location, division FROM printer_exceptions ORDER BY hostname"))
            exceptions_list = [dict(row._mapping) for row in result]
    except Exception as e:
        exceptions_list = []
        error = f"Error fetching exceptions: {e}"

    engine.dispose()

    return render_template('exceptions.html', exceptions=exceptions_list, printers=printers_list, divisions=divisions, locations=locations, message=message, error=error)

from flask import redirect

@app.route('/exceptions/delete/<int:exception_id>', methods=['POST'])
def delete_exception(exception_id):
    engine = get_sqlalchemy_engine()
    message = None
    error = None
    try:
        with engine.begin() as conn:
            # Check if exception exists
            existing = conn.execute(
                text("SELECT id FROM printer_exceptions WHERE id = :id"),
                {'id': exception_id}
            ).fetchone()
            if existing:
                conn.execute(
                    text("DELETE FROM printer_exceptions WHERE id = :id"),
                    {'id': exception_id}
                )
                message = "Exception deleted successfully."
            else:
                error = "Exception not found."
    except Exception as e:
        error = f"Error deleting exception: {e}"
    engine.dispose()
    # Redirect back to exceptions page with message or error flashed
    if message:
        flash(message)
    if error:
        flash(error)
    return redirect(url_for('exceptions'))

import matplotlib
matplotlib.use('Agg')  # Use non-GUI backend to avoid tkinter errors
import matplotlib.pyplot as plt
import base64
from io import BytesIO
import pandas as pd
import numpy as np
from flask import jsonify

@app.route('/dashboard/visualize')
def dashboard_visualize():
    try:
        # Parse filters from query parameters
        time_filter = request.args.get('time_filter')
        location_filter = request.args.get('location_filter')
        division_filter = request.args.get('division_filter')
        date_input = request.args.get('date_input')
        month_input = request.args.get('month_input')
        year_input = request.args.get('year_input')
        week_select = request.args.get('week_select')

        engine = get_sqlalchemy_engine()
        query = """
            SELECT user_name, printer_model, hostname, location, division, pages_printed, date, month, week
            FROM printer_logs
            WHERE hostname NOT IN (SELECT hostname FROM printer_exceptions)
        """
        df = pd.read_sql_query(query, engine)
        engine.dispose()

        df['date'] = pd.to_datetime(df['date'], errors='coerce')

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

        # Prepare data for graphs
        # 1. Pages Printed Over Time (daily or monthly depending on filter)
        if time_filter in ['daily', 'weekly']:
            df_time = df.groupby(df['date'].dt.date)['pages_printed'].sum()
        elif time_filter == 'monthly':
            # Separate query for graph 1 to show all months data for current year
            current_year = pd.Timestamp.now().year
            df_time_graph1 = df[df['date'].dt.year == current_year].groupby('month')['pages_printed'].sum()
            # Debug: log unique month values and counts for current year in df_time_graph1
            import logging
            logging.debug(f"Unique months and counts for current year in df_time_graph1:\\n{df_time_graph1}")
            # Convert month strings like "May'25" to datetime for sorting
            def parse_month_str(m):
                try:
                    return pd.to_datetime(m, format="%b'%y")
                except Exception:
                    return pd.NaT
            df_time_graph1.index = df_time_graph1.index.map(parse_month_str)
            df_time_graph1 = df_time_graph1.dropna()
            df_time_graph1 = df_time_graph1.sort_index()
            # For graph 1, use df_time_graph1 with all months of the year
            df_time = df_time_graph1
        else:  # yearly
            df_time = df.groupby(df['date'].dt.year)['pages_printed'].sum()

        # 2. Pages Printed by Location (10 Least Used Printers)
        least_printers = df.groupby('printer_model')['pages_printed'].sum().sort_values(ascending=True).head(10)

        # 3. Pages Printed by Location (Top 10 Used Printers)
        top_printers = df.groupby('printer_model')['pages_printed'].sum().sort_values(ascending=False).head(10)

        # 4. Last 3 Months Pages Printed (10 Least Used Printers)
        last_3_months = pd.Timestamp.now() - pd.DateOffset(months=3)

        # Separate query for graph 4 data filtered only by division and last 3 months, ignoring other filters
        graph4_query = """
            SELECT printer_model, SUM(pages_printed) AS pages_printed
            FROM printer_logs
            WHERE date >= :last_3_months
            AND hostname NOT IN (SELECT hostname FROM printer_exceptions)
        """
        params_graph4 = {'last_3_months': last_3_months}

        if division_filter and division_filter != 'all':
            graph4_query += " AND division = :division_filter"
            params_graph4['division_filter'] = division_filter

        graph4_query += " GROUP BY printer_model ORDER BY pages_printed ASC"

        df_graph4 = pd.read_sql_query(text(graph4_query), engine, params=params_graph4)
        least_printers_3m = df_graph4.set_index('printer_model')['pages_printed'].head(10)

        # Helper function to plot and encode to base64
        def plot_to_base64(fig):
            buf = BytesIO()
            fig.savefig(buf, format='png', bbox_inches='tight')
            buf.seek(0)
            img_base64 = base64.b64encode(buf.read()).decode('utf-8')
            plt.close(fig)
            return img_base64

        # Graph 1: Pages Printed Over Time
        fig1, ax1 = plt.subplots(figsize=(8, 4))
        if time_filter in ['daily', 'weekly']:
            df_time.plot(kind='bar', ax=ax1, color='blue')
            ax1.set_xlabel('Date')
        elif time_filter in ['monthly']:
            # Use separate df_time_graph1 for graph 1 to show all months of current year
            def parse_month_str(m):
                try:
                    return pd.to_datetime(m, format="%b'%y")
                except Exception:
                    return pd.NaT
            df_time_graph1.index = df_time_graph1.index.map(parse_month_str)
            df_time_graph1 = df_time_graph1.dropna()
            df_time_graph1 = df_time_graph1.sort_index()
            df_time_graph1.plot(kind='bar', ax=ax1, color='blue')
            ax1.set_xlabel('Month')
            # Set x-axis tick labels to month names only (e.g., May, Jun)
            ax1.set_xticklabels([dt.strftime('%b') for dt in df_time_graph1.index], rotation=45, ha='right')
        else:
            # For yearly data, index is already year integers, no need to parse
            df_time = df_time.sort_index()
            df_time.plot(kind='bar', ax=ax1, color='blue')
            ax1.set_xlabel('Year')
        ax1.set_title('Pages Printed Over Time')
        ax1.set_ylabel('Pages Printed')
        ax1.grid(True)
        graph1 = plot_to_base64(fig1)

        # Graph 2: Pages Printed by Location (10 Least Used Printers)
        fig2, ax2 = plt.subplots(figsize=(8, 4))
        least_printers.plot(kind='bar', ax=ax2, color='red')
        ax2.set_title('Pages Printed by Location (10 Least Used Printers)')
        ax2.set_xlabel('Printer Model')
        ax2.set_ylabel('Pages Printed')
        ax2.grid(axis='y')
        graph2 = plot_to_base64(fig2)

        # Graph 3: Pages Printed by Location (Top 10 Used Printers)
        fig3, ax3 = plt.subplots(figsize=(8, 4))
        top_printers.plot(kind='bar', ax=ax3, color='green')
        ax3.set_title('Pages Printed by Location (Top 10 Used Printers)')
        ax3.set_xlabel('Printer Model')
        ax3.set_ylabel('Pages Printed')
        ax3.grid(axis='y')
        graph3 = plot_to_base64(fig3)

        # Graph 4: Last 3 Months Pages Printed (10 Least Used Printers)
        fig4, ax4 = plt.subplots(figsize=(8, 4))
        least_printers_3m.plot(kind='bar', ax=ax4, color='orange')
        ax4.set_title('Last 3 Months Pages Printed (10 Least Used Printers)')
        ax4.set_xlabel('Printer Model')
        ax4.set_ylabel('Pages Printed')
        ax4.grid(axis='y')
        graph4 = plot_to_base64(fig4)

        return jsonify({
            'success': True,
            'graph1': graph1,
            'graph2': graph2,
            'graph3': graph3,
            'graph4': graph4
        })

    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    
import os    

@app.route('/admin')
def admin():
    username = os.getlogin()  # Get Windows username

    # Connect to DB
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Check roles for current user
    cursor.execute("SELECT roles FROM roles WHERE user_name = ?", username)
    row = cursor.fetchone()

    if not row or 'admin' not in row[0].lower():
        cursor.close()
        conn.close()
        return abort(403)
    
    # Fetch all users and their roles
    cursor.execute("SELECT user_name, roles FROM roles")
    users = cursor.fetchall()

    # Convert to dictionary {username: {view: bool, upload: bool, admin: bool}}
    user_data = {}
    for user_name, roles in users:
        roles_lower = roles.lower()
        user_data[user_name] = {
            'view': 'view' in roles_lower,
            'upload': 'upload' in roles_lower,
            'admin': 'admin' in roles_lower
        }
    
    cursor.close()
    conn.close()

    return render_template('admin.html', users=user_data)

@app.route('/add_user', methods=['POST'])
def add_user():
    data = request.get_json()
    username = data.get('username')
    roles = data.get('roles', [])

    if not username or not isinstance(roles, list):
        return jsonify({'error': 'Invalid input'}), 400

    # Combine roles into comma-separated string
    roles_str = ','.join(roles)

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("INSERT INTO roles (user_name, roles) VALUES (?, ?)", username, roles_str)
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

    return jsonify({'message': 'User added successfully'})

@app.route('/update_user_permissions', methods=['POST'])
def update_user_permissions():
    data = request.get_json()
    username = data.get('username')
    roles = data.get('roles', [])

    if not username or not isinstance(roles, list):
        return jsonify({'error': 'Invalid input'}), 400

    roles_str = ','.join(roles)

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("UPDATE roles SET roles = ? WHERE user_name = ?", (roles_str, username))
        if cursor.rowcount == 0:
            # No user found with this username
            return jsonify({'error': 'User not found'}), 404
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

    return jsonify({'message': 'Permissions updated successfully'})


app.run(host='0.0.0.0', port=5000, debug=True)
