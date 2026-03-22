import anthropic
import os
import json
from dotenv import load_dotenv
import sys
sys.path.append(".")  # so Python finds the backend module

from backend.db import get_connection

load_dotenv()
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
tools = [
    {
        "name": "get_transactions",
        "description": "Fetch recent congressional stock transactions. Use this when the user asks about trades made by senators or representatives.",
        "input_schema": {
            "type": "object",
            "properties": {
                "senator": {"type": "string", "description": "Full name of the senator or representative"},
                "ticker":  {"type": "string", "description": "Stock ticker symbol e.g. AAPL, NVDA"},
                "side":    {"type": "string", "enum": ["BUY", "SELL"]},
                "limit":   {"type": "integer", "default": 10}
            },
            "required": []
        },
    },
    {
    "name": "get_prices",
    "description": "Fetch recent stock prices. Use this when the user asks about current or historical stock prices.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ticker":  {"type": "string", "description": "Stock ticker symbol e.g. AAPL, NVDA"},
            "date":    {"type": "string", "description": "Date of the price"},
            "price":   {"type": "number", "description": "Stock price"},
            "start_date": {"type": "string", "description": "Start date for price range e.g. 2025-01-01"},
            "end_date":   {"type": "string", "description": "End date for price range e.g. 2025-03-20"},
            "limit":   {"type": "integer", "default": 10}
        },
        "required": []
        }
    }
    {
      "name": "get_top_stocks",
      "description": "Get the most traded stocks by senators and representatives. Use this when the user asks about top stocks, most popular stocks, most bought or sold stocks in a given period.",
      "input_schema": {
          "type": "object",
          "properties": {
              "period": {
                  "type": "string",
                  "enum": ["1M", "3M", "YTD", "1Y"],
                  "description": "Time period: 1M = last month, 3M = last 3 months, YTD = this year, 1Y = last year"
              },
              "side": {
                  "type": "string",
                  "enum": ["BUY", "SELL"],
                  "description": "Filter for bought or sold stocks"
              },
              "limit": {"type": "integer", "default": 5}
          },
          "required": []
      }
    }
    ]

def get_transactions(senator=None, ticker=None, side=None, limit=10):
    conn = get_connection()
    try:
        cur = conn.cursor()
        sql    = "SELECT full_name, ticker, side, tx_date, tx_estimate FROM transactions WHERE 1=1"
        params = []

        if senator:
            sql += " AND full_name ILIKE %s"; params.append(f"%{senator}%")
        if side:
            sql += " AND side = %s"; params.append(side)
        if ticker:
            sql += " AND ticker = %s"; params.append(ticker.upper())

        sql += " ORDER BY tx_date DESC LIMIT %s"
        params.append(limit)

        cur.execute(sql, params)
        rows = cur.fetchall()
        return [
            {"full_name": r[0], "ticker": r[1], "side": r[2],
            "tx_date": str(r[3]), "tx_estimate": r[4]}
            for r in rows
        ]
    finally:
        conn.close()

def get_prices(ticker=None, date=None, price=None, start_date=None, end_date=None, limit=10):
    conn = get_connection()
    try:
        cur = conn.cursor()
        sql    = "SELECT ticker, date, price FROM prices WHERE 1=1"
        params = []

        if ticker:
            sql += " AND ticker = %s"; params.append(ticker.upper())
        if date:
            sql += " AND date = %s"; params.append(date)
        if price:
            sql += " AND price = %s"; params.append(price)
        if start_date:
            sql += " AND date >= %s"; params.append(start_date)
        if end_date:
            sql += " AND date <= %s"; params.append(end_date)

        sql += " ORDER BY date DESC LIMIT %s"
        params.append(limit)

        cur.execute(sql, params)
        rows = cur.fetchall()
        return [
            {"ticker": r[0], "date": str(r[1]), "price": r[2]}
            for r in rows
        ]
    finally:
        conn.close()
def get_top_stocks(period="1Y", side=None, limit=5):
      conn = get_connection()
      try:
          cur = conn.cursor()
          sql = """
              SELECT side, ticker, n_trades, n_members, price_change
              FROM top_stocks_cache
              WHERE period = %s AND rank <= %s
          """
          params = [period, limit]

          if side:
              sql += " AND side = %s"
              params.append(side)

          sql += " ORDER BY side, rank ASC"

          cur.execute(sql, params)
          rows = cur.fetchall()
          return [
              {"side": r[0], "ticker": r[1], "n_trades": r[2],
               "n_members": r[3], "price_change": r[4]}
              for r in rows
          ]
      finally:
          conn.close()
# --- Tool router: maps tool name → actual function ---
def run_tool(name, inputs):
    if name == "get_transactions":
        return get_transactions(**inputs)
    if name == "get_prices":
        return get_prices(**inputs)
    if name == "get_top_stocks":
      return get_top_stocks(**inputs)
    return {"error": "unknown tool"}
# --- Main loop ---
while True:
    user_input = input("Ask a question: ")

    messages = [
        {"role": "user", "content": user_input}
    ]
    while True:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            system="You are a helpful assistant for a congressional stock trading tracker. "
            "You only answer questions about U.S. senators and representatives stock trades, "
            "portfolios, and financial disclosures. "
            "If the user asks about anything unrelated, politely decline and remind them "
            "what you can help with.",
            max_tokens=1024,
            tools=tools,
            messages=messages
        )
        # If Claude is done, print the answer and exit
        if response.stop_reason == "end_turn":
            print("\nClaude:", response.content[0].text)
            messages.append({"role": "assistant", "content": response.content[0].text})
            break
        # If Claude wants to call a tool
        if response.stop_reason == "tool_use":
            # Add Claude's response to message history
            messages.append({"role": "assistant", "content": response.content})
            # Process each tool call
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    print(f"\n[Tool called]: {block.name} with {block.input}")
                    result = run_tool(block.name, block.input)
                    print(f"[Tool result]: {result}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result)
                    })
            # Send the results back to Claude
            messages.append({"role": "user", "content": tool_results})