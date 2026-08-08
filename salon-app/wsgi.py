import sys
import os

# Ensure the salon-app directory is in the path
sys.path.insert(0, os.path.dirname(__file__))

from app import app, init_db

# Initialize DB on startup
with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run()
