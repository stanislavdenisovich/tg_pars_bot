#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sqlite3
from typing import List, Dict
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from export import export

from dotenv import load_dotenv
load_dotenv()

API_TOKEN = os.getenv("TG_BOT_TOKEN")
ADMIN_ID = 1028456026
IDEAS_FILE = "past_ideas.txt"   # твой файл с 616+ идеями
DB_PATH = "bot.db"

if not API_TOKEN or ":" not in API_TOKEN:
    raise RuntimeError("TG_BOT_TOKEN не найден или некорректен. Проверь .env")

bot = telebot.TeleBot(API_TOKEN, parse_mode="HTML")

# ===================== Загрузка идей =====================

def load_ideas() -> List[Dict]:
    if not os.path.exists(IDEAS_FILE):
        raise FileNotFoundError(f"Файл {IDEAS_FILE} не найден рядом со скриптом.")

    with open(IDEAS_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()

    ideas = []
    current_block = []

    def flush_block(block):
        """Разбор одного блока идеи"""
        if not block:
            return None
        text = "\n".join(block)

        title = ""
        desc = ""
        categories = ""
        income = ""
        link = ""
        score = ""

        for line in block:
            line = line.strip()
            if not line:
                continue
            if re.match(r"^\d+\.", line):
                title = line
            elif line.startswith("Описание:"):
                desc = line.replace("Описание:", "", 1).strip()
            elif line.startswith("Категории:"):
                categories = line.replace("Категории:", "", 1).strip()
            elif line.startswith("Доход:"):
                income = line.replace("Доход:", "", 1).strip()
            elif line.startswith("Ссылка:"):
                link = line.replace("Ссылка:", "", 1).strip()
            elif line.startswith("Оценка:"):
                score = line.replace("Оценка:", "", 1).strip()

        if not title:
            return None

        return {
            "title": title,
            "desc": desc,
            "categories": categories,
            "income": income,
            "link": link,
            "score": score
        }

    import re

    for line in lines:
        if re.match(r"^\d+\.\s", line.strip()):
            # Новая идея началась → сохраняем предыдущую
            idea = flush_block(current_block)
            if idea:
                ideas.append(idea)
            current_block = [line.strip()]
        else:
            current_block.append(line.strip())

    # Добавить последнюю идею
    last_idea = flush_block(current_block)
    if last_idea:
        ideas.append(last_idea)

    if not ideas:
        raise RuntimeError("Не удалось извлечь ни одной идеи. Проверь формат файла.")

    return ideas


IDEAS = load_ideas()
TOTAL = len(IDEAS)
print(f"✅ Загружено идей: {TOTAL}")

# ===================== База данных =====================

def db_connect():
    # отдельное соединение на каждую операцию — безопаснее для многопоточности
    return sqlite3.connect(DB_PATH)

def init_db():
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                current_index INTEGER DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS favorites (
                user_id INTEGER,
                idea_index INTEGER,
                PRIMARY KEY (user_id, idea_index)
            )
        """)
        con.commit()

def get_current_index(user_id: int) -> int:
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("SELECT current_index FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()

        if row is None:
            cur.execute("INSERT INTO users (user_id, current_index) VALUES (?, ?)", (user_id, 0))
            con.commit()
            return 0

        idx = int(row[0])
        # Авто-правка индекса, если он вне диапазона
        if idx < 0 or idx >= TOTAL:
            idx = 0
            cur.execute("UPDATE users SET current_index = 0 WHERE user_id = ?", (user_id,))
            con.commit()
        return idx

def set_current_index(user_id: int, idx: int):
    # защита от выхода за пределы
    idx = max(0, min(TOTAL - 1, idx))
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO users(user_id, current_index)
            VALUES(?, ?)
            ON CONFLICT(user_id) DO UPDATE SET current_index=excluded.current_index
        """, (user_id, idx))
        con.commit()

def is_favorite(user_id: int, idea_index: int) -> bool:
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("SELECT 1 FROM favorites WHERE user_id = ? AND idea_index = ?", (user_id, idea_index))
        return cur.fetchone() is not None

def toggle_favorite(user_id: int, idea_index: int) -> bool:
    idea_index = max(0, min(TOTAL - 1, idea_index))
    with db_connect() as con:
        cur = con.cursor()
        if is_favorite(user_id, idea_index):
            cur.execute("DELETE FROM favorites WHERE user_id = ? AND idea_index = ?", (user_id, idea_index))
            con.commit()
            return False
        else:
            cur.execute("INSERT OR IGNORE INTO favorites(user_id, idea_index) VALUES (?, ?)", (user_id, idea_index))
            con.commit()
            return True

def list_favorites(user_id: int):
    with db_connect() as con:
        cur = con.cursor()
        cur.execute("SELECT idea_index FROM favorites WHERE user_id = ? ORDER BY idea_index ASC", (user_id,))
        rows = cur.fetchall()
        # фильтруем «битые» индексы, если файл обновлялся
        clean = [int(r[0]) for r in rows if 0 <= int(r[0]) < TOTAL]
        return clean

# ===================== UI =====================

def main_menu(user_id=None):
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row(KeyboardButton("📖 Смотреть идеи"), KeyboardButton("⭐ Избранное"))

    if user_id == ADMIN_ID:
        kb.row(KeyboardButton("📤 Экспорт избранных"))

    return kb

def idea_card_text(idea_index: int) -> str:
    # двойная защита от выхода за границы
    if idea_index < 0 or idea_index >= TOTAL:
        idea_index = 0
    idea = IDEAS[idea_index]

    desc = idea['desc'] or "—"
    if len(desc) > 1200:  # Телеге не нравятся слишком длинные сообщения
        desc = desc[:1200] + "…"

    categories = idea['categories'] or "—"
    income = idea['income'] or "—"
    score = idea['score'] or "—"
    link = idea['link'] or "#"

    text = (
        f"<b>{idea['title']}</b>\n\n"
        f"<b>Описание:</b>\n{desc}\n\n"
        f"<b>Категории:</b> {categories}\n"
        f"<b>Доход:</b> {income}\n"
        f"<b>Оценка:</b> {score}\n\n"
        f"🔗 <a href=\"{link}\">Открыть на сайте</a>\n"
        f"\n<i>Идея {idea_index+1} из {TOTAL}</i>"
    )
    return text

def idea_inline_kb(user_id: int, idea_index: int) -> InlineKeyboardMarkup:
    idea_index = max(0, min(TOTAL - 1, idea_index))
    fav = "⭐ Убрать из избранного" if is_favorite(user_id, idea_index) else "⭐ В избранное"
    kb = InlineKeyboardMarkup()
    kb.row(
        InlineKeyboardButton("⬅️ Назад", callback_data=f"prev:{idea_index}"),
        InlineKeyboardButton(fav, callback_data=f"fav:{idea_index}"),
        InlineKeyboardButton("➡️ Далее", callback_data=f"next:{idea_index}")
    )
    kb.row(InlineKeyboardButton("ℹ️ Оценка", callback_data=f"score:{idea_index}"))
    return kb

def favorites_list_kb(favs) -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup()
    row = []
    for idx in favs[:60]:  # не раздуваем сообщение
        title = f"#{idx+1}"
        row.append(InlineKeyboardButton(title, callback_data=f"open:{idx}"))
        if len(row) == 6:
            kb.row(*row); row = []
    if row:
        kb.row(*row)
    return kb

# ===================== Handlers =====================

@bot.message_handler(commands=["start"])
def on_start(msg):
    init_db()
    bot.send_message(
        msg.chat.id,
        "🔥 Привет! Это каталог идей стартапов.\n\n"
        "• Нажми «📖 Смотреть идеи» чтобы листать\n"
        "• Нажми «⭐ Избранное» чтобы увидеть сохранённые\n\n"
        "Удачи! 🚀",
        reply_markup=main_menu(msg.from_user.id)
    )

@bot.message_handler(commands=["menu"])
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() in ["меню", "📋 меню"])
def on_menu(msg):
    bot.send_message(msg.chat.id, "Главное меню:", reply_markup=main_menu())

import io

@bot.message_handler(func=lambda m: m.text == "📤 Экспорт избранных")
def export_favorites(msg):
    if msg.from_user.id != ADMIN_ID:
        return bot.send_message(msg.chat.id, "⛔ У вас нет доступа.")

    # читаем базу
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT user_id, idea_index FROM favorites ORDER BY user_id, idea_index")
    rows = cur.fetchall()
    con.close()

    if not rows:
        return bot.send_message(msg.chat.id, "⚠️ Нет данных в избранном.")

    # загружаем идеи
    with open(IDEAS_FILE, "r", encoding="utf-8") as f:
        raw = f.read()
    blocks = [b.strip() for b in raw.split("\n--------------------------------------------------") if b.strip()]
    idea_titles = [b.split("\n")[0] for b in blocks]

    # формируем текстовый файл в памяти
    output = io.StringIO()
    output.write("=== Экспорт избранных ===\n\n")

    users = {}
    for user_id, idea_index in rows:
        users.setdefault(user_id, []).append(idea_index)

    for uid, ideas in users.items():
        output.write(f"Пользователь {uid}:\n")
        for idx in ideas:
            name = idea_titles[idx] if idx < len(idea_titles) else "UNKNOWN"
            output.write(f" - {name}\n")
        output.write("\n")

    output.seek(0)

    bot.send_document(
        msg.chat.id,
        ("favorites_export.txt", output.read().encode("utf-8"))
    )

@bot.message_handler(commands=["ideas"])
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() in ["смотреть идеи", "📖 смотреть идеи"])
def on_ideas(msg):
    init_db()
    user_id = msg.from_user.id
    idx = get_current_index(user_id)

    # Жёсткая коррекция индекса перед показом
    if idx < 0 or idx >= TOTAL:
        idx = 0
        set_current_index(user_id, 0)

    text = idea_card_text(idx)
    kb = idea_inline_kb(user_id, idx)
    bot.send_message(msg.chat.id, text, reply_markup=kb)

@bot.message_handler(commands=["favorites"])
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() in ["избранное", "⭐ избранное"])
def on_favorites(msg):
    init_db()
    user_id = msg.from_user.id
    favs = list_favorites(user_id)
    if not favs:
        bot.send_message(msg.chat.id, "У тебя пока нет избранных идей. Нажимай ⭐ на карточках, чтобы добавлять.")
        return

    titles_preview = "\n".join([f"{i+1}. {IDEAS[i]['title']}" for i in favs[:10]])
    bot.send_message(
        msg.chat.id,
        f"⭐ Избранные идеи ({len(favs)}):\n\n{titles_preview}\n\nНажми на номер ниже, чтобы открыть:",
        reply_markup=favorites_list_kb(favs)
    )

@bot.message_handler(commands=["export"])
def on_export(msg):
    try:
        # создаём экспортный файл
        export()   # вызываем функцию из твоего export.py

        # отправляем файл в Telegram
        with open("favorites_export.txt", "rb") as f:
            bot.send_document(msg.chat.id, f)

    except Exception as e:
        bot.send_message(msg.chat.id, f"Ошибка экспорта: {e}")

@bot.callback_query_handler(func=lambda call: True)
def on_callback(call):
    init_db()
    user_id = call.from_user.id
    data = call.data.split(":")
    action = data[0]
    curr = int(data[1]) if len(data) > 1 and data[1].isdigit() else get_current_index(user_id)

    # Централизованная коррекция индекса из callback (вдруг пришёл старый/битый)
    if curr < 0 or curr >= TOTAL:
        curr = 0
        set_current_index(user_id, 0)

    if action == "next":
        new_idx = min(TOTAL - 1, curr + 1)
        set_current_index(user_id, new_idx)
        bot.edit_message_text(
            idea_card_text(new_idx),
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=idea_inline_kb(user_id, new_idx)
        )

    elif action == "prev":
        new_idx = max(0, curr - 1)
        set_current_index(user_id, new_idx)
        bot.edit_message_text(
            idea_card_text(new_idx),
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=idea_inline_kb(user_id, new_idx)
        )

    elif action == "fav":
        now_fav = toggle_favorite(user_id, curr)
        bot.answer_callback_query(call.id, "Добавлено в избранное ⭐" if now_fav else "Убрано из избранного")
        bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=idea_inline_kb(user_id, curr)
        )

    elif action == "open":
        idx = curr
        if idx < 0 or idx >= TOTAL:
            idx = 0
            set_current_index(user_id, 0)
        set_current_index(user_id, idx)
        bot.edit_message_text(
            idea_card_text(idx),
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=idea_inline_kb(user_id, idx)
        )

    elif action == "score":
        # Отдельное сообщение с кнопкой закрытия
        idea = IDEAS[curr]
        score_text = (
            f"<b>📊 Оценка идеи</b>\n\n"
            f"<b>Итоговая оценка:</b> {idea.get('score') or '—'}\n\n"
            f"RICE = Reach × Impact × Confidence ÷ Effort\n\n"
            f"<i>Reach</i> — сколько людей испытывают проблему\n"
            f"<i>Impact</i> — сила решения\n"
            f"<i>Confidence</i> — уверенность в успехе\n"
            f"<i>Effort</i> — сложность реализации\n"
        )
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("❌ Закрыть", callback_data="close_score"))
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, score_text, reply_markup=kb)

    elif action == "close_score":
        bot.answer_callback_query(call.id)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            # если сообщение уже удалено или нет прав — просто игнорируем
            pass

# ===================== Запуск =====================

if __name__ == "__main__":
    init_db()
    print(f"Bot started. Total ideas: {TOTAL}")
    print("Tip: export TG_BOT_TOKEN=xxxx before run")
    bot.infinity_polling()