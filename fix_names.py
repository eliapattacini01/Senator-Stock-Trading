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

# 2. Strip leading non-alphabetic characters (e.g. ".. Smith" → "Smith") from House members
cur.execute("""
    UPDATE transactions
    SET full_name = regexp_replace(full_name, '^[^a-zA-Z]+', '')
    WHERE chamber = 'House' AND full_name ~ '^[^a-zA-Z]'
""")
print(f"House names with leading dots/junk fixed: {cur.rowcount}")

# 3. Remove embedded '..' artifacts (e.g. "John .. Smith" → "John Smith")
cur.execute("""
    UPDATE transactions
    SET full_name = regexp_replace(full_name, '\\s*\\.\\.\\s*', ' ', 'g')
    WHERE chamber = 'House' AND full_name LIKE '%..%'
""")
print(f"House names with embedded '..' fixed: {cur.rowcount}")

# 4. Apply same name fixes to leaderboard_cache
cur.execute("""
    UPDATE leaderboard_cache
    SET full_name = regexp_replace(full_name, '^[^a-zA-Z]+', '')
    WHERE full_name ~ '^[^a-zA-Z]'
""")
print(f"leaderboard_cache leading dots fixed: {cur.rowcount}")

cur.execute("""
    UPDATE leaderboard_cache
    SET full_name = regexp_replace(full_name, '\\s*\\.\\.\\s*', ' ', 'g')
    WHERE full_name LIKE '%..%'
""")
print(f"leaderboard_cache embedded dots fixed: {cur.rowcount}")

# 5. Remove duplicates — keep the row with the lowest ctid for each unique combo
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

