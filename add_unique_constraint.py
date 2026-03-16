from dotenv import load_dotenv
load_dotenv()
from backend.db import get_connection

conn = get_connection()
cur = conn.cursor()

cur.execute("""
    ALTER TABLE transactions
    DROP CONSTRAINT IF EXISTS uq_transactions_core;
""")
cur.execute("""
    ALTER TABLE transactions
    ADD CONSTRAINT uq_transactions_core
    UNIQUE (full_name, ticker, side, tx_date, chamber, tx_estimate);
""")
conn.commit()
conn.close()
print("Unique constraint added.")
