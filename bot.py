import os
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from openai import OpenAI
import json
from datetime import datetime
import math

# =============================
#  ENV VARIABLES
# =============================
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

if not TG_BOT_TOKEN:
    raise RuntimeError("TG_BOT_TOKEN не найден в Railway → Variables")
if not OPENAI_KEY:
    raise RuntimeError("OPENAI_API_KEY не найден в Railway → Variables")

bot = telebot.TeleBot(TG_BOT_TOKEN, parse_mode="HTML")
client = OpenAI(api_key=OPENAI_KEY)

# =============================
#  STATE
# =============================
STATE = {}        # user_id → {"mode": "ask_questions" | "collect", "questions": [...], "answers": []}

# =============================
# /start
# =============================
@bot.message_handler(commands=["start"])
def start(msg):
    bot.send_message(
        msg.chat.id,
        "🔥 Напиши свою идею стартапа в одном сообщении.\n"
        "Я сам задам уточняющие вопросы, а потом оценю её по модели RICE+."
    )

    STATE[msg.from_user.id] = {"mode": "wait_idea"}


# =============================
# Генерация вопросов GPT
# =============================
def generate_questions(idea_text: str):
    prompt = f"""
Ты — эксперт-аналитик стартапов.
Пользователь дал идею:

\"\"\"{idea_text}\"\"\"

Сгенерируй 3–5 самых важных уточняющих вопросов,
которые нужны для корректной оценки идеи по метрикам
RICE (Reach, Impact, Confidence, Effort) + Competition.

Формат ответа: ТОЛЬКО JSON, пример:

{{
  "questions": [
    "Вопрос 1...",
    "Вопрос 2...",
    "Вопрос 3..."
  ]
}}
"""
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.choices[0].message.content

    # извлечение JSON
    try:
        return json.loads(raw)["questions"]
    except:
        import re
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            raise RuntimeError("Ошибка: GPT вернул не JSON:\n" + raw)
        return json.loads(m.group(0))["questions"]

# =============================
# ChatGPT анализ
# =============================
def ask_chatgpt(idea, q_list, a_list):
    """
    Возвращает словарь с числовыми полями:
    {
      "reach": int [0..100000],
      "impact": int [1..5],
      "confidence": float [0..1],
      "effort": int [1..10],
      "competition": int [1..10]
    }
    """

    rules = """
Ты — строгий аналитик стартапов, использующий модель RICE+Competition.
Всегда возвращай ТОЛЬКО JSON без текста, без комментариев.

Твоя задача — преобразовать ответы пользователя в количественные оценки по пяти метрикам:
reach (R), impact (I), confidence (C), effort (E), competition (K).

Используй следующие строгие правила:

----------------------------------------------------
1) REACH — размер потенциальной аудитории
----------------------------------------------------
• Тип: целое число
• Диапазон: 0..100000
• Это: сколько людей может пользоваться этим за месяц
• Интерпретация:
  0–999       → ультра-ниша
  1000–9999   → маленький рынок
  10000–29999 → средний рынок
  30000–59999 → крупный рынок
  60000–100000 → массовый рынок
• Учитывай:
  - географию
  - сегментацию
  - реальную доступность аудитории
  - частоту проблемы (если часто → больше R)

----------------------------------------------------
2) IMPACT — сила влияния решения
----------------------------------------------------
• Тип: целое число
• Диапазон: 1..5
• Интерпретация:
  1 = косметическое улучшение
  2 = заметное удобство
  3 = существенная польза
  4 = критичное улучшение
  5 = трансформационный эффект

----------------------------------------------------
3) CONFIDENCE — уверенность в данных
----------------------------------------------------
• Тип: float
• Диапазон: 0..1
• Округление: до двух знаков
• Интерпретация:
  0.2 — слабая уверенность, мало данных
  0.5 — средняя уверенность
  0.8 — высокая уверенность
• Зависит от:
  - чёткости проблемы
  - зрелости рынка
  - есть ли подтверждения спроса
  - есть ли аналоги/референсы

----------------------------------------------------
4) EFFORT — трудозатраты на MVP
----------------------------------------------------
• Тип: целое число
• Диапазон: 1..10

1–2: простой сайт / бот / форма  
3–4: веб-продукт + 1–2 интеграции  
5–6: мобильное приложение, платежи, авторизация  
7–8: real-time, сложные интеграции, ML  
9–10: высокий R&D, комплаенс, большие команды  

----------------------------------------------------
5) COMPETITION — уровень конкуренции
----------------------------------------------------
• Тип: целое число
• Диапазон: 1..10

1–2  → нет конкурентов, синяя зона  
3–5  → умеренная конкуренция  
6–8  → насыщенный рынок  
9–10 → рынок под монополистами  

----------------------------------------------------

Требования:
• Всегда соблюдай типы.
• Всегда укладывай значения в диапазоны.
• Если данных мало → оцени консервативно.
• Верни ТОЛЬКО JSON без текста.
"""

    user_data = f"""
Идея:
{idea}

Вопросы и ответы:
{json.dumps(list(zip(q_list, a_list)), ensure_ascii=False, indent=2)}

Верни JSON строго такого вида:
{{
  "reach": <int 0..100000>,
  "impact": <int 1..5>,
  "confidence": <float 0..1>,
  "effort": <int 1..10>,
  "competition": <int 1..10>
}}
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.1,
        messages=[
            {"role": "system", "content": rules.strip()},
            {"role": "user", "content": user_data.strip()}
        ]
    )

    text = response.choices[0].message.content

    # Жёсткая валидация JSON
    try:
        data = json.loads(text)
    except:
        import re
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            raise RuntimeError("ChatGPT вернул невалидный JSON:\n" + text)
        data = json.loads(m.group(0))

    # нормализация
    def clamp_int(v, lo, hi):
        try:
            v = int(round(float(v)))
        except:
            v = lo
        return max(lo, min(hi, v))

    def clamp_float(v, lo, hi, ndigits=2):
        try:
            v = float(v)
        except:
            v = lo
        v = max(lo, min(hi, v))
        return round(v, ndigits)

    return {
        "reach": clamp_int(data.get("reach", 0), 0, 100000),
        "impact": clamp_int(data.get("impact", 3), 1, 5),
        "confidence": clamp_float(data.get("confidence", 0.5), 0, 1),
        "effort": clamp_int(data.get("effort", 5), 1, 10),
        "competition": clamp_int(data.get("competition", 5), 1, 10)
    }

# =============================
# SCORE
# =============================
def compute_score(R, I, C, E, K, alpha=0.9, beta=1.2, gamma=0.7, delta=1.8, etha=1.5):
    import math
    R_norm = math.log(1 + max(R, 0)) ** alpha
    I_w = I ** beta
    E_w = E ** delta
    K_w = K ** etha
    C_w = C ** gamma

    return round((R_norm * I_w * C_w) / (E_w * K_w), 4)

# =============================
#  Сохранение
# =============================
def save_result(user_id, idea, questions, answers, params, score):
    with open("results.txt", "a", encoding="utf-8") as f:
        f.write("\n============================\n")
        f.write(f"Дата: {datetime.now()}\n")
        f.write(f"User ID: {user_id}\n")
        f.write(f"Идея: {idea}\n\n")
        f.write("Вопросы и ответы:\n")
        for q, a in zip(questions, answers):
            f.write(f"- {q}\n  {a}\n")
        f.write("\nОценка параметров:\n")
        f.write(json.dumps(params, ensure_ascii=False, indent=2))
        f.write(f"\nScore: {score}\n")
        f.write("============================\n")


# =============================
#  Основная логика сообщений
# =============================
@bot.message_handler(func=lambda m: True)
def all_messages(msg):
    user = msg.from_user.id

    # --- Шаг 1 — ждем идею ---
    if user not in STATE or STATE[user]["mode"] == "wait_idea":
        idea = msg.text
        bot.send_message(msg.chat.id, "✅ Получил идею. Генерирую уточняющие вопросы...")

        questions = generate_questions(idea)

        STATE[user] = {
            "mode": "collect",
            "idea": idea,
            "questions": questions,
            "answers": [],
            "index": 0
        }

        bot.send_message(msg.chat.id, f"❓ {questions[0]}")
        return

    # --- Шаг 2 — собираем ответы ---
    st = STATE[user]

    st["answers"].append(msg.text)
    st["index"] += 1

    if st["index"] < len(st["questions"]):
        bot.send_message(msg.chat.id, f"❓ {st['questions'][st['index']]}")
        return

    # --- Шаг 3 — все ответы получены ---
    bot.send_message(msg.chat.id, "✅ Супер! Оцениваю идею...")

    params = ask_chatgpt(st["idea"], st["questions"], st["answers"])

    score = compute_score(
        R=params["reach"],
        I=params["impact"],
        C=params["confidence"],
        E=params["effort"],
        K=params["competition"]
    )

    save_result(user, st["idea"], st["questions"], st["answers"], params, score)

    bot.send_message(
        msg.chat.id,
        f"🔥 Готово!\n\n"
        f"<b>Итоговая оценка: {score}</b>\n\n"
        f"<pre>{json.dumps(params, indent=2, ensure_ascii=False)}</pre>"
    )

    del STATE[user]


# =============================
#  RUN
# =============================
print("Bot started.")
bot.infinity_polling()