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

# Global variable to hold uploaded data in memory
data_store = None

# Basic HTML templates inline for simplicity

@app.route('/')
def home():
    return render_template("home.html")

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    global data_store

    if request.method == 'POST':
        file = request.files.get('file')
        if not file:
            flash('No file uploaded.')
            return redirect(url_for('upload'))

        try:
            # Read Excel file into DataFrame
            in_memory_file = BytesIO(file.read())
            df = pd.read_excel(in_memory_file)

            # Validate columns
            expected_cols = {'date', 'time', 'printer_name', 'user_name', 'no_of_pages', 'location'}
            df.columns = [col.lower() for col in df.columns]  # Normalize first
            if not expected_cols.issubset(set(df.columns)):
                flash(f"Excel file must contain columns: {expected_cols}")
                return redirect(url_for('upload'))

            # Convert date column to datetime
            df['date'] = pd.to_datetime(df['date'], errors='coerce')
            if df['date'].isnull().any():
                flash("Some dates could not be parsed. Please fix the Excel file.")
                return redirect(url_for('upload'))

            # Convert no_of_pages to numeric
            df['no_of_pages'] = pd.to_numeric(df['no_of_pages'], errors='coerce')
            if df['no_of_pages'].isnull().any():
                flash("Some 'no_of_pages' values are invalid.")
                return redirect(url_for('upload'))

            # Store in global variable
            data_store = df

            flash('File uploaded and processed successfully!')
            return redirect(url_for('view'))

        except Exception as e:
            flash(f"Error processing file: {e}")
            return redirect(url_for('upload'))

    return render_template('upload.html')

@app.route('/view')
def view():
    global data_store
    return render_template('view.html', data=data_store)

@app.route('/dashboard')
def dashboard():
    global data_store
    if data_store is None:
        return render_template('dashboard.html', data=None)

    # Filters from query params
    time_filter = request.args.get('time_filter', 'all')
    location_filter = request.args.get('location_filter', 'all')

    df = data_store.copy()

    # Apply location filter
    if location_filter != 'all':
        df = df[df['location'] == location_filter]

    # Apply time filter
    if time_filter != 'all':
        if time_filter == 'daily':
            df['date_key'] = df['date'].dt.date
        elif time_filter == 'monthly':
            df['date_key'] = df['date'].dt.to_period('M')
        elif time_filter == 'yearly':
            df['date_key'] = df['date'].dt.year
        # Group by the date_key if you want summaries by date_key - but for simplicity we skip grouping here

    # Compute stats
    top_users = df.groupby('user_name')['no_of_pages'].sum().sort_values(ascending=False).head(10).to_dict()
    top_printers = df.groupby('printer_name')['no_of_pages'].sum().sort_values(ascending=False).head(10).to_dict()
    least_printers = df.groupby('printer_name')['no_of_pages'].sum().sort_values(ascending=True).head(10).to_dict()

    locations = sorted(df['location'].unique())

    return render_template(
        'dashboard.html',
        data=df,
        top_users=top_users,
        top_printers=top_printers,
        least_printers=least_printers,
        time_filter=time_filter,
        location_filter=location_filter,
        locations=locations
    )

if __name__ == '__main__':
    app.run(debug=True)
