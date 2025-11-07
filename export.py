import sqlite3
import os

DB_PATH = "bot.db"
IDEAS_FILE = "past_ideas.txt"
OUT_FILE = "favorites_export.txt"


def load_ideas():
    if not os.path.exists(IDEAS_FILE):
        return []

    with open(IDEAS_FILE, "r", encoding="utf-8") as f:
        raw = f.read()

    blocks = [b.strip() for b in raw.split("\n--------------------------------------------------") if b.strip()]
    ideas = []

    for b in blocks:
        lines = [l for l in b.split("\n") if l.strip()]
        ideas.append(lines[0])

    return ideas


def load_favorites():
    if not os.path.exists(DB_PATH):
        return []

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    cur.execute("SELECT user_id, idea_index FROM favorites ORDER BY user_id, idea_index")
    rows = cur.fetchall()

    con.close()
    return rows


def export():
    ideas = load_ideas()
    favs = load_favorites()

    users = {}
    for user_id, idea_index in favs:
        if user_id not in users:
            users[user_id] = []
        users[user_id].append(idea_index)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        for user_id, idea_indices in users.items():
            f.write(f"Пользователь {user_id}:\n")

            for idx in idea_indices:
                title = ideas[idx] if idx < len(ideas) else "???"
                f.write(f" - {title}\n")

            f.write("\n")

    return OUT_FILE