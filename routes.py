from flask import Blueprint, Flask, request, render_template, redirect, url_for, flash, jsonify, send_file
import pandas as pd
from io import BytesIO
from sqlalchemy import text
import io
import logging
import matplotlib
matplotlib.use('Agg')  # Use non-GUI backend to avoid tkinter errors
import matplotlib.pyplot as plt
import base64
from datetime import datetime, timedelta
import traceback
import numpy as np
import re
import os
from flask import abort, request, jsonify, render_template

from config import get_sqlalchemy_engine
from db import (
    create_table_if_not_exists,
    create_printer_data_table_if_not_exists,
    create_printer_exceptions_table_if_not_exists,
    create_roles_table_if_not_exists,
    get_db_connection
)
from data_processing import insert_data_to_db

import functools
import os
from flask import Blueprint, Flask, request, render_template, redirect, url_for, flash, jsonify, send_file, abort, g

routes = Blueprint('routes', __name__)

def get_user_roles():
    username = os.getlogin()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT roles FROM roles WHERE user_name = ?", (username,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row:
        roles = [role.strip().lower() for role in row[0].split(',')]
        return roles
    return []

@routes.before_app_request
def before_request():
    allowed_paths_for_roles = {
        'admin': {'/admin', '/', '/home', '/dashboard', '/exceptions', '/exceptions/delete/', '/view', '/upload', '/update_user_permissions', '/add_user', '/download', '/dashboard/export', '/dashboard/visualize'},
        'upload': {'/upload', '/', '/home', '/dashboard', '/exceptions', '/exceptions/delete/', '/view', '/download', '/dashboard/export', '/dashboard/visualize'},
        'view': {'/', '/home', '/view', '/dashboard', '/download', '/dashboard/export', '/dashboard/visualize'},
    }

    path = request.path
    # Normalize path to remove trailing slash except root
    if path != '/' and path.endswith('/'):
        path = path[:-1]

    # Allow access to static files without role check
    if path.startswith('/static/'):
        return None

    roles = get_user_roles()
    g.user_roles = roles  # store roles in flask.g for use in routes if needed

    # Redirect root path based on role to home.html
    if path == '/':
        # Allow access to home page without redirect to avoid loop
        if 'admin' in roles or 'upload' in roles or 'view' in roles:
            return None  # allow access to '/'
        else:
            return abort(403)

    # Check access for other paths
    # If user has any role that allows access to the path, allow
    for role in roles:
        allowed_paths = allowed_paths_for_roles.get(role, set())
        # Check if path matches allowed path exactly or starts with allowed path (for parameterized routes)
        for allowed_path in allowed_paths:
            if allowed_path.endswith('/') and path.startswith(allowed_path):
                return None  # allow access
            if path == allowed_path:
                return None  # allow access

    # If no roles allow access, abort 403
    return abort(403)

    # Check access for other paths
    # If user has any role that allows access to the path, allow
    for role in roles:
        allowed_paths = allowed_paths_for_roles.get(role, set())
        if path in allowed_paths:
            return None  # allow access

    # If no roles allow access, abort 403
    return abort(403)

# Update existing routes to use g.user_roles if needed for template rendering or further checks


# User role management routes

@routes.route('/admin')
def admin():
    username = os.getlogin()  # Get Windows username

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT roles FROM roles WHERE user_name = ?", username)
    row = cursor.fetchone()

    if not row or 'admin' not in row[0].lower():
        cursor.close()
        conn.close()
        return abort(403)

    cursor.execute("SELECT user_name, roles FROM roles")
    users = cursor.fetchall()

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

@routes.route('/add_user', methods=['POST'])
def add_user():
    data = request.get_json()
    username = data.get('username')
    roles = data.get('roles', [])

    if not username or not isinstance(roles, list):
        return jsonify({'error': 'Invalid input'}), 400

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

@routes.route('/update_user_permissions', methods=['POST'])
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
            return jsonify({'error': 'User not found'}), 404
        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

    return jsonify({'message': 'Permissions updated successfully'})


@routes.route('/')
def home():
    return render_template("home.html")

# Context processor to inject user_roles into templates
@routes.app_context_processor
def inject_user_roles():
    return dict(user_roles=getattr(g, 'user_roles', []))

@routes.route('/api/home/html')
def api_home_html():
    return render_template('home.html')

@routes.route('/api/home', methods=['GET'])
def api_home():
    return jsonify({
        "message": "Welcome to Printer Desk",
        "status": "success",
        "info": "Use this API to integrate with the Printer Desk platform."
    })

@routes.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        file = request.files.get('file')
        if not file:
            flash('No file uploaded.')
            return redirect(url_for('routes.upload'))

        try:
            in_memory_file = BytesIO(file.read())

            expected_cols = {'document name', 'user name', 'hostname', 'pages printed', 'date'}

            for header_row in [0, 1, 2]:
                in_memory_file.seek(0)
                df = pd.read_excel(in_memory_file, header=header_row)
                logging.info(f"Columns read from Excel (header={header_row}): {df.columns.tolist()}")
                df.columns = [col.strip().lower() for col in df.columns]
                if expected_cols.issubset(set(df.columns)):
                    break
            else:
                flash(f"Excel file must contain columns: {expected_cols}")
                return redirect(url_for('routes.upload'))

            df.columns = [col.strip().lower() for col in df.columns]

            if not expected_cols.issubset(set(df.columns)):
                flash(f"Excel file must contain columns: {expected_cols}")
                return redirect(url_for('routes.upload'))

            def clean_string(s):
                if pd.isna(s):
                    return None
                s = re.sub(r'[\x00-\x1F\x7F-\x9F]', '', str(s))
                s = re.sub(r'\s+', ' ', s).strip()
                return s

            for col in ['user name', 'document name', 'hostname', 'month', 'week', 'printer_model', 'division', 'location']:
                if col in df.columns:
                    df[col] = df[col].apply(clean_string)

            # Enhanced cleaning for date column
            def clean_date_string(s):
                if pd.isna(s):
                    return None
                s = str(s)
                # Remove control chars and non-printable characters
                s = re.sub(r'[\x00-\x1F\x7F-\x9F\u200B-\u200D\uFEFF]', '', s)
                s = s.strip()
                return s

            df['date'] = df['date'].apply(clean_date_string)

            # Debug logging for date strings that fail parsing
            raw_dates = df['date'].astype(str).tolist()
            logging.debug(f"Raw date strings before parsing: {raw_dates[:10]}")

            date_formats = [
                '%Y-%m-%d %H:%M:%S',
                '%d-%m-%Y %H:%M:%S',
                '%m/%d/%Y %H:%M:%S',
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

            # Use pandas.to_datetime with dayfirst=True for robust parsing
            df['date'] = pd.to_datetime(df['date'], dayfirst=True, errors='coerce')

            logging.info(f"Date column after parsing with pandas.to_datetime:\\n{df['date'].head()}")

            initial_row_count = len(df)
            invalid_dates = df[df['date'].isna()]
            if not invalid_dates.empty:
                invalid_date_values = invalid_dates['date'].astype(str).tolist()
                logging.warning(f"Rows with invalid dates:\\n{invalid_date_values[:20]}")
                flash(f"{len(invalid_date_values)} rows with invalid dates were skipped. Invalid dates: {invalid_date_values[:5]} ...")
            df = df.dropna(subset=['date'])
            dropped_rows = initial_row_count - len(df)

            df['pages printed'] = pd.to_numeric(df['pages printed'], errors='coerce')
            df = df.dropna(subset=['pages printed'])

            try:
                if 'month' not in df.columns or df['month'].isnull().all():
                    df['month'] = df['date'].dt.strftime("%b'%y")
            except Exception as e:
                logging.error(f"Error deriving 'month' column: {e}")
                flash(f"Error deriving 'month' column: {e}")
                return redirect(url_for('routes.upload'))

            def week_of_month(dt):
                first_day = dt.replace(day=1)
                dom = dt.day
                adjusted_dom = dom + first_day.weekday()
                return int((adjusted_dom - 1) / 7) + 1

            df['week'] = df['date'].apply(week_of_month)
            df['week'] = df['week'].apply(lambda x: f"Week {x}")

            engine = get_sqlalchemy_engine()
            query = "SELECT hostname, division, printer_model, location FROM printer_data"
            printer_data_db = pd.read_sql_query(query, engine)
            engine.dispose()

            printer_data_db['hostname'] = printer_data_db['hostname'].str.strip().str.lower()
            df['hostname'] = df['hostname'].str.strip().str.lower()

            df = df.merge(printer_data_db, on='hostname', how='left')

            create_table_if_not_exists()

            if 'week 1' in df.columns:
                df.rename(columns={'week 1': 'week'}, inplace=True)

            df['printer_model'] = df['printer_model'].where(pd.notnull(df['printer_model']), None)
            df['division'] = df['division'].where(pd.notnull(df['division']), None)
            df['location'] = df['location'].where(pd.notnull(df['location']), None)

            logging.info(f"Derived 'month' column sample data:\\n{df['month'].head()}")
            logging.info(f"Derived 'week' column sample data:\\n{df['week'].head()}")

            engine = get_sqlalchemy_engine()
            with engine.connect() as conn:
                result = conn.execute(text("SELECT MAX(date) FROM printer_logs"))
                max_date_in_db = result.scalar()
            engine.dispose()

            if max_date_in_db is not None:
                min_date_in_upload = df['date'].min()
                if min_date_in_upload <= max_date_in_db:
                    flash(f"Please upload data from after {max_date_in_db}. Current file contains duplicate data.")
                    return redirect(url_for('routes.upload'))

            try:
                # Remove duplicate rows based on user_name, hostname, and date columns
                before_dedup_count = len(df)
                df = df.drop_duplicates(subset=['user name', 'hostname', 'date'])
                after_dedup_count = len(df)
                skipped_count = before_dedup_count - after_dedup_count
                logging.info(f"Skipped {skipped_count} duplicate rows based on user_name, hostname, and date in uploaded file.")
                if skipped_count > 0:
                    flash(f"Skipped {skipped_count} duplicate entries in the uploaded file.")

                import os
                from datetime import datetime
                uploaded_by = os.getlogin()
                upload_date = datetime.now()
                chunk_size = 5000
                for start in range(0, len(df), chunk_size):
                    chunk = df.iloc[start:start+chunk_size]
                    insert_data_to_db(chunk, uploaded_by=uploaded_by, upload_date=upload_date)
            except Exception as e:
                flash(f"Error inserting data into database: {e}")
                logging.error(f"Error inserting data into database: {e}")
                return redirect(url_for('routes.upload'))

            flash('File uploaded and data saved to database successfully!')
            return redirect(url_for('routes.view'))

        except Exception as e:
            flash(f"Error processing file: {e}")
            return redirect(url_for('routes.upload'))

    return render_template('upload.html')

@routes.route('/api/upload', methods=['GET', 'POST'])
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

@routes.route('/view')
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

    if search_term:
        search_clauses = [f"{col} LIKE :search" for col in columns]
        filters.append("(" + " OR ".join(search_clauses) + ")")
        params["search"] = f"%{search_term}%"

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
        today = datetime.today()
        first_day_this_month = today.replace(day=1)
        last_month_end = first_day_this_month - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        filters.append("CAST(date AS DATE) BETWEEN :from_date AND :to_date")
        params["from_date"] = last_month_start.strftime('%Y-%m-%d')
        params["to_date"] = last_month_end.strftime('%Y-%m-%d')

    for col in columns:
        if col in ('date',):
            continue
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

@routes.route('/download')
def download_excel():
    columns = [
        "document_name", "user_name", "hostname", "pages_printed", "date",
        "month", "week", "printer_model", "division", "location"
    ]

    filters = []
    params = {}
    applied_filters = []

    # Search term
    search_term = request.args.get("search", "").strip()
    if search_term:
        search_clauses = [f"{col} LIKE :search" for col in columns]
        filters.append("(" + " OR ".join(search_clauses) + ")")
        params["search"] = f"%{search_term}%"
        applied_filters.append(f"Search: {search_term}")

    # Date filters
    from_date = request.args.get("from_date", "").strip()
    to_date = request.args.get("to_date", "").strip()

    if from_date and to_date:
        filters.append("CAST(date AS DATE) BETWEEN :from_date AND :to_date")
        params["from_date"] = from_date
        params["to_date"] = to_date
        applied_filters.append(f"Date: {from_date} to {to_date}")
    elif from_date:
        filters.append("CAST(date AS DATE) >= :from_date")
        params["from_date"] = from_date
        applied_filters.append(f"Date from: {from_date}")
    elif to_date:
        filters.append("CAST(date AS DATE) <= :to_date")
        params["to_date"] = to_date
        applied_filters.append(f"Date to: {to_date}")

    # Column-specific filters
    for col in columns:
        if col == 'date':
            continue
        val = request.args.get(col)
        if val:
            filters.append(f"{col} = :{col}")
            params[col] = val
            applied_filters.append(f"{col.title().replace('_', ' ')}: {val}")

    where_clause = "WHERE " + " AND ".join(filters) if filters else ""

    try:
        engine = get_sqlalchemy_engine()
        query = f"""
            SELECT {', '.join(columns)}
            FROM printer_logs
            {where_clause}
            ORDER BY date DESC
        """
        df = pd.read_sql_query(text(query), engine, params=params)
        engine.dispose()

        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.strftime('%Y-%m-%d %H:%M:%S')

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            worksheet = writer.book.add_worksheet("FilteredData")
            writer.sheets['FilteredData'] = worksheet

            for i, filt in enumerate(applied_filters):
                worksheet.write(i, 0, filt)

            df_start_row = len(applied_filters) + 2
            df.to_excel(writer, sheet_name='FilteredData', index=False, startrow=df_start_row)

        output.seek(0)
        return send_file(
            output,
            as_attachment=True,
            download_name='printer_logs_filtered.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        flash(f"Error generating Excel: {e}")
        return redirect(url_for('routes.view'))


@routes.route('/dashboard')
def dashboard():
    time_filter = request.args.get('time_filter')
    location_filter = request.args.get('location_filter')
    division_filter = request.args.get('division_filter')
    date_input = request.args.get('date_input')
    month_input = request.args.get('month_input')
    year_input = request.args.get('year_input')
    week_select = request.args.get('week_select')

    if not request.args:
        now = datetime.now()
        last_month_date = now.replace(day=1) - timedelta(days=1)
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

        if location_filter and location_filter != 'all':
            df = df[df['location'] == location_filter]

        if division_filter and division_filter != 'all':
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
                year_int = filter_date.year
                df_time_graph1 = df[df['date'].dt.year == year_int].groupby('month')['pages_printed'].sum()
                month_str = filter_date.strftime("%b'%y")
                df = df[df['month'] == month_str]
        elif time_filter == 'yearly' and year_input:
            try:
                year_int = int(year_input)
                df = df[df['date'].dt.year == year_int]
            except ValueError:
                pass

        top_users = df.groupby('user_name')['pages_printed'].sum().sort_values(ascending=False).head(10).to_dict()

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

@routes.route('/dashboard/export')
def dashboard_export():
    time_filter = request.args.get('time_filter', 'all')
    location_filter = request.args.get('location_filter', 'all')
    division_filter = request.args.get('division_filter', 'all')
    date_input = request.args.get('date_input')
    month_input = request.args.get('month_input')
    year_input = request.args.get('year_input')
    week_select = request.args.get('week_select')

    try:
        engine = get_sqlalchemy_engine()
        query = """
            SELECT user_name, printer_model, hostname, location, division, pages_printed, date, month, week
            FROM printer_logs
        """
        df = pd.read_sql_query(query, engine)
        engine.dispose()

        df['date'] = pd.to_datetime(df['date'], errors='coerce')

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

        printer_group = df.groupby(['printer_model', 'hostname', 'location'])['pages_printed'].sum().reset_index()
        top_printers = printer_group.sort_values(by='pages_printed', ascending=False).head(10)
        least_printers = printer_group.sort_values(by='pages_printed', ascending=True).head(10)

        top_users = df.groupby('user_name')['pages_printed'].sum().sort_values(ascending=False).head(10).reset_index()

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

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            for sheet_name, data in [
                ('Top Users', top_users),
                ('Top Printers', top_printers),
                ('Least Used Printers', least_printers),
            ]:
                filter_df = pd.DataFrame(filter_summary)
                filter_df.to_excel(writer, sheet_name=sheet_name, index=False, header=False, startrow=0)
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
        return redirect(url_for('routes.dashboard'))

@routes.route('/exceptions', methods=['GET', 'POST'])
def exceptions():
    engine = get_sqlalchemy_engine()
    message = None
    error = None

    try:
        with engine.connect() as conn:
            printers_result = conn.execute(text("SELECT id, hostname, printer_model, location, division FROM printer_data WHERE hostname NOT IN (SELECT hostname FROM printer_exceptions) ORDER BY hostname "))
            printers_list = [dict(row._mapping) for row in printers_result]

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
        if request.form.get('add_zero_pages'):
            try:
                with engine.begin() as conn:
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
            selected_printer_ids = request.form.getlist('selected_printers')
            if not selected_printer_ids:
                error = "No printers selected to add as exceptions."
            else:
                try:
                    with engine.begin() as conn:
                        added_count = 0
                        for pid in selected_printer_ids:
                            print(f"Processing printer id: {pid}")
                            printer = conn.execute(
                                text("SELECT hostname, printer_model, location, division FROM printer_data WHERE id = :id"),
                                {'id': pid}
                            ).fetchone()
                            if printer:
                                print(f"Printer found: {printer}")
                                existing = conn.execute(
                                    text("SELECT id FROM printer_exceptions WHERE hostname = :hostname"),
                                    {'hostname': printer.hostname}
                                ).fetchone()
                                if not existing:
                                    print(f"Inserting exception for hostname: {printer.hostname}")
                                    try:
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
                                        print(f"Inserted exception for hostname: {printer.hostname}")
                                        added_count += 1
                                    except Exception as insert_exc:
                                        print(f"Error inserting exception for hostname {printer.hostname}: {insert_exc}")
                        message = f"Added {added_count} printer(s) to exceptions."
                except Exception as e:
                    error = f"Error adding exceptions: {e}"

    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT id, hostname, printer_model, location, division FROM printer_exceptions ORDER BY hostname"))
            exceptions_list = [dict(row._mapping) for row in result]
    except Exception as e:
        exceptions_list = []
        error = f"Error fetching exceptions: {e}"

    engine.dispose()

    return render_template('exceptions.html', exceptions=exceptions_list, printers=printers_list, divisions=divisions, locations=locations, message=message, error=error)

@routes.route('/exceptions/delete/<int:exception_id>', methods=['GET', 'POST'])
def delete_exception(exception_id):
    if request.method == 'GET':
        # Redirect or show friendly error for GET requests
        flash("Invalid request method for deleting exception. Please use the Remove button.")
        return redirect(url_for('routes.exceptions'))

    engine = get_sqlalchemy_engine()
    message = None
    error = None
    try:
        with engine.begin() as conn:
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
    if message:
        flash(message)
    if error:
        flash(error)
    return redirect(url_for('routes.exceptions'))

@routes.route('/dashboard/visualize')
def dashboard_visualize():
    try:
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

        if location_filter and location_filter != 'all':
            df = df[df['location'] == location_filter]

        if division_filter and division_filter != 'all':
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
                df = df[df['month'] == filter_date.strftime("%b'%y")]
        elif time_filter == 'yearly' and year_input:
            try:
                year_int = int(year_input)
                df = df[df['date'].dt.year == year_int]
            except ValueError:
                pass

        if time_filter in ['daily', 'weekly']:
            df_time = df.groupby(df['date'].dt.date)['pages_printed'].sum()
        elif time_filter == 'monthly':
            current_year = pd.Timestamp.now().year
            df_time_graph1 = df[df['date'].dt.year == current_year].groupby('month')['pages_printed'].sum()
            def parse_month_str(m):
                try:
                    return pd.to_datetime(m, format="%b'%y")
                except Exception:
                    return pd.NaT
            df_time_graph1.index = df_time_graph1.index.map(parse_month_str)
            df_time_graph1 = df_time_graph1.dropna()
            df_time_graph1 = df_time_graph1.sort_index()
            df_time = df_time_graph1
        else:
            df_time = df.groupby(df['date'].dt.year)['pages_printed'].sum()

        least_printers = df.groupby('printer_model')['pages_printed'].sum().sort_values(ascending=True).head(10)
        top_printers = df.groupby('printer_model')['pages_printed'].sum().sort_values(ascending=False).head(10)

        last_3_months = pd.Timestamp.now() - pd.DateOffset(months=3)

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

        def plot_to_base64(fig):
            buf = BytesIO()
            fig.savefig(buf, format='png', bbox_inches='tight')
            buf.seek(0)
            img_base64 = base64.b64encode(buf.read()).decode('utf-8')
            plt.close(fig)
            return img_base64

        fig1, ax1 = plt.subplots(figsize=(8, 4))
        if time_filter in ['daily', 'weekly']:
            df_time.plot(kind='bar', ax=ax1, color='blue')
            ax1.set_xlabel('Date')
        elif time_filter == 'monthly':
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
            ax1.set_xticklabels([dt.strftime('%b') for dt in df_time_graph1.index], rotation=45, ha='right')
        else:
            df_time = df_time.sort_index()
            df_time.plot(kind='bar', ax=ax1, color='blue')
            ax1.set_xlabel('Year')
        ax1.set_title('Pages Printed Over Time')
        ax1.set_ylabel('Pages Printed')
        ax1.grid(True)
        graph1 = plot_to_base64(fig1)

        fig2, ax2 = plt.subplots(figsize=(8, 4))
        least_printers.plot(kind='bar', ax=ax2, color='red')
        ax2.set_title('Pages Printed by Location (10 Least Used Printers)')
        ax2.set_xlabel('Printer Model')
        ax2.set_ylabel('Pages Printed')
        ax2.grid(axis='y')
        graph2 = plot_to_base64(fig2)

        fig3, ax3 = plt.subplots(figsize=(8, 4))
        top_printers.plot(kind='bar', ax=ax3, color='green')
        ax3.set_title('Pages Printed by Location (Top 10 Used Printers)')
        ax3.set_xlabel('Printer Model')
        ax3.set_ylabel('Pages Printed')
        ax3.grid(axis='y')
        graph3 = plot_to_base64(fig3)

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
