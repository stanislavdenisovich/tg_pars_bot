#!/usr/bin/env python3
# coding: utf-8
"""
Телеграм-бот: принимает текст идеи, проверяет её валидность через GPT,
извлекает RICE+ параметры, считает Score с использованием обученных весов
и истории, сохраняет результат и даёт краткий совет.
Основано на твоём коде — добавлена загрузка weights.json, адаптивная
логистическая шкала по истории (перцентили), устойчивый парсинг ответов
GPT и защита от ошибок.
"""

import os
import json
import re
import math
import time
from datetime import datetime
from dotenv import load_dotenv
import telebot
from openai import OpenAI

# ---------------------------
# Конфигурация / окружение
# ---------------------------
load_dotenv()

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

if not TG_BOT_TOKEN:
    raise RuntimeError("TG_BOT_TOKEN не найден в Railway → Variables")
if not OPENAI_KEY:
    raise RuntimeError("OPENAI_API_KEY не найден в Railway → Variables")

bot = telebot.TeleBot(TG_BOT_TOKEN, parse_mode="HTML")
client = OpenAI(api_key=OPENAI_KEY)

# ---------------------------
# Файлы и параметры
# ---------------------------
HISTORY_PATH = "score_history_raw.json"   # для хранения raw значений (используется для калибровки)
WEIGHTS_PATH = "weights.json"             # обученные веса (I, C, E, K)
RESULTS_PATH = "results.txt"              # лог результатов (человеко-читаемый)
# Параметры перцентилей для адаптивной шкалы
P_LOW = 5
P_HIGH = 95

# ---------------------------
# Утилиты
# ---------------------------
def safe_json_load(s):
    """Попытаться разобрать строку в JSON, найти первый {...} блок при необходимости."""
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", s)
        if not m:
            raise
        return json.loads(m.group(0))

def clamp_int(v, lo, hi):
    try:
        v = int(round(float(v)))
    except Exception:
        v = lo
    return max(lo, min(hi, v))

def clamp_float(v, lo, hi, ndigits=2):
    try:
        v = float(v)
    except Exception:
        v = lo
    return round(max(lo, min(hi, v)), ndigits)

# ---------------------------
# Загрузка/сохранение весов
# ---------------------------
def load_weights():
    # По умолчанию используем веса, которые ты получил
    defaults = {
        "I": 1.2235740102817168,
        "C": 0.6003549839399369,
        "E": 0.6068635928558199,
        "K": 1.7795459571198546
    }
    if os.path.exists(WEIGHTS_PATH):
        try:
            data = json.load(open(WEIGHTS_PATH, "r", encoding="utf-8"))
            # простая валидация
            for k in ("I","C","E","K"):
                if k not in data:
                    return defaults
            return data
        except Exception:
            return defaults
    return defaults

def save_weights(w):
    try:
        json.dump(w, open(WEIGHTS_PATH, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    except Exception as e:
        print("Ошибка сохранения weights.json:", e)

# ---------------------------
# Перцентиль (ручной)
# ---------------------------
def _percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * (p/100.0)
    f = math.floor(k); c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)

def _get_history_bounds(path=HISTORY_PATH, p_low=P_LOW, p_high=P_HIGH):
    try:
        if not os.path.exists(path):
            return None
        raw = json.load(open(path, "r", encoding="utf-8"))
        if not raw:
            return None
        vals = sorted(float(x) for x in raw)
        lo = _percentile(vals, p_low)
        hi = _percentile(vals, p_high)
        if lo is None or hi is None or hi <= lo:
            return None
        return (lo, hi)
    except Exception:
        return None

# ---------------------------
# compute_score — использует weights.json и историю
# ---------------------------
def compute_score(R, I, C, E, K):
    """
    R: reach (int)
    I: impact (1..5)
    C: confidence (0..1)
    E: effort (1..10)
    K: competition (1..10)
    Возвращает строку 'NN.N%'
    """

    weights = load_weights()
    I_w = (float(I) ** float(weights["I"]))
    C_w = (float(C) ** float(weights["C"]))
    E_w = (float(E) ** float(weights["E"]))
    K_w = (float(K) ** float(weights["K"]))

    # нормализация охвата (как в твоём коде)
    R_norm = math.log1p(max(0, float(R))) / math.log(100000)

    raw = (R_norm * I_w * C_w) / (E_w * K_w)

    # адаптивная логистическая шкала через историю
    bounds = _get_history_bounds()
    if bounds:
        lo, hi = bounds
        # защита от деления на 0
        width = hi - lo
        if width <= 0:
            x0 = 0.08
            k = 16
        else:
            x0 = (lo + hi) / 2.0
            # k выбираем так, чтобы ширина перехода была разумной
            k = max(1.0, 8.0 / width)
    else:
        x0 = 0.08
        k = 16.0

    # sigmoid -> [0,1]
    try:
        score_val = 1.0 / (1.0 + math.exp(-k * (raw - x0)))
    except OverflowError:
        score_val = 0.0 if (k * (raw - x0)) < 0 else 1.0

    score_val = max(0.01, min(1.0, score_val))

    # сохраняем raw в историю (в фоне, устойчиво)
    try:
        history = []
        if os.path.exists(HISTORY_PATH):
            history = json.load(open(HISTORY_PATH, "r", encoding="utf-8"))
        history.append(raw)
        # ограничим историю, чтобы файл не бесконечно рос
        history = history[-500:]
        json.dump(history, open(HISTORY_PATH, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    except Exception:
        pass

    return f"{round(score_val * 100, 1)}%"

# ---------------------------
# Взаимодействие с OpenAI (подсказки)
# ---------------------------

def validate_idea(text):
    prompt = f"""
Ты — фильтр качества. Твой ответ должен быть ТОЛЬКО JSON.

Задача: определить, является ли текст полноценным описанием идеи стартапа.

Требования к идее:
- текст связан, не набор случайных слов
- нет бессмыслицы, матов, спама, бессвязного чата

Верни JSON строго такого формата:
{{
  "valid": true/false,
  "reason": "короткое объяснение, почему"
}}

Проверяемый текст:
\"\"\"{text}\"\"\"  
"""
    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300
        )
        raw = res.choices[0].message.content
        try:
            data = safe_json_load(raw)
        except Exception:
            return {"valid": False, "reason": "Ошибка парсинга ответа модели"}
        # валидация полей
        if not isinstance(data, dict) or "valid" not in data:
            return {"valid": False, "reason": "Модель вернула некорректный формат"}
        return {"valid": bool(data.get("valid")), "reason": str(data.get("reason", ""))}
    except Exception as e:
        # если произошла ошибка (rate limit и пр.), вернём невалид и причину
        return {"valid": False, "reason": f"Ошибка модели: {e}"}

def ask_chatgpt(idea_text, retries=2, backoff=5):
    """
    Возвращает dict с полями reach, impact, confidence, effort, competition
    или None при ошибке.
    """
    rules = """ 
(здесь используются те же правила RICE+Competition, что и у тебя — модель должна вернуть JSON 
{ "reach": <int>, "impact": <int>, "confidence": <float>, "effort": <int>, "competition": <int> })
"""
    user_data = f"Идея: {idea_text}\n\nВыведи JSON строго указанного формата."
    for attempt in range(retries + 1):
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.1,
                messages=[
                    {"role": "system", "content": rules},
                    {"role": "user", "content": user_data}
                ],
                max_tokens=300
            )
            raw = response.choices[0].message.content
            try:
                data = safe_json_load(raw)
            except Exception:
                # попробуем найти JSON блок
                m = re.search(r"\{[\s\S]*\}", raw)
                if not m:
                    raise RuntimeError("GPT вернул невалидный JSON:\n" + raw)
                data = json.loads(m.group(0))

            return {
                "reach": clamp_int(data.get("reach", 0), 0, 100000),
                "impact": clamp_int(data.get("impact", 3), 1, 5),
                "confidence": clamp_float(data.get("confidence", 0.5), 0, 1),
                "effort": clamp_int(data.get("effort", 5), 1, 10),
                "competition": clamp_int(data.get("competition", 5), 1, 10),
            }
        except Exception as e:
            # если rate limit, подождём и попробуем снова
            if attempt < retries:
                sleep_time = backoff * (2 ** attempt)
                print(f"Ошибка GPT ({e}), жду {sleep_time}s и повторяю...")
                time.sleep(sleep_time)
                continue
            else:
                print("ask_chatgpt окончательно провалился:", e)
                return None

# ---------------------------
# Сохранение результата (лог)
# ---------------------------
def save_result(user_id, idea, params, score):
    try:
        with open(RESULTS_PATH, "a", encoding="utf-8") as f:
            f.write("\n============================\n")
            f.write(f"Дата: {datetime.now()}\n")
            f.write(f"User ID: {user_id}\n")
            f.write(f"Идея:\n{idea}\n\n")
            f.write("RICE+ параметры:\n")
            f.write(json.dumps(params, ensure_ascii=False, indent=2))
            f.write(f"\nScore: {score}\n")
            f.write("============================\n")
    except Exception as e:
        print("Ошибка сохранения результата:", e)

# ---------------------------
# Генерация совета (на основе GPT)
# ---------------------------
def generate_advice(idea, params, retries=1):
    prompt = f"""
Ты — опытный продуктовый аналитик из Казахстана. 
Пиши кратко, по делу и простым человеческим языком. 
Каждый абзац — 1–2 коротких предложения, не больше. 
Не лей воду, не объясняй очевидное, не используй сложные слова.

Структура текста:
1) Очень коротко перескажи идею (одно предложение).
2) Короткий вывод о потенциале (максимум 2 предложения).
3) Сильные стороны — один мини-абзац.
4) Слабые места — один мини-абзац.
5) Что делать дальше — один мини-абзац с конкретикой.
6) Пиши только на русском.
7) Не упоминай RICE, баллы и оценки.

Используй параметры только для внутренних выводов:
Reach: {params['reach']}
Impact: {params['impact']}
Confidence: {params['confidence']}
Effort: {params['effort']}
Competition: {params['competition']}

Идея:
\"\"\"{idea}\"\"\"

Выведи только чистый текст, максимально ёмко.
"""
    for attempt in range(retries + 1):
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.6,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            if attempt < retries:
                time.sleep(2)
                continue
            else:
                return "Ошибка при генерации совета."

# ---------------------------
# MAIN HANDLER
# ---------------------------
@bot.message_handler(func=lambda m: True)
def handle_idea(msg):
    user = msg.from_user.id
    idea = msg.text.strip()

    # ---------- ШАГ 0: Проверка валидности ----------
    check = validate_idea(idea)
    if not check.get("valid", False):
        bot.send_message(
            msg.chat.id,
            f"❌ <b>Это не похоже на идею стартапа.</b>\n"
            f"Причина: {check.get('reason','Неизвестная')}\n\n"
            "✅ Пожалуйста, опиши идею так, чтобы было понятно:\n"
            "Попробуй ещё раз 😉"
        )
        return

    # ---------- ШАГ 1: RICE+ ----------
    bot.send_message(msg.chat.id, "✅ Анализирую твою идею...")

    params = ask_chatgpt(idea)
    if params is None:
        bot.send_message(msg.chat.id, "⚠️ Не удалось получить оценку от модели. Попробуй позже.")
        return

    score = compute_score(
        R=params["reach"],
        I=params["impact"],
        C=params["confidence"],
        E=params["effort"],
        K=params["competition"]
    )

    # ---------- ШАГ 2: сохраняем ----------
    save_result(user, idea, params, score)

    # ---------- ШАГ 3: персональные советы ----------
    advice = generate_advice(idea, params)

    # ---------- ШАГ 4: отправляем результат ----------
    result_text = f"""
<b>🔍 Анализ твоей идеи</b>

<b>✅ Итоговая оценка: {score}</b>

<b>💡 Экспертный разбор:</b>
{advice}
"""

    bot.send_message(msg.chat.id, result_text)

# ---------------------------
# RUN
# ---------------------------
if __name__ == "__main__":
    print("Bot started.")
    # При первом запуске можно показать текущие веса в консоль
    print("Loaded weights:", load_weights())
    try:
        bot.infinity_polling(timeout=60, long_polling_timeout = 60)
    except KeyboardInterrupt:
        print("Stopped by user.")
    except Exception as e:
        print("Fatal error in polling:", e)