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
# STATE
# =============================
STATE = {}   # user_id → {mode, idea}


# =============================
# /start
# =============================
@bot.message_handler(commands=["start"])
def start(msg):
    STATE[msg.from_user.id] = {"mode": "wait_idea"}

    bot.send_message(
        msg.chat.id,
        "🔥 Напиши свою идею стартапа одним сообщением, как хочешь.\n\n"
        "Я её структурирую, улучшу и покажу в виде готового описания.\n"
        "После ты сможешь нажать «Принять» или «Редактировать»."
    )


# =============================
# GPT: структурирование идеи
# =============================
def expand_idea(raw_text):
    prompt = f"""
Ты — эксперт-продуктолог. Пользователь написал идею стартапа (неструктурированно):

\"\"\"{raw_text}\"\"\"

Твоя задача:
• перепиши её красиво, структурировано и понятно
• сохрани суть
• добавь недостающие детали, которые логически следуют из описания
• сделай таким образом, чтобы её можно было оценить по модели RICE+

Формат вывода:
ТОЛЬКО текст описания, без списка, без JSON, без комментариев.
"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.4,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.choices[0].message.content.strip()
# =============================
# ChatGPT RICE+ анализ
# =============================
def ask_chatgpt(idea_text):
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
{idea_text}

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
def compute_score(R, I, C, E, K):
    R_norm = math.log(1 + max(R, 0)) ** 0.9
    I_w = I ** 1.2
    E_w = E ** 1.8
    K_w = K ** 1.5
    C_w = C ** 0.7
    return round((R_norm * I_w * C_w) / (E_w * K_w), 4)


# =============================
# SAVE
# =============================
def save_result(user_id, idea, params, score):
    with open("results.txt", "a", encoding="utf-8") as f:
        f.write("\n============================\n")
        f.write(f"Дата: {datetime.now()}\n")
        f.write(f"User ID: {user_id}\n\n")
        f.write("Идея:\n")
        f.write(idea + "\n\n")
        f.write("Параметры RICE+:\n")
        f.write(json.dumps(params, ensure_ascii=False, indent=2))
        f.write(f"\nScore: {score}\n")
        f.write("============================\n")


# =============================
# МЕССЕДЖИ
# =============================
@bot.message_handler(func=lambda m: True)
def main_handler(msg):
    user = msg.from_user.id

    # Если только начали — пришла сырая идея
    if user not in STATE or STATE[user]["mode"] == "wait_idea":
        raw = msg.text

        bot.send_message(msg.chat.id, "✍️ Обрабатываю, структурирую идею...")
        expanded = expand_idea(raw)

        # кнопки
        kb = ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add(KeyboardButton("✅ Принять"), KeyboardButton("✏️ Редактировать"))

        STATE[user] = {"mode": "confirm", "idea": expanded}

        bot.send_message(
            msg.chat.id,
            f"📄 <b>Вот улучшенная версия идеи:</b>\n\n{expanded}\n\nВыбери действие:",
            reply_markup=kb
        )
        return

    # Если на этапе подтверждения
    if STATE[user]["mode"] == "confirm":
        if msg.text == "✅ Принять":
            idea = STATE[user]["idea"]

            bot.send_message(msg.chat.id, "✅ Отлично! Оцениваю идею по RICE+...")

            params = ask_chatgpt(idea)
            score = compute_score(
                R=params["reach"],
                I=params["impact"],
                C=params["confidence"],
                E=params["effort"],
                K=params["competition"]
            )            
            save_result(user, idea, params, score)

            bot.send_message(
                msg.chat.id,
                f"""
<b>🔍 Анализ твоей идеи</b>

<b>Итоговая оценка: {score}</b>

📊 <b>Параметры:</b>
• Reach: {params['reach']}
• Impact: {params['impact']}
• Confidence: {params['confidence']}
• Effort: {params['effort']}
• Competition: {params['competition']}

<b>💡 Вывод:</b>
{"🔥 Очень высокий потенциал — можно запускать!" if score > 0.8 else
 "✅ Идея перспективная, но требует уточнений." if score > 0.4 else
 "⚠️ Идея слабая — маленький рынок или высокая конкуренция."}
""",
                reply_markup=telebot.types.ReplyKeyboardRemove()
            )

            STATE.pop(user, None)
            return

        elif msg.text == "✏️ Редактировать":
            bot.send_message(
                msg.chat.id,
                "✏️ Напиши новую версию идеи любым текстом.",
                reply_markup=telebot.types.ReplyKeyboardRemove()
            )
            STATE[user] = {"mode": "wait_idea"}
            return

        else:
            bot.send_message(msg.chat.id, "Выбери кнопку: ✅ Принять или ✏️ Редактировать.")
            return


# =============================
# RUN
# =============================
print("Bot started.")
bot.infinity_polling()