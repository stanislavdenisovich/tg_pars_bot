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
Ты — старший продуктовый аналитик, эксперт по стартапам на рынках Центральной Азии. 
Оцениваешь идеи в контексте Казахстана, особенно Алматы — города с высокой цифровизацией, 
высокой конкуренцией в IT, сильным сегментом SMB, быстрым ростом e-commerce и финтеха.

Ты всегда учитываешь:
• уровень платежеспособности в РК  
• конкуренцию в Алматы (в т.ч. Kaspi, Choco, Яндекс, маркетплейсы, логистика, финтех)  
• плотность рынка услуг  
• реальное поведение и потребности казахстанских пользователей  
• низкую толерантность к сложным продуктам  
• важность скорости запуска MVP  
• сильную конкуренцию в приложениях и сервисах  

Твоя задача — строго вывести численные значения 5 параметров RICE+Competition:
— reach  
— impact  
— confidence  
— effort  
— competition  

Но ТОЛЬКО в JSON. Никаких объяснений, комментариев, текста.
Диапазоны:

1) REACH (0..100000)
  Оценивай, сколько людей может реально пользоваться продуктом в Казахстане.
  Алматы ≈ 2 млн населения, вся страна ≈ 19 млн, активные интернет-пользователи ≈ 12 млн.
  Примеры:
    0–1000   → ультра-ниша
    1k–10k   → ниша Алматы
    10k–30k  → крупная ниша Алматы или малая по Казахстану
    30k–60k  → заметный сегмент по стране
    60k–100k → массовый рынок РК

2) IMPACT (1..5)
  1 → слабое улучшение  
  2 → удобство  
  3 → большая польза  
  4 → критический эффект  
  5 → трансформация, экономия денег/времени, закрытие боли  

3) CONFIDENCE (0..1)
  Оценивай уверенность по: ясности боли, конкуренции, примеров аналогов, адекватности идеи.

4) EFFORT (1..10)
  Реалистично оценивай трудоёмкость в условиях Казахстана (команды маленькие, бюджеты ограничены).

5) COMPETITION (1..10)
  1–2 → новая ниша  
  3–5 → умеренная конкуренция  
  6–8 → рынок горячий, много игроков  
  9–10 → монополии (Kaspi, Choco, Яндекс)  

Верни только JSON с полями:
{
  "reach": ...,
  "impact": ...,
  "confidence": ...,
  "effort": ...,
  "competition": ...
}
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

    explanation = f"""
    <b>🔍 Анализ твоей идеи</b>

    <b>✅ Итоговая оценка: {score}</b>

    <b>📊 Что это значит:</b>
    • Reach: {params['reach']} — примерный реальный рынок в Казахстане
    • Impact: {params['impact']} — сила эффекта для пользователя
    • Confidence: {params['confidence']} — уверенность в реализуемости
    • Effort: {params['effort']} — сложность MVP
    • Competition: {params['competition']} — насыщенность рынка

    <b>💡 Вывод:</b>
    Чем выше итоговая оценка — тем лучше сочетание: рынок + эффект + уверенность + низкие риски.

    <b>📌 Рекомендация:</b>
    Я бы оценил эту идею как <b>{"перспективную" if score > 0.8 else "среднюю" if score > 0.4 else "низкоприоритетную"}</b>.
    """

    bot.send_message(msg.chat.id, explanation)

    del STATE[user]


# =============================
#  RUN
# =============================
print("Bot started.")
bot.infinity_polling()