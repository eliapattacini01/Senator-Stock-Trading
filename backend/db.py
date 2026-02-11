import psycopg
import os
from dotenv import load_dotenv

load_dotenv()  # loads backend/.env if present
def get_connection():
    return psycopg.connect(
        os.environ["DATABASE_URL"]
    )

