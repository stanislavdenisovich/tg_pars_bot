import os
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv
from openai import OpenAI
import math
import json
from datetime import datetime

load_dotenv()

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

if not TG_BOT_TOKEN:
    raise RuntimeError("TG_BOT_TOKEN отсутствует в .env")
if not OPENAI_KEY:
    raise RuntimeError("OPENAI_API_KEY отсутствует в .env")

bot = telebot.TeleBot(TG_BOT_TOKEN, parse_mode="HTML")
client = OpenAI(api_key=OPENAI_KEY)

USER_STATE = {}
ANSWERS = {}

QUESTIONS = [
    "1) Опиши проблему, которую решает твоя идея:",
    "2) Опиши само решение — что делает продукт:",
    "3) Кто целевая аудитория?",
    "4) Насколько массовая проблема? (в процентах или числах)",
    "5) Насколько сильно решение влияет на пользователя?"
]


# =============================
# /start
# =============================
@bot.message_handler(commands=["start"])
def start(msg):
    user = msg.from_user.id
    USER_STATE[user] = 0
    ANSWERS[user] = []

    bot.send_message(msg.chat.id,
        "🔥 Приступаем к оценке твоей идеи.\n"
        "Отвечай на следующие вопросы.\n\n"
        + QUESTIONS[0]
    )


# =============================
# Сбор ответов на вопросы
# =============================
@bot.message_handler(func=lambda m: m.from_user.id in USER_STATE)
def collect_answers(msg):
    user = msg.from_user.id
    step = USER_STATE[user]

    ANSWERS[user].append(msg.text)
    USER_STATE[user] += 1

    if USER_STATE[user] < len(QUESTIONS):
        bot.send_message(msg.chat.id, QUESTIONS[USER_STATE[user]])
    else:
        bot.send_message(msg.chat.id, "✅ Отлично. Иду считать оценку...")
        process_idea(msg.chat.id, user)


# =============================
# ChatGPT: Анализ идеи и получение R,I,C,E,K
# =============================
def ask_chatgpt(answers):
    prompt = f"""
Проанализируй стартап идею по пяти параметрам RICE+:

Данные пользователя:
1) Проблема: {answers[0]}
2) Решение: {answers[1]}
3) Аудитория: {answers[2]}
4) Масштаб: {answers[3]}
5) Эффект: {answers[4]}

Верни строго JSON с ключами:
reach — число
impact — число от 1 до 5
confidence — число 0–1
effort — число от 1 до 10
competition — число 1–10
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )

    text = response.choices[0].message["content"]

    try:
        data = json.loads(text)
        return data
    except:
        raise RuntimeError("ChatGPT вернул невалидный JSON:\n" + text)


# =============================
# Вычисление финального SCORE
# =============================
def compute_score(R, I, C, E, K, alpha=0.9, beta=1.2, gamma=0.7, delta=1.8, etha=1.5):
    R_norm = math.log(1 + max(R, 0)) ** alpha
    I_w = I ** beta
    E_w = E ** delta
    K_w = K ** etha
    C_w = C ** gamma

    return round((R_norm * I_w * C_w) / (E_w * K_w), 4)


# =============================
# Сохранение в results.txt
# =============================
def save_result(user_id, answers, params, score):
    with open("results.txt", "a", encoding="utf-8") as f:
        f.write("\n============================\n")
        f.write(f"Дата: {datetime.now()}\n")
        f.write(f"User ID: {user_id}\n")
        f.write("Описание идеи:\n")
        for a in answers:
            f.write(f" - {a}\n")
        f.write("\nПараметры:\n")
        for k, v in params.items():
            f.write(f"{k}: {v}\n")
        f.write(f"\nScore: {score}\n")
        f.write("============================\n")


# =============================
# Главная функция обработки
# =============================
def process_idea(chat_id, user):
    answers = ANSWERS[user]

    params = ask_chatgpt(answers)
    score = compute_score(
        R=params["reach"],
        I=params["impact"],
        C=params["confidence"],
        E=params["effort"],
        K=params["competition"]
    )

    save_result(user, answers, params, score)

    bot.send_message(chat_id,
        f"✅ Готово!\n\n"
        f"<b>Оценка идеи: {score}</b>\n\n"
        f"<pre>{json.dumps(params, indent=2, ensure_ascii=False)}</pre>"
    )

    del USER_STATE[user]
    del ANSWERS[user]


# =============================
# Запуск
# =============================
print("Bot started.")
bot.infinity_polling()