from dotenv import load_dotenv
load_dotenv()
from backend.db import get_connection
import datetime

conn = get_connection()
cur = conn.cursor()

today = datetime.date.today()

# 1. Delete rows with impossible tx_date (year < 2000 or year > today's year)
cur.execute("""
    DELETE FROM transactions
    WHERE EXTRACT(YEAR FROM tx_date) < 2000
       OR EXTRACT(YEAR FROM tx_date) > %s
""", (today.year,))
print(f"Bad date rows deleted: {cur.rowcount}")

# 2. Remove duplicates — keep the row with the lowest ctid for each unique combo
cur.execute("""
    DELETE FROM transactions
    WHERE ctid NOT IN (
        SELECT MIN(ctid)
        FROM transactions
        GROUP BY full_name, ticker, side, tx_date, tx_estimate, chamber
    )
""")
print(f"Duplicate rows deleted: {cur.rowcount}")

conn.commit()
conn.close()
print("Done.")

