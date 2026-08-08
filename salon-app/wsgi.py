import sys
import os

# Ensure the salon-app directory is in the path
sys.path.insert(0, os.path.dirname(__file__))

from app import app, init_db

# Initialize DB on startup
with app.app_context():
    init_db()

if __name__ == "__main__":
    port = int(os.environ.get('PORT', os.environ.get('FLASK_PORT', 5000)))
    app.run(host='127.0.0.1', port=port)
