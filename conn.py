import pyodbc

# SQL Server connection configuration - replace placeholders with your actual values
SQL_SERVER = '10.3.2.121,63973'
SQL_DATABASE = 'PrintDesk'
SQL_USERNAME = 'sa'
SQL_PASSWORD = 'Sql@2025'
SQL_DRIVER = '{ODBC Driver 18 for SQL Server}'  # Adjust driver version if needed

def get_db_connection():
    """Create and return a new database connection."""
    conn_str = (
        f'DRIVER={SQL_DRIVER};'
        f'SERVER={SQL_SERVER};'
        f'DATABASE={SQL_DATABASE};'
        f'UID={SQL_USERNAME};'
        f'PWD={SQL_PASSWORD};'
        'TrustServerCertificate=yes;'
    )
    connection = pyodbc.connect(conn_str)
    return connection

    if request.method == 'POST':
        try:
            # Reconstruct DataFrame from form data
            form_data = request.form
            if data_store is None:
                flash("No data to update. Please upload an Excel file first.")
                return redirect(url_for('upload'))

            # Prepare a dictionary to hold columns and their values
            updated_data = {col: [] for col in data_store.columns}

            # Number of rows
            num_rows = len(data_store)

            # Iterate over rows and columns to get updated values
            for i in range(num_rows):
                for col in data_store.columns:
                    key = f"{col}_{i}"
                    val = form_data.get(key)
                    updated_data[col].append(val)

            # Create new DataFrame
            df_updated = pd.DataFrame(updated_data)

            # Convert columns to appropriate types
            df_updated['date'] = pd.to_datetime(df_updated['date'], errors='coerce')
            df_updated['no_of_pages'] = pd.to_numeric(df_updated['no_of_pages'], errors='coerce')

            # Check for conversion errors
            if df_updated['date'].isnull().any():
                flash("Some dates could not be parsed. Please fix the input data.")
                return redirect(url_for('view'))
            if df_updated['no_of_pages'].isnull().any():
                flash("Some 'no_of_pages' values are invalid.")
                return redirect(url_for('view'))

            # Update global data_store
            data_store = df_updated

            flash("Data updated successfully!")
            return redirect(url_for('view'))

        except Exception as e:
            flash(f"Error updating data: {e}")
            return redirect(url_for('view'))
