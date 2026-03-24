"""
One-time script: builds frontend/legislators.json
Maps every full_name in the transactions DB to a Congress bioguide ID.

Photo URL pattern:
  https://theunitedstates.io/images/congress/225x275/{bioguide_id}.jpg

Run: python build_legislators.py
"""
import json
import os
import re
import urllib.request

from dotenv import load_dotenv
load_dotenv()

from backend.db import get_connection

CURRENT_URL    = "https://unitedstates.github.io/congress-legislators/legislators-current.json"
HISTORICAL_URL = "https://unitedstates.github.io/congress-legislators/legislators-historical.json"
OUT_PATH       = os.path.join(os.path.dirname(__file__), "frontend", "legislators.json")

_SUFFIX_RE = re.compile(
    r'\b(jr\.?|sr\.?|ii|iii|iv|v|hon\.?|dr\.?|mr\.?|mrs\.?|ms\.?|rep\.?|sen\.?)\b',
    re.IGNORECASE,
)

def norm_key(name: str) -> tuple:
    """Same (first, last) key logic as backend/main.py."""
    n = name.lower()
    n = re.sub(r'[.,\-]', ' ', n)
    n = _SUFFIX_RE.sub(' ', n)
    n = re.sub(r'\s+', ' ', n).strip()
    tokens = n.split()
    if not tokens:
        return (name.lower(), name.lower())
    if len(tokens) == 1:
        return (tokens[0], tokens[0])
    return (tokens[0], tokens[-1])


def download(url: str) -> list:
    print(f"  Downloading {url} …")
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read())


# ── 1. Build norm_key → bioguide_id from both datasets ─────────────────────
print("Fetching Congress legislators datasets…")
legislators = download(CURRENT_URL) + download(HISTORICAL_URL)
print(f"  {len(legislators)} total legislators loaded")

key_to_bioguide: dict[tuple, str] = {}

for leg in legislators:
    bioguide_id = leg.get("id", {}).get("bioguide")
    if not bioguide_id:
        continue

    name_obj = leg.get("name", {})
    first    = name_obj.get("first", "")
    last     = name_obj.get("last", "")
    nickname = name_obj.get("nickname", "")
    official = name_obj.get("official_full", "")

    candidates = []
    if official:
        candidates.append(official)
    if first and last:
        candidates.append(f"{first} {last}")
    if nickname and last:
        candidates.append(f"{nickname} {last}")

    for name in candidates:
        key = norm_key(name)
        if key not in key_to_bioguide:   # first match wins (current before historical)
            key_to_bioguide[key] = bioguide_id

    # Also index by (last_alpha, last) to catch "JD Vance" vs "J.D. Vance"
    # Strip all non-alpha from the first token to unify "J.D." and "JD"
    if first and last:
        first_alpha = re.sub(r'[^a-z]', '', first.lower())
        last_norm   = norm_key(f"x {last}")[1]   # just the last-name token
        alpha_key   = (first_alpha, last_norm)
        if alpha_key not in key_to_bioguide:
            key_to_bioguide[alpha_key] = bioguide_id
        if nickname:
            nick_alpha = re.sub(r'[^a-z]', '', nickname.lower())
            nick_key   = (nick_alpha, last_norm)
            if nick_key not in key_to_bioguide:
                key_to_bioguide[nick_key] = bioguide_id

print(f"  {len(key_to_bioguide)} unique (first, last) keys indexed")

# ── 2. Load all names from the DB ───────────────────────────────────────────
print("Querying DB for member names…")
conn = get_connection()
cur  = conn.cursor()
cur.execute("SELECT DISTINCT full_name FROM transactions WHERE full_name IS NOT NULL")
db_names = [r[0] for r in cur.fetchall()]
conn.close()
print(f"  {len(db_names)} distinct names found")

# ── 3. Match and build output mapping ───────────────────────────────────────
result: dict[str, str] = {}
unmatched: list[str]   = []

for name in db_names:
    key = norm_key(name)
    bio = key_to_bioguide.get(key)
    if bio:
        result[name] = bio
    else:
        unmatched.append(name)

matched_pct = 100 * len(result) / max(len(db_names), 1)
print(f"  Matched {len(result)} / {len(db_names)} names ({matched_pct:.1f}%)")

if unmatched:
    print(f"  Unmatched ({len(unmatched)}):")
    for n in sorted(unmatched)[:20]:
        print(f"    {n!r}")
    if len(unmatched) > 20:
        print(f"    … and {len(unmatched) - 20} more")

# ── 4. Save ─────────────────────────────────────────────────────────────────
with open(OUT_PATH, "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2, sort_keys=True)

print(f"\nSaved: {OUT_PATH}  ({os.path.getsize(OUT_PATH) // 1024} KB)")
