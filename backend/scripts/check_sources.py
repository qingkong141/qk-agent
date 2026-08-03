import sqlite3

conn = sqlite3.connect("data/agent.db")
cur = conn.cursor()
cur.execute(
    "SELECT id, role, substr(content, 1, 40), sources "
    "FROM messages WHERE role='assistant' ORDER BY id DESC LIMIT 5"
)
for row in cur.fetchall():
    print(row)
