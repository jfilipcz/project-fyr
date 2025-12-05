import sqlite3
import json

conn = sqlite3.connect('project_fyr.db')
cursor = conn.cursor()

print("--- Rollouts ---")
cursor.execute("SELECT id, deployment, status, analysis_status FROM rollouts")
for row in cursor.fetchall():
    print(row)

print("\n--- Analyses ---")
cursor.execute("SELECT id, rollout_id, analysis FROM analyses")
for row in cursor.fetchall():
    print(f"ID: {row[0]}, RolloutID: {row[1]}")
    analysis = json.loads(row[2])
    print(f"Summary: {analysis.get('summary')}")

conn.close()
