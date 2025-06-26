from flask import Flask
from routes import routes

app = Flask(__name__, static_folder='static', static_url_path='/static')
app.secret_key = 'supersecretkey'

app.register_blueprint(routes)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
