from flask import Flask, request, render_template, redirect, url_for, flash, jsonify
import pandas as pd
from io import BytesIO
import pyodbc
from datetime import datetime
import logging
from flask_cors import CORS

# -------------------------------
# CONFIGURATION
# -------------------------------
app = Flask(__name__)
app.secret_key = 'supersecretkey'
CORS(app)

SQL_DRIVER = '{ODBC Driver 18 for SQL Server}'
SQL_SERVER = '10.3.2.121'
SQL_DATABASE = 'PrintDesk'
SQL_USERNAME = 'sa'
SQL_PASSWORD = 'Sql@2025'

def get_db_connection():
    conn_str = (
        f'DRIVER={SQL_DRIVER};SERVER={SQL_SERVER};DATABASE={SQL_DATABASE};'
        f'UID={SQL_USERNAME};PWD={SQL_PASSWORD};TrustServerCertificate=yes;'
    )
    return pyodbc.connect(conn_str)

# -------------------------------
# CREATE TABLE IF NOT EXISTS
# -------------------------------
def create_table_if_not_exists():
    sql = """
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
            user_name, document_name, hostname, pages_printed, date,
            month, week_1, printer_model, division, location
        )
    )
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"Error creating table: {e}")

# -------------------------------
# INSERT DATA TO DB
# -------------------------------
def insert_data_to_db(df):
    duplicates = []
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        check_sql = """
        SELECT COUNT(*) FROM printer_logs 
        WHERE user_name = ? AND document_name = ? AND hostname = ? AND pages_printed = ? 
        AND date = ? AND month = ? AND week_1 = ? AND printer_model = ? AND division = ? AND location = ?
        """
        insert_sql = """
        INSERT INTO printer_logs (document_name, user_name, hostname, pages_printed, date,
        month, week_1, printer_model, division, location)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        for _, row in df.iterrows():
            row_data = [
                row['user name'], row['document name'], row['hostname'],
                int(row['pages printed']), row['date'], row['month'],
                row['week 1'], row['printer model'], row['division'], row['location']
            ]
            cur.execute(check_sql, row_data)
            if cur.fetchone()[0] > 0:
                duplicates.append(dict(zip([
                    'user_name', 'document_name', 'hostname', 'pages_printed', 'date',
                    'month', 'week_1', 'printer_model', 'division', 'location'
                ], row_data)))
            else:
                cur.execute(insert_sql, row_data[1:])
        conn.commit()
        cur.close()
        conn.close()
        return duplicates
    except Exception as e:
        logging.error(f"Insertion error: {e}")
        return None

# -------------------------------
# DATE PARSING FUNCTION
# -------------------------------
date_formats = [
    '%Y-%m-%d %H:%M:%S', '%d-%m-%Y %H:%M:%S', '%Y-%m-%d', '%d-%m-%Y',
    '%m/%d/%Y', '%d/%m/%Y', '%b %d %Y', '%B %d %Y', '%b %d, %Y',
    '%B %d, %Y', '%Y/%m/%d', '%m-%d-%Y', '%d %b %Y', '%d %B %Y',
    '%Y.%m.%d', '%d.%m.%Y', '%Y%m%d'
]

def parse_date(text):
    for fmt in date_formats:
        try:
            return datetime.strptime(str(text), fmt)
        except:
            continue
    return pd.NaT

# -------------------------------
# COMMON FILE PROCESSING FUNCTION
# -------------------------------
def process_file(file):
    df = pd.read_excel(BytesIO(file.read()))
    df.columns = [str(col).strip().lower() for col in df.columns]

    required_columns = {
        'user name', 'document name', 'hostname', 'pages printed',
        'date', 'month', 'week 1', 'printer model', 'division', 'location'
    }

    if not required_columns.issubset(df.columns):
        raise ValueError(f"Missing required columns: {required_columns - set(df.columns)}")

    df['date'] = df['date'].astype(str).str.strip().apply(parse_date)
    df = df.dropna(subset=['date'])

    df['pages printed'] = pd.to_numeric(df['pages printed'], errors='coerce')
    df = df.dropna(subset=['pages printed'])

    return df

# -------------------------------
# ROUTES
# -------------------------------
@app.route('/')
def home():
    return render_template("home.html")

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if request.method == 'POST':
        file = request.files.get('file')
        if not file:
            flash("No file uploaded.")
            return redirect(url_for('upload'))
        try:
            create_table_if_not_exists()
            df = process_file(file)
            duplicates = insert_data_to_db(df)
            flash(f"Upload successful. {len(df)} rows processed. {len(duplicates)} duplicates skipped.")
            return redirect(url_for('view'))
        except Exception as e:
            flash(f"Error: {e}")
            return redirect(url_for('upload'))
    return render_template("upload.html")

@app.route('/view')
def view():
    try:
        conn = get_db_connection()
        df = pd.read_sql("SELECT * FROM printer_logs", conn)
        conn.close()
        df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d %H:%M:%S')
        return render_template("view.html", data=df)
    except Exception as e:
        flash(f"Error loading data: {e}")
        return render_template("view.html", data=None)

@app.route('/dashboard')
def dashboard():
    return render_template("dashboard.html")

# -------------------------------
# REST API ENDPOINTS
# -------------------------------
@app.route('/api/upload', methods=['POST'])
def api_upload():
    file = request.files.get('file')
    if not file:
        return jsonify({'error': 'No file uploaded'}), 400
    try:
        create_table_if_not_exists()
        df = process_file(file)
        duplicates = insert_data_to_db(df)
        return jsonify({
            'message': f"{len(df)} rows inserted. {len(duplicates)} duplicates skipped.",
            'duplicates': duplicates[:5]  # preview
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/logs', methods=['GET'])
def api_logs():
    try:
        conn = get_db_connection()
        df = pd.read_sql("SELECT * FROM printer_logs", conn)
        conn.close()
        df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d %H:%M:%S')
        return jsonify(df.to_dict(orient='records'))
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# -------------------------------
# RUN THE APP ON NETWORK
# -------------------------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
