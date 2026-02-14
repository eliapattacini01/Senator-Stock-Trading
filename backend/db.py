# backend/db.py
import os
import psycopg

def get_connection():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set (Render: add it in Environment).")
    return psycopg.connect(db_url)
