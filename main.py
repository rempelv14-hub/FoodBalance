import asyncio
import logging
import os
import random
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder

# ============================================================
# TELEGRAM NUTRITION BOT 4.0 WITHOUT OPENAI
# ============================================================
# Установка:
#   pip install aiogram
#
# Запуск:
#   1) Вставь реальный токен в BOT_TOKEN ниже
#   2) python main.py
#
# Важно:
# - Напоминания работают, пока бот запущен
# - Если выключить компьютер, бот и напоминания остановятся
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DB_PATH = os.getenv("DB_PATH", "nutrition_bot.db")
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}

if not BOT_TOKEN:
    raise RuntimeError("Set BOT_TOKEN environment variable before running the bot")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

REMINDER_TASKS: dict[int, asyncio.Task] = {}


# =========================
# DATABASE
# =========================
def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column(cur: sqlite3.Cursor, table: str, column: str, column_type: str, existing: set[str]) -> None:
    if column not in existing:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def init_db() -> None:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            name TEXT,
            age INTEGER,
            sex TEXT,
            height_cm INTEGER,
            weight_kg REAL,
            activity_level TEXT,
            goal TEXT,
            allergies TEXT,
            dislikes TEXT,
            meals_per_day INTEGER DEFAULT 3,
            water_goal_ml INTEGER DEFAULT 2000,
            calories_goal INTEGER DEFAULT 2000,
            protein_g INTEGER DEFAULT 100,
            fat_g INTEGER DEFAULT 65,
            carbs_g INTEGER DEFAULT 250,
            reminder_enabled INTEGER DEFAULT 0,
            reminder_text TEXT DEFAULT 'Пора поесть и выпить воды 💧',
            reminder_hour INTEGER DEFAULT 14,
            reminder_minute INTEGER DEFAULT 0
        )
        """
    )

    cur.execute("PRAGMA table_info(users)")
    user_columns = {row[1] for row in cur.fetchall()}
    required_user_columns = {
        "name": "TEXT DEFAULT ''",
        "age": "INTEGER DEFAULT 18",
        "sex": "TEXT DEFAULT ''",
        "height_cm": "INTEGER DEFAULT 170",
        "weight_kg": "REAL DEFAULT 70",
        "activity_level": "TEXT DEFAULT 'Средняя'",
        "goal": "TEXT DEFAULT 'Сбалансированное питание'",
        "allergies": "TEXT DEFAULT 'нет'",
        "dislikes": "TEXT DEFAULT 'нет'",
        "meals_per_day": "INTEGER DEFAULT 3",
        "water_goal_ml": "INTEGER DEFAULT 2000",
        "calories_goal": "INTEGER DEFAULT 2000",
        "protein_g": "INTEGER DEFAULT 100",
        "fat_g": "INTEGER DEFAULT 65",
        "carbs_g": "INTEGER DEFAULT 250",
        "reminder_enabled": "INTEGER DEFAULT 0",
        "reminder_text": "TEXT DEFAULT 'Пора поесть и выпить воды 💧'",
        "reminder_hour": "INTEGER DEFAULT 14",
        "reminder_minute": "INTEGER DEFAULT 0",
    }
    for col, col_type in required_user_columns.items():
        ensure_column(cur, "users", col, col_type, user_columns)

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS weight_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            weight_kg REAL NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS meal_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            meal_text TEXT NOT NULL,
            meal_type TEXT DEFAULT 'обычно',
            estimated_kcal INTEGER DEFAULT 0,
            estimated_protein REAL DEFAULT 0,
            estimated_fat REAL DEFAULT 0,
            estimated_carbs REAL DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute("PRAGMA table_info(meal_logs)")
    meal_columns = {row[1] for row in cur.fetchall()}
    required_meal_columns = {
        "meal_type": "TEXT DEFAULT 'обычно'",
        "estimated_kcal": "INTEGER DEFAULT 0",
        "estimated_protein": "REAL DEFAULT 0",
        "estimated_fat": "REAL DEFAULT 0",
        "estimated_carbs": "REAL DEFAULT 0",
        "created_at": "TEXT DEFAULT ''",
    }
    for col, col_type in required_meal_columns.items():
        ensure_column(cur, "meal_logs", col, col_type, meal_columns)

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS sleep_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            sleep_hours REAL NOT NULL,
            quality INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS water_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            amount_ml INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    conn.commit()
    conn.close()


def save_user_profile(
    telegram_id: int,
    name: str,
    age: int,
    sex: str,
    height_cm: int,
    weight_kg: float,
    activity_level: str,
    goal: str,
    allergies: str,
    dislikes: str,
    meals_per_day: int,
    water_goal_ml: int,
    calories_goal: int,
    protein_g: int,
    fat_g: int,
    carbs_g: int,
) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO users (
            telegram_id, name, age, sex, height_cm, weight_kg,
            activity_level, goal, allergies, dislikes, meals_per_day,
            water_goal_ml, calories_goal, protein_g, fat_g, carbs_g
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET
            name=excluded.name,
            age=excluded.age,
            sex=excluded.sex,
            height_cm=excluded.height_cm,
            weight_kg=excluded.weight_kg,
            activity_level=excluded.activity_level,
            goal=excluded.goal,
            allergies=excluded.allergies,
            dislikes=excluded.dislikes,
            meals_per_day=excluded.meals_per_day,
            water_goal_ml=excluded.water_goal_ml,
            calories_goal=excluded.calories_goal,
            protein_g=excluded.protein_g,
            fat_g=excluded.fat_g,
            carbs_g=excluded.carbs_g
        """,
        (
            telegram_id,
            name,
            age,
            sex,
            height_cm,
            weight_kg,
            activity_level,
            goal,
            allergies,
            dislikes,
            meals_per_day,
            water_goal_ml,
            calories_goal,
            protein_g,
            fat_g,
            carbs_g,
        ),
    )
    conn.commit()
    conn.close()


def get_user_profile(telegram_id: int) -> Optional[dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT name, age, sex, height_cm, weight_kg, activity_level, goal,
               allergies, dislikes, meals_per_day, water_goal_ml,
               calories_goal, protein_g, fat_g, carbs_g,
               reminder_enabled, reminder_text, reminder_hour, reminder_minute
        FROM users WHERE telegram_id = ?
        """,
        (telegram_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return dict(row)


def save_weight_log(telegram_id: int, weight_kg: float) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO weight_logs (telegram_id, weight_kg, created_at) VALUES (?, ?, ?)",
        (telegram_id, weight_kg, now),
    )
    cur.execute("UPDATE users SET weight_kg = ? WHERE telegram_id = ?", (weight_kg, telegram_id))
    conn.commit()
    conn.close()


def get_recent_weight_logs(telegram_id: int, limit: int = 7) -> list[sqlite3.Row]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT weight_kg, created_at FROM weight_logs WHERE telegram_id = ? ORDER BY id DESC LIMIT ?",
        (telegram_id, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def save_meal_log(
    telegram_id: int,
    meal_text: str,
    meal_type: str,
    estimated_kcal: int,
    estimated_protein: float = 0,
    estimated_fat: float = 0,
    estimated_carbs: float = 0,
) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO meal_logs (
            telegram_id, meal_text, meal_type, estimated_kcal,
            estimated_protein, estimated_fat, estimated_carbs, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (telegram_id, meal_text, meal_type, estimated_kcal, estimated_protein, estimated_fat, estimated_carbs, now),
    )
    conn.commit()
    conn.close()


def get_recent_meal_logs(telegram_id: int, limit: int = 10) -> list[sqlite3.Row]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT meal_text, meal_type, estimated_kcal, estimated_protein,
               estimated_fat, estimated_carbs, created_at
        FROM meal_logs WHERE telegram_id = ? ORDER BY id DESC LIMIT ?
        """,
        (telegram_id, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_today_meal_kcal(telegram_id: int) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(SUM(estimated_kcal), 0) FROM meal_logs WHERE telegram_id = ? AND substr(created_at, 1, 10) = ?",
        (telegram_id, today),
    )
    value = cur.fetchone()[0]
    conn.close()
    return int(value or 0)


def get_today_meal_macros(telegram_id: int) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COALESCE(SUM(estimated_protein), 0),
               COALESCE(SUM(estimated_fat), 0),
               COALESCE(SUM(estimated_carbs), 0)
        FROM meal_logs
        WHERE telegram_id = ? AND substr(created_at, 1, 10) = ?
        """,
        (telegram_id, today),
    )
    protein, fat, carbs = cur.fetchone()
    conn.close()
    return {
        "protein": round(float(protein or 0), 1),
        "fat": round(float(fat or 0), 1),
        "carbs": round(float(carbs or 0), 1),
    }


def update_reminder_settings(telegram_id: int, enabled: bool, hour: int, minute: int, text: str) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE users
        SET reminder_enabled = ?, reminder_hour = ?, reminder_minute = ?, reminder_text = ?
        WHERE telegram_id = ?
        """,
        (1 if enabled else 0, hour, minute, text, telegram_id),
    )
    conn.commit()
    conn.close()


def save_sleep_log(telegram_id: int, sleep_hours: float, quality: int) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO sleep_logs (telegram_id, sleep_hours, quality, created_at) VALUES (?, ?, ?, ?)",
        (telegram_id, sleep_hours, quality, now),
    )
    conn.commit()
    conn.close()


def get_recent_sleep_logs(telegram_id: int, limit: int = 7) -> list[sqlite3.Row]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT sleep_hours, quality, created_at FROM sleep_logs WHERE telegram_id = ? ORDER BY id DESC LIMIT ?",
        (telegram_id, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_average_sleep_hours(telegram_id: int, limit: int = 7) -> float:
    rows = get_recent_sleep_logs(telegram_id, limit)
    if not rows:
        return 0.0
    total = sum(float(row["sleep_hours"]) for row in rows)
    return round(total / len(rows), 1)


def save_water_log(telegram_id: int, amount_ml: int) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO water_logs (telegram_id, amount_ml, created_at) VALUES (?, ?, ?)",
        (telegram_id, amount_ml, now),
    )
    conn.commit()
    conn.close()


def get_today_water_ml(telegram_id: int) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(SUM(amount_ml), 0) FROM water_logs WHERE telegram_id = ? AND substr(created_at, 1, 10) = ?",
        (telegram_id, today),
    )
    value = cur.fetchone()[0]
    conn.close()
    return int(value or 0)


def get_recent_water_logs(telegram_id: int, limit: int = 10) -> list[sqlite3.Row]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT amount_ml, created_at FROM water_logs WHERE telegram_id = ? ORDER BY id DESC LIMIT ?",
        (telegram_id, limit),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def delete_last_weight_log(telegram_id: int) -> Optional[float]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM weight_logs WHERE telegram_id = ? ORDER BY id DESC LIMIT 1", (telegram_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    cur.execute("DELETE FROM weight_logs WHERE id = ?", (row[0],))
    cur.execute("SELECT weight_kg FROM weight_logs WHERE telegram_id = ? ORDER BY id DESC LIMIT 1", (telegram_id,))
    latest = cur.fetchone()
    if latest:
        cur.execute("UPDATE users SET weight_kg = ? WHERE telegram_id = ?", (float(latest[0]), telegram_id))
    conn.commit()
    conn.close()
    return float(latest[0]) if latest else None


def delete_last_meal_log(telegram_id: int) -> Optional[sqlite3.Row]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, meal_text, estimated_kcal FROM meal_logs WHERE telegram_id = ? ORDER BY id DESC LIMIT 1",
        (telegram_id,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    cur.execute("DELETE FROM meal_logs WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    return row


def delete_last_sleep_log(telegram_id: int) -> Optional[sqlite3.Row]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, sleep_hours, quality FROM sleep_logs WHERE telegram_id = ? ORDER BY id DESC LIMIT 1",
        (telegram_id,),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    cur.execute("DELETE FROM sleep_logs WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    return row


def build_water_text(profile: Optional[dict], telegram_id: int) -> str:
    goal = int(profile.get("water_goal_ml", estimate_water_goal(profile)) if profile else 2000)
    today = get_today_water_ml(telegram_id)
    remain = max(0, goal - today)
    progress = round((today / goal) * 100) if goal else 0
    return (
        "💧 Баланс воды\n\n"
        f"🎯 Норма: {goal} мл\n"
        f"✅ Сегодня выпито: {today} мл\n"
        f"📉 Осталось: {remain} мл\n"
        f"📊 Прогресс: {progress}%\n\n"
        "Быстрые кнопки: 💧 +250 мл и 💧 +500 мл"
    )


def build_water_history_text(profile: Optional[dict], telegram_id: int) -> str:
    rows = get_recent_water_logs(telegram_id, 10)
    if not rows:
        return "История воды пока пустая. Нажми 💧 +250 мл или 💧 +500 мл."
    goal = int(profile.get("water_goal_ml", estimate_water_goal(profile)) if profile else 2000)
    today = get_today_water_ml(telegram_id)
    lines = ["💧 Последние записи воды", ""]
    for row in rows:
        lines.append(f"— {row['created_at'][5:16]} | {row['amount_ml']} мл")
    lines.extend(["", f"Сегодня: {today} / {goal} мл"])
    return "\n".join(lines)


def is_admin(user_id: int) -> bool:
    return bool(ADMIN_IDS) and user_id in ADMIN_IDS


def build_admin_stats_text() -> str:
    conn = get_connection()
    cur = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")
    cur.execute("SELECT COUNT(*) FROM users")
    total_users = int(cur.fetchone()[0] or 0)
    cur.execute("SELECT COUNT(*) FROM users WHERE name IS NOT NULL AND TRIM(name) != ''")
    filled_profiles = int(cur.fetchone()[0] or 0)
    cur.execute("SELECT COUNT(DISTINCT telegram_id) FROM meal_logs WHERE substr(created_at, 1, 10) = ?", (today,))
    active_meals = int(cur.fetchone()[0] or 0)
    cur.execute("SELECT COUNT(DISTINCT telegram_id) FROM weight_logs WHERE substr(created_at, 1, 10) = ?", (today,))
    active_weight = int(cur.fetchone()[0] or 0)
    cur.execute("SELECT COUNT(DISTINCT telegram_id) FROM sleep_logs WHERE substr(created_at, 1, 10) = ?", (today,))
    active_sleep = int(cur.fetchone()[0] or 0)
    cur.execute("SELECT COUNT(DISTINCT telegram_id) FROM water_logs WHERE substr(created_at, 1, 10) = ?", (today,))
    active_water = int(cur.fetchone()[0] or 0)
    conn.close()
    return (
        "📊 Админ-панель\n\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"📝 Заполнено анкет: {filled_profiles}\n"
        f"🍴 Активных по еде сегодня: {active_meals}\n"
        f"⚖️ Активных по весу сегодня: {active_weight}\n"
        f"😴 Активных по сну сегодня: {active_sleep}\n"
        f"💧 Активных по воде сегодня: {active_water}"
    )


def get_users_with_enabled_reminders() -> list[int]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT telegram_id FROM users WHERE reminder_enabled = 1")
    rows = [int(row[0]) for row in cur.fetchall()]
    conn.close()
    return rows


# =========================
# FOOD DATA


# =========================
# FOOD DATA
# =========================
BREAKFASTS = [
    {"name": "Овсянка с бананом и орехами", "kcal": 420, "p": 12, "f": 14, "c": 60},
    {"name": "Омлет с овощами и хлебом", "kcal": 380, "p": 22, "f": 20, "c": 25},
    {"name": "Творог с ягодами", "kcal": 300, "p": 28, "f": 8, "c": 20},
    {"name": "Йогурт с овсянкой и яблоком", "kcal": 340, "p": 16, "f": 8, "c": 48},
    {"name": "Яйца, сыр и овощи", "kcal": 360, "p": 24, "f": 24, "c": 8},
]

LUNCHES = [
    {"name": "Курица с рисом и овощами", "kcal": 620, "p": 42, "f": 16, "c": 72},
    {"name": "Рыба с картофелем и салатом", "kcal": 560, "p": 36, "f": 18, "c": 54},
    {"name": "Гречка с индейкой и овощами", "kcal": 590, "p": 40, "f": 15, "c": 66},
    {"name": "Тушёные бобовые с овощами", "kcal": 510, "p": 24, "f": 12, "c": 74},
    {"name": "Паста с курицей и овощами", "kcal": 640, "p": 38, "f": 17, "c": 78},
]

DINNERS = [
    {"name": "Омлет с салатом", "kcal": 340, "p": 22, "f": 22, "c": 10},
    {"name": "Рыба с овощами", "kcal": 390, "p": 34, "f": 20, "c": 12},
    {"name": "Курица с тушёными овощами", "kcal": 430, "p": 38, "f": 18, "c": 18},
    {"name": "Творог, овощи и тост", "kcal": 350, "p": 28, "f": 10, "c": 30},
    {"name": "Индейка и салат", "kcal": 410, "p": 35, "f": 20, "c": 14},
]

SNACKS = [
    {"name": "Яблоко и немного орехов", "kcal": 180, "p": 4, "f": 10, "c": 18},
    {"name": "Йогурт без лишнего сахара", "kcal": 120, "p": 8, "f": 4, "c": 12},
    {"name": "Банан", "kcal": 110, "p": 1, "f": 0, "c": 25},
    {"name": "Сыр и хлебец", "kcal": 170, "p": 9, "f": 10, "c": 10},
    {"name": "Творог", "kcal": 150, "p": 20, "f": 5, "c": 6},
]

FOOD_DATABASE = [
    {"name": "курица", "aliases": ["курица", "курин", "грудка"], "kcal": 165, "p": 31, "f": 3.6, "c": 0, "default_g": 150},
    {"name": "индейка", "aliases": ["индейка", "индейк"], "kcal": 135, "p": 29, "f": 1.0, "c": 0, "default_g": 150},
    {"name": "рыба", "aliases": ["рыба", "лосось", "тунец", "треска"], "kcal": 140, "p": 22, "f": 6, "c": 0, "default_g": 150},
    {"name": "говядина", "aliases": ["говядина", "говядин", "мясо"], "kcal": 187, "p": 18, "f": 12, "c": 0, "default_g": 150},
    {"name": "яйцо", "aliases": ["яйцо", "яйца", "омлет"], "kcal": 143, "p": 12.6, "f": 9.5, "c": 1.1, "default_g": 100, "piece_g": 55},
    {"name": "рис", "aliases": ["рис"], "kcal": 130, "p": 2.7, "f": 0.3, "c": 28, "default_g": 150},
    {"name": "гречка", "aliases": ["гречка", "гречки"], "kcal": 110, "p": 4.2, "f": 1.1, "c": 21.3, "default_g": 150},
    {"name": "овсянка", "aliases": ["овсянка", "овсяную", "овсяной"], "kcal": 88, "p": 3, "f": 1.7, "c": 15, "default_g": 180},
    {"name": "макароны", "aliases": ["макароны", "паста"], "kcal": 158, "p": 5.8, "f": 0.9, "c": 30.9, "default_g": 180},
    {"name": "картофель", "aliases": ["картофель", "картошка", "пюре"], "kcal": 77, "p": 2, "f": 0.4, "c": 17, "default_g": 180},
    {"name": "творог", "aliases": ["творог"], "kcal": 121, "p": 17, "f": 5, "c": 3, "default_g": 180},
    {"name": "йогурт", "aliases": ["йогурт"], "kcal": 75, "p": 5, "f": 3, "c": 7, "default_g": 180},
    {"name": "сыр", "aliases": ["сыр"], "kcal": 350, "p": 24, "f": 27, "c": 0, "default_g": 30},
    {"name": "хлеб", "aliases": ["хлеб", "тост", "хлебец"], "kcal": 250, "p": 8, "f": 2, "c": 49, "default_g": 40},
    {"name": "банан", "aliases": ["банан", "бананы"], "kcal": 89, "p": 1.1, "f": 0.3, "c": 23, "default_g": 120, "piece_g": 120},
    {"name": "яблоко", "aliases": ["яблоко", "яблоки"], "kcal": 52, "p": 0.3, "f": 0.2, "c": 14, "default_g": 150, "piece_g": 150},
    {"name": "орехи", "aliases": ["орехи", "орех"], "kcal": 610, "p": 20, "f": 54, "c": 16, "default_g": 25},
    {"name": "овощи", "aliases": ["овощи", "салат", "огурец", "огурцы", "помидор", "помидоры", "брокколи"], "kcal": 30, "p": 1.5, "f": 0.2, "c": 5, "default_g": 200},
    {"name": "молоко", "aliases": ["молоко", "молочный", "молочная", "молочные"], "kcal": 52, "p": 2.8, "f": 2.5, "c": 4.7, "default_g": 200},
    {"name": "кефир", "aliases": ["кефир"], "kcal": 50, "p": 3, "f": 2.5, "c": 4, "default_g": 200},
    {"name": "сметана", "aliases": ["сметана", "сметанный"], "kcal": 206, "p": 2.8, "f": 20, "c": 3.2, "default_g": 30},
    {"name": "сливки", "aliases": ["сливки", "сливочный", "сливочная"], "kcal": 200, "p": 2.5, "f": 20, "c": 3.7, "default_g": 50},
    {"name": "ряженка", "aliases": ["ряженка"], "kcal": 67, "p": 2.8, "f": 4, "c": 4.2, "default_g": 200},
    {"name": "масло", "aliases": ["масло", "оливковое масло"], "kcal": 899, "p": 0, "f": 99.9, "c": 0, "default_g": 10},
    {"name": "пицца", "aliases": ["пицца"], "kcal": 266, "p": 11, "f": 10, "c": 33, "default_g": 250},
    {"name": "бургер", "aliases": ["бургер"], "kcal": 295, "p": 17, "f": 14, "c": 24, "default_g": 220},
    {"name": "шаурма", "aliases": ["шаурма"], "kcal": 220, "p": 10, "f": 9, "c": 24, "default_g": 300},
    {"name": "фарш", "aliases": ["фарш"], "kcal": 240, "p": 16, "f": 18, "c": 0, "default_g": 150},
    {"name": "котлета", "aliases": ["котлета", "котлеты"], "kcal": 250, "p": 14, "f": 18, "c": 8, "default_g": 100, "piece_g": 90},
    {"name": "суп", "aliases": ["суп", "борщ"], "kcal": 55, "p": 2.5, "f": 2.5, "c": 6, "default_g": 300},
    {"name": "плов", "aliases": ["плов"], "kcal": 180, "p": 5, "f": 7, "c": 24, "default_g": 250},
    {"name": "пельмени", "aliases": ["пельмени"], "kcal": 275, "p": 11, "f": 12, "c": 29, "default_g": 200},
    {"name": "вареники", "aliases": ["вареники"], "kcal": 210, "p": 6, "f": 5, "c": 36, "default_g": 200},
    {"name": "колбаса", "aliases": ["колбаса"], "kcal": 300, "p": 13, "f": 27, "c": 2, "default_g": 50},
    {"name": "сосиски", "aliases": ["сосиски", "сосиска"], "kcal": 270, "p": 11, "f": 24, "c": 2, "default_g": 100, "piece_g": 50},
    {"name": "лаваш", "aliases": ["лаваш"], "kcal": 260, "p": 8, "f": 1.5, "c": 55, "default_g": 70},
    {"name": "майонез", "aliases": ["майонез"], "kcal": 620, "p": 1, "f": 67, "c": 3, "default_g": 15},
    {"name": "кетчуп", "aliases": ["кетчуп"], "kcal": 110, "p": 1.3, "f": 0.2, "c": 25, "default_g": 15},
    {"name": "сахар", "aliases": ["сахар"], "kcal": 398, "p": 0, "f": 0, "c": 99.8, "default_g": 5},
    {"name": "чай", "aliases": ["чай"], "kcal": 1, "p": 0, "f": 0, "c": 0, "default_g": 250},
    {"name": "кофе", "aliases": ["кофе"], "kcal": 2, "p": 0.2, "f": 0, "c": 0.3, "default_g": 200},
]

RECIPE_LIBRARY = [
    {"title": "Омлет с овощами", "tags": ["завтрак", "яйца", "быстро"], "cook_time": "10–12 минут", "kcal": 360, "ingredients": ["2 яйца", "120 г помидоров", "80 г перца", "20 г сыра", "1 ч. л. масла", "соль и специи"], "steps": ["Нарежь овощи.", "Слегка обжарь их 2–3 минуты.", "Взбей яйца и залей овощи.", "Добавь сыр и готовь под крышкой 4–5 минут."]},
    {"title": "Курица с рисом и овощами", "tags": ["обед", "курица", "рис"], "cook_time": "25–30 минут", "kcal": 620, "ingredients": ["150 г курицы", "150 г готового риса", "200 г овощей", "1 ч. л. масла", "соль, паприка, чеснок"], "steps": ["Нарежь курицу и приправь.", "Обжарь или запеки до готовности.", "Отвари рис.", "Потуши овощи 5–7 минут.", "Собери блюдо: рис + курица + овощи."]},
    {"title": "Рыба с картофелем", "tags": ["ужин", "рыба", "картофель"], "cook_time": "30 минут", "kcal": 540, "ingredients": ["160 г рыбы", "200 г картофеля", "150 г салата", "1 ч. л. масла", "лимон, соль, специи"], "steps": ["Запеки картофель до мягкости.", "Рыбу посоли и добавь специи.", "Запеки рыбу 15–18 минут.", "Подавай с салатом и картофелем."]},
    {"title": "Творог с ягодами и бананом", "tags": ["перекус", "творог", "быстро"], "cook_time": "3 минуты", "kcal": 320, "ingredients": ["180 г творога", "80 г ягод", "1 маленький банан"], "steps": ["Выложи творог в миску.", "Добавь ягоды и нарезанный банан.", "Перемешай или ешь слоями."]},
    {"title": "Индейка с гречкой", "tags": ["обед", "индейка", "гречка"], "cook_time": "25 минут", "kcal": 590, "ingredients": ["150 г индейки", "150 г готовой гречки", "180 г овощей", "1 ч. л. масла"], "steps": ["Отвари гречку.", "Обжарь или потуши индейку 8–10 минут.", "Добавь овощи и готовь ещё 5 минут.", "Подавай вместе с гречкой."]},
]

SHOPPING_BASE = [
    "яйца", "курица", "рыба", "индейка", "творог", "йогурт без лишнего сахара",
    "овсянка", "рис", "гречка", "цельнозерновой хлеб", "макароны из твёрдых сортов",
    "овощи", "фрукты", "орехи", "сыр", "вода",
]

DAYS_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]


# =========================
# HELPERS
# =========================
def _contains_any(text: str, words: list[str]) -> bool:
    return any(word in text for word in words)


def _blocked_text(profile: Optional[dict]) -> str:
    if not profile:
        return ""
    allergies = str(profile.get("allergies", "")).lower().strip()
    dislikes = str(profile.get("dislikes", "")).lower().strip()
    combined = f"{allergies}, {dislikes}".replace(";", ",")
    return combined.strip(" ,")


def parse_blocked_products(profile: Optional[dict]) -> list[str]:
    """Расширяет запреты: один продукт или категория блокируют все похожие продукты."""
    blocked_text = _blocked_text(profile)
    if not blocked_text or blocked_text in {"нет", "нет, нет", "не", "none", "no", "ничего"}:
        return []

    normalized_blocked_text = normalize_food_text(blocked_text)
    raw_parts = [part.strip() for part in re.split(r",|;|\n|/", normalized_blocked_text) if part.strip()]

    synonym_map = {
        "яйца": ["яйца", "яйцо", "яичница", "омлет", "яичный", "яичная", "яичные", "желток", "белок яйца"],
        "курица": ["курица", "курицу", "курицей", "куриное", "куриная", "куриный", "курин", "грудка", "филе курицы"],
        "индейка": ["индейка", "индейку", "индейкой", "индейки", "индейк", "филе индейки"],
        "говядина": ["говядина", "говядин", "телятина", "стейк", "фарш говяжий"],
        "свинина": ["свинина", "свинин", "бекон", "ветчина", "карбонад"],
        "баранина": ["баранина", "баранин"],
        "мясо": ["мясо", "мясной", "мясная", "мясные", "фарш", "котлета", "котлеты", "колбаса", "сосиски", "сардельки", "ветчина", "бекон"],
        "птица": ["птица", "курица", "курин", "индейка", "индейк", "утка", "гусь"],
        "рыба": ["рыба", "рыбу", "рыбой", "рыбный", "рыбная", "рыбные", "лосось", "семга", "форель", "тунец", "треска", "хек", "минтай", "скумбрия", "сельдь", "сардина", "карп", "судак"],
        "морепродукты": ["морепродукты", "креветки", "креветка", "кальмар", "мидии", "мидия", "краб", "устрицы", "осьминог"],
        "молоко": ["молоко", "молочный", "молочная", "молочные", "молочка", "лактоза"],
        "кефир": ["кефир"],
        "йогурт": ["йогурт", "йогуртовый"],
        "творог": ["творог", "творож", "сырники", "творожная запеканка"],
        "сыр": ["сыр", "сыром", "сырный", "моцарелла", "брынза", "фета", "пармезан", "сулугуни"],
        "сметана": ["сметана", "сметанный"],
        "сливки": ["сливки", "сливочный", "сливочная"],
        "ряженка": ["ряженка"],
        "айран": ["айран", "тан"],
        "сливочное масло": ["сливочное масло", "масло сливочное"],
        "рис": ["рис", "рисом", "рисовый"],
        "гречка": ["гречка", "гречкой", "гречки", "гречнев"],
        "овсянка": ["овсянка", "овсянкой", "овсяную", "овсяной", "овес", "овсяные хлопья", "геркулес"],
        "пшено": ["пшено", "пшенная"],
        "перловка": ["перловка", "перловая"],
        "булгур": ["булгур"],
        "кускус": ["кус кус", "кускус"],
        "манка": ["манка", "манная"],
        "киноа": ["киноа"],
        "хлеб": ["хлеб", "тост", "тосты", "хлебец", "хлебцы", "булка", "батон", "лаваш", "лепешка"],
        "макароны": ["макароны", "макарон", "паста", "спагетти", "лапша", "вермишель"],
        "мука": ["мука", "мучное", "мучной", "выпечка", "булочка", "пирожок", "блин", "блины", "оладьи"],
        "глютен": ["глютен", "пшеница", "пшеничный", "рожь", "ячмень", "мука", "макароны", "паста", "хлеб", "лаваш", "булка", "выпечка"],
        "картофель": ["картофель", "картошка", "картофелем", "пюре", "картошка фри"],
        "бобовые": ["бобовые", "фасоль", "чечевица", "нут", "горох", "маш", "соевые бобы"],
        "соя": ["соя", "соевый", "соевое", "тофу", "соевый соус"],
        "овощи": ["овощи", "овощной", "салат", "огурец", "огурцы", "помидор", "помидоры", "томат", "томаты", "брокколи", "морковь", "капуста", "перец", "лук", "чеснок", "кабачок", "баклажан", "свекла", "зелень", "шпинат"],
        "грибы": ["грибы", "гриб", "шампиньоны", "вешенки"],
        "фрукты": ["фрукты", "фрукт", "яблоко", "яблоки", "банан", "бананы", "груша", "груши", "апельсин", "мандарин", "лимон", "виноград", "персик", "абрикос"],
        "ягоды": ["ягоды", "ягода", "клубника", "малина", "черника", "смородина", "вишня"],
        "орехи": ["орехи", "орех", "орехов", "миндаль", "арахис", "фундук", "кешью", "грецкий орех", "фисташки"],
        "семечки": ["семечки", "семена", "кунжут", "чиа", "лен", "тыквенные семечки", "подсолнечные семечки"],
        "сладкое": ["сладкое", "сладости", "сахар", "сироп", "мед", "варенье", "джем", "конфеты", "шоколад", "торт", "печенье", "пирожное", "кекс", "мороженое"],
        "фастфуд": ["фастфуд", "бургер", "пицца", "шаурма", "картошка фри", "наггетсы", "хот дог", "хот-дог"],
        "соусы": ["соус", "соусы", "майонез", "кетчуп", "горчица", "соевый соус"],
        "масло": ["масло", "оливковое масло", "растительное масло", "подсолнечное масло", "сливочное масло"],
        "жирное": ["жирное", "жирный", "жареное", "фритюр", "майонез", "бекон"],
        "кофеин": ["кофе", "кофеин", "капучино", "латте", "эспрессо", "американо", "энергетик"],
        "газировка": ["газировка", "газированные напитки", "кола", "лимонад", "энергетик"],
        "соки": ["сок", "соки", "нектар"],
        "напитки": ["напитки", "кофе", "чай", "газировка", "соки", "лимонад", "энергетик"],
    }

    category_map = {
        "молочные продукты": ["молоко", "творог", "йогурт", "кефир", "сыр", "сметана", "сливки", "ряженка", "айран", "сливочное масло"],
        "молочка": ["молоко", "творог", "йогурт", "кефир", "сыр", "сметана", "сливки", "ряженка", "айран", "сливочное масло"],
        "кисломолочные продукты": ["кефир", "йогурт", "творог", "сметана", "ряженка", "айран"],
        "мясные продукты": ["мясо", "говядина", "свинина", "баранина", "курица", "индейка", "птица"],
        "мясо": ["мясо", "говядина", "свинина", "баранина", "фарш", "колбаса", "сосиски", "бекон", "ветчина"],
        "птица": ["птица", "курица", "индейка", "утка", "гусь"],
        "рыбные продукты": ["рыба", "лосось", "семга", "форель", "тунец", "треска", "хек", "минтай", "скумбрия", "сельдь"],
        "рыба": ["рыба", "лосось", "семга", "форель", "тунец", "треска", "хек", "минтай", "скумбрия", "сельдь"],
        "морепродукты": ["морепродукты", "креветки", "кальмар", "мидии", "краб", "устрицы"],
        "яйца": ["яйца", "яйцо", "яичница", "омлет", "желток", "белок яйца"],
        "крупы": ["рис", "гречка", "овсянка", "пшено", "перловка", "булгур", "кускус", "манка", "киноа"],
        "злаки": ["рис", "гречка", "овсянка", "пшеница", "рожь", "ячмень", "булгур", "кускус", "хлеб", "макароны"],
        "мучное": ["мука", "хлеб", "тост", "хлебец", "лаваш", "булка", "выпечка", "макароны", "паста", "спагетти", "лапша", "блины", "оладьи"],
        "глютен": ["глютен", "пшеница", "рожь", "ячмень", "мука", "хлеб", "макароны", "паста", "лаваш", "выпечка"],
        "бобовые": ["бобовые", "фасоль", "чечевица", "нут", "горох", "маш", "соя", "тофу"],
        "овощи": ["овощи", "огурец", "огурцы", "помидор", "помидоры", "томат", "брокколи", "морковь", "капуста", "перец", "лук", "чеснок", "кабачок", "баклажан", "свекла", "зелень", "грибы"],
        "фрукты": ["фрукты", "яблоко", "банан", "груша", "цитрусовые", "ягоды", "виноград", "персик", "абрикос"],
        "ягоды": ["ягоды", "клубника", "малина", "черника", "смородина", "вишня"],
        "орехи": ["орехи", "миндаль", "арахис", "фундук", "кешью", "грецкий орех", "фисташки"],
        "семечки": ["семечки", "семена", "кунжут", "чиа", "лен"],
        "сладости": ["сладкое", "сахар", "конфеты", "шоколад", "торт", "печенье", "пирожное", "мед", "варенье", "мороженое"],
        "сладкое": ["сладкое", "сахар", "конфеты", "шоколад", "торт", "печенье", "пирожное", "мед", "варенье", "мороженое"],
        "фастфуд": ["бургер", "пицца", "шаурма", "картошка фри", "наггетсы", "хот дог", "хот-дог"],
        "соусы": ["соусы", "майонез", "кетчуп", "горчица", "соевый соус"],
        "масла": ["масло", "оливковое масло", "растительное масло", "подсолнечное масло", "сливочное масло"],
        "жирное": ["жирное", "жареное", "фритюр", "майонез", "бекон"],
        "напитки": ["кофеин", "газировка", "соки"],
        "кофеин": ["кофеин", "кофе", "энергетик"],
        "газированные напитки": ["газировка", "кола", "лимонад", "энергетик"],
    }

    category_stems = {
        "молоч": "молочные продукты", "лактоз": "молочные продукты", "кисломолоч": "кисломолочные продукты",
        "мяс": "мясные продукты", "колбас": "мясные продукты", "сосиск": "мясные продукты",
        "кур": "птица", "индей": "птица", "птиц": "птица",
        "рыб": "рыба", "морепродукт": "морепродукты",
        "яиц": "яйца", "яйц": "яйца", "круп": "крупы", "злак": "злаки",
        "мучн": "мучное", "глютен": "глютен", "бобов": "бобовые",
        "овощ": "овощи", "фрукт": "фрукты", "ягод": "ягоды", "орех": "орехи", "семеч": "семечки",
        "слад": "сладости", "сахар": "сладости", "фастфуд": "фастфуд", "соус": "соусы",
        "жирн": "жирное", "жарен": "жирное", "кофеин": "кофеин", "кофе": "кофеин",
        "газиров": "газированные напитки", "напит": "напитки",
    }

    final_tokens: set[str] = set()

    def add_token_with_synonyms(token: str) -> None:
        token = normalize_food_text(token)
        if not token or token in {"нет", "ничего"}:
            return
        final_tokens.add(token)
        for key, values in synonym_map.items():
            all_values = [key] + values
            if token == key or token in values or key in token or any(value in token for value in all_values if len(value) >= 4):
                final_tokens.add(key)
                final_tokens.update(values)

    def add_category(category: str) -> None:
        category = normalize_food_text(category)
        final_tokens.add(category)
        for product in category_map.get(category, []):
            add_token_with_synonyms(product)

    for item in raw_parts:
        add_token_with_synonyms(item)
        for category in category_map:
            category_words = category.split()
            if category in item or item in category or all(word in item for word in category_words):
                add_category(category)
        for stem, category in category_stems.items():
            if stem in item:
                add_category(category)

    return sorted(token for token in final_tokens if token and token not in {"нет", "ничего"})

def forbidden_hits_in_text(text: str, profile: Optional[dict]) -> list[str]:
    """Находит запретные продукты в любом тексте."""
    blocked_tokens = parse_blocked_products(profile)
    if not blocked_tokens:
        return []
    normalized_text = normalize_food_text(text)
    hits = []
    for token in blocked_tokens:
        normalized_token = normalize_food_text(token)
        if normalized_token and normalized_token in normalized_text:
            hits.append(token)
    return sorted(set(hits))


def _safe_item_name(item_name: str, profile: Optional[dict]) -> bool:
    return not forbidden_hits_in_text(item_name, profile)


def _filter_safe_items(options: list[dict], profile: Optional[dict]) -> list[dict]:
    return [item for item in options if _safe_item_name(str(item.get("name", "")), profile)]


def _pick(options: list[dict], profile: Optional[dict]) -> Optional[dict]:
    allowed = _filter_safe_items(options, profile)
    return random.choice(allowed) if allowed else None


def _blocked_notice(profile: Optional[dict], limit: int = 12) -> str:
    blocked = parse_blocked_products(profile)
    return ", ".join(blocked[:limit]) if blocked else ""


def _no_safe_food_text(place: str, profile: Optional[dict]) -> str:
    return (
        f"Не смог подобрать {place}: все подходящие варианты пересекаются с твоими запретами.\n\n"
        "Измени запреты или добавь больше разрешённых продуктов."
    )


def estimate_water_goal(profile: Optional[dict]) -> int:
    if not profile:
        return 2000
    weight = float(profile.get("weight_kg") or 0)
    activity = str(profile.get("activity_level") or "").lower()
    base = int(weight * 30) if weight > 0 else 2000
    if "сред" in activity:
        base += 300
    elif "выс" in activity:
        base += 600
    return max(1500, min(base, 3500))


def activity_multiplier(activity_level: str) -> float:
    value = activity_level.lower()
    if "низ" in value:
        return 1.35
    if "сред" in value:
        return 1.55
    if "выс" in value:
        return 1.75
    return 1.4


def calculate_bmr(age: int, sex: str, weight_kg: float, height_cm: int) -> int:
    if sex == "Женский":
        return int(10 * weight_kg + 6.25 * height_cm - 5 * age - 161)
    return int(10 * weight_kg + 6.25 * height_cm - 5 * age + 5)


def calculate_targets(profile: dict) -> dict:
    age = int(profile["age"])
    sex = str(profile["sex"])
    weight = float(profile["weight_kg"])
    height = int(profile["height_cm"])
    goal = str(profile["goal"])
    activity = str(profile["activity_level"])

    bmr = calculate_bmr(age, sex, weight, height)
    maintain = int(bmr * activity_multiplier(activity))

    is_female = "жен" in sex.lower()
    min_cut_calories = 1500 if is_female else 1700
    min_fat = 50 if is_female else 55

    if goal == "Безопасное снижение веса":
        activity_value = activity.lower()
        if "низ" in activity_value:
            deficit_percent = 0.15
        elif "выс" in activity_value:
            deficit_percent = 0.18
        else:
            deficit_percent = 0.17

        deficit = int(maintain * deficit_percent)
        deficit = max(300, min(deficit, 700))
        calories = maintain - deficit
        calories = max(min_cut_calories, calories)

        protein = round(weight * 1.8)
        fat = max(min_fat, round(weight * 0.9))
    elif goal == "Набор массы":
        calories = maintain + 250
        protein = round(weight * 1.8)
        fat = max(min_fat, round(weight * 1.0))
    else:
        calories = maintain
        protein = round(weight * 1.6)
        fat = max(min_fat, round(weight * 0.9))

    carbs = round((calories - protein * 4 - fat * 9) / 4)
    carbs = max(100, carbs)

    if protein * 4 + fat * 9 + carbs * 4 > calories + 80:
        carbs = max(100, round((calories - protein * 4 - fat * 9) / 4))
        if carbs < 100:
            carbs = 100
            fat = max(min_fat, round((calories - protein * 4 - carbs * 4) / 9))

    water = estimate_water_goal(profile)
    return {
        "calories": int(calories),
        "protein": int(protein),
        "fat": int(fat),
        "carbs": int(carbs),
        "water": int(water),
        "bmr": int(bmr),
        "maintain": int(maintain),
    }


def bmi_value(profile: dict) -> Optional[float]:
    try:
        h = float(profile["height_cm"]) / 100
        w = float(profile["weight_kg"])
        return w / (h * h)
    except Exception:
        return None


def item_line(item: dict) -> str:
    return f"{item['name']} — {item['kcal']} ккал, Б {item['p']} / Ж {item['f']} / У {item['c']}"


def normalize_food_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("ё", "е")).strip()


def _normalize_unit(unit: str) -> str:
    unit = (unit or "").lower().strip().replace(".", "")
    if unit == "гр":
        return "г"
    return unit


def _extract_amount_from_segment(segment: str, item: dict) -> tuple[float, str]:
    """Возвращает (граммы, source), где source = exact / inferred / default."""
    s = normalize_food_text(segment)

    match = re.search(r"(\d+(?:[\.,]\d+)?)\s*(кг|г|гр|грамм|грамма|граммов|мл|л|шт|штука|штуки|штук)\b", s)
    if match:
        value = float(match.group(1).replace(",", "."))
        unit = _normalize_unit(match.group(2))

        if unit == "кг":
            return value * 1000, "exact"
        if unit == "л":
            return value * 1000, "exact"
        if unit in {"г", "грамм", "грамма", "граммов", "мл"}:
            return value, "exact"
        if unit in {"шт", "штука", "штуки", "штук"}:
            piece_g = float(item.get("piece_g", item.get("default_g", 100)))
            return value * piece_g, "exact"

    piece_match = re.search(r"(\d+(?:[\.,]\d+)?)\s+([а-яa-z]+)", s)
    if piece_match and item.get("piece_g"):
        value = float(piece_match.group(1).replace(",", "."))
        word = piece_match.group(2)
        if any(alias in word or word in alias for alias in item.get("aliases", [])):
            return value * float(item["piece_g"]), "inferred"

    if any(x in s for x in ["полпачки", "половина пачки"]):
        return float(item.get("default_g", 100)) / 2, "inferred"
    if "пачка" in s:
        return float(item.get("default_g", 100)), "inferred"

    if "масло" in item["name"]:
        spoon_match = re.search(r"(\d+(?:[\.,]\d+)?)\s*(ст\.?\s*л|столовая ложка|столовые ложки)", s)
        if spoon_match:
            spoons = float(spoon_match.group(1).replace(",", "."))
            return spoons * 14, "inferred"
        tea_match = re.search(r"(\d+(?:[\.,]\d+)?)\s*(ч\.?\s*л|чайная ложка|чайные ложки)", s)
        if tea_match:
            spoons = float(tea_match.group(1).replace(",", "."))
            return spoons * 5, "inferred"
        if "ложка" in s:
            return 14, "inferred"

    return float(item.get("default_g", 100)), "default"


def _find_food_item_for_segment(segment: str) -> Optional[dict]:
    s = normalize_food_text(segment)
    best_item = None
    best_score = 0

    for item in FOOD_DATABASE:
        score = 0
        for alias in item["aliases"]:
            alias_n = normalize_food_text(alias)
            if re.search(rf"\b{re.escape(alias_n)}", s):
                score = max(score, len(alias_n) + 10)
            elif alias_n in s:
                score = max(score, len(alias_n))
        if score > best_score:
            best_score = score
            best_item = item

    return best_item if best_score > 0 else None


def estimate_meal_details(text: str) -> dict:
    normalized = normalize_food_text(text)
    raw_segments = re.split(r",|;|\+|\n| и | с ", normalized)
    segments = [segment.strip() for segment in raw_segments if segment.strip()]

    found_items = []
    seen_pairs = set()

    for segment in segments:
        item = _find_food_item_for_segment(segment)
        if not item:
            continue

        key = (segment, item["name"])
        if key in seen_pairs:
            continue
        seen_pairs.add(key)

        grams, source = _extract_amount_from_segment(segment, item)
        ratio = grams / 100
        found_items.append({
            "name": item["name"],
            "grams": round(grams),
            "kcal": round(item["kcal"] * ratio),
            "protein": round(item["p"] * ratio, 1),
            "fat": round(item["f"] * ratio, 1),
            "carbs": round(item["c"] * ratio, 1),
            "source": source,
        })

    if not found_items:
        return {
            "kcal": 0,
            "protein": 0.0,
            "fat": 0.0,
            "carbs": 0.0,
            "items": [],
            "precision": "none",
            "comment": "Не смог точно распознать продукты. Напиши так: 150 г курицы, 120 г риса, 100 г салата.",
        }

    exact_count = sum(1 for item in found_items if item["source"] == "exact")
    inferred_count = sum(1 for item in found_items if item["source"] == "inferred")

    if exact_count == len(found_items):
        precision = "high"
    elif exact_count > 0 or inferred_count > 0:
        precision = "medium"
    else:
        precision = "low"

    return {
        "kcal": sum(item["kcal"] for item in found_items),
        "protein": round(sum(item["protein"] for item in found_items), 1),
        "fat": round(sum(item["fat"] for item in found_items), 1),
        "carbs": round(sum(item["carbs"] for item in found_items), 1),
        "items": found_items,
        "precision": precision,
        "comment": "",
    }


def estimate_meal_kcal(text: str) -> int:
    return int(estimate_meal_details(text)["kcal"])


# =========================
# TEXT BUILDERS


# =========================
# TEXT BUILDERS
# =========================
def profile_summary(profile: dict) -> str:
    bmi = bmi_value(profile)
    bmi_text = f"{bmi:.1f}" if bmi is not None else "—"
    reminder_status = "включены" if int(profile.get("reminder_enabled", 0)) else "выключены"
    reminder_time = f"{int(profile.get('reminder_hour', 14)):02d}:{int(profile.get('reminder_minute', 0)):02d}"
    return (
        "👤 Твой профиль\n\n"
        f"Имя: {profile['name']}\n"
        f"Возраст: {profile['age']}\n"
        f"Пол: {profile['sex']}\n"
        f"Рост: {profile['height_cm']} см\n"
        f"Вес: {profile['weight_kg']} кг\n\n"
        f"🎯 Цель: {profile['goal']}\n"
        f"⚡ Активность: {profile['activity_level']}\n"
        f"🔥 Калории: {profile['calories_goal']} ккал\n"
        f"🥩 БЖУ: {profile['protein_g']} / {profile['fat_g']} / {profile['carbs_g']}\n"
        f"💧 Вода: {profile['water_goal_ml']} мл\n"
        f"🍽 Приёмов пищи: {profile['meals_per_day']}\n"
        f"⏰ Напоминания: {reminder_status} ({reminder_time})\n"
        f"📏 ИМТ: {bmi_text}\n"
        f"😴 Сон: смотри в разделе истории сна\n\n"
        f"🚫 Аллергии: {profile['allergies']}\n"
        f"🚫 Не ешь / не любишь: {profile['dislikes']}"
    )

def build_targets_text(profile: Optional[dict], telegram_id: int) -> str:
    if not profile:
        return "Сначала заполни анкету, чтобы я мог рассчитать калории и БЖУ."
    today_kcal = get_today_meal_kcal(telegram_id)
    today_macros = get_today_meal_macros(telegram_id)
    remain_kcal = max(0, int(profile['calories_goal']) - today_kcal)
    return (
        "🔥 Твой баланс на сегодня\n\n"
        f"🎯 Цель: {profile['calories_goal']} ккал\n"
        f"🥩 Белки: {profile['protein_g']} г\n"
        f"🥑 Жиры: {profile['fat_g']} г\n"
        f"🍚 Углеводы: {profile['carbs_g']} г\n"
        f"💧 Норма воды: {profile['water_goal_ml']} мл\n"
        f"💦 Выпито воды: {get_today_water_ml(telegram_id)} мл\n\n"
        f"📌 Уже съедено: {today_kcal} ккал\n"
        f"📊 Сегодня по БЖУ: {today_macros['protein']} / {today_macros['fat']} / {today_macros['carbs']}\n"
        f"✅ Осталось до цели: {remain_kcal} ккал\n\n"
        "Это ориентировочные расчёты, а не медицинский план."
    )

def build_day_plan(profile: Optional[dict]) -> str:
    breakfast = _pick(BREAKFASTS, profile)
    lunch = _pick(LUNCHES, profile)
    dinner = _pick(DINNERS, profile)
    snack1 = _pick(SNACKS, profile)
    snack2 = _pick(SNACKS, profile)
    blocked = parse_blocked_products(profile)

    meals_count = int(profile.get("meals_per_day", 3)) if profile else 3
    target_kcal = int(profile.get("calories_goal", 2000)) if profile else 2000

    meal_rows = [("🥣 Завтрак", breakfast), ("🍗 Обед", lunch)]
    if meals_count >= 4:
        meal_rows.append(("🍏 Перекус", snack1))
    meal_rows.append(("🌙 Ужин", dinner))
    if meals_count >= 5:
        meal_rows.append(("🥜 Доп. перекус", snack2))

    selected = [item for _, item in meal_rows if item]
    if not selected:
        return _no_safe_food_text("план на день", profile)

    total_kcal = sum(x["kcal"] for x in selected)
    total_p = sum(x["p"] for x in selected)
    total_f = sum(x["f"] for x in selected)
    total_c = sum(x["c"] for x in selected)

    lines = ["🍽 Твой план питания на сегодня", ""]
    for meal_name, item in meal_rows:
        if item:
            lines.append(meal_name)
            lines.append(item_line(item))
            lines.append("")
    lines.extend([
        "━━━━━━━━━━━━━━",
        f"🔥 Итого: {total_kcal} ккал",
        f"🥩 БЖУ: {total_p} / {total_f} / {total_c}",
        f"🎯 Цель: {target_kcal} ккал",
    ])
    if blocked:
        lines.append("🚫 Запреты учтены")
    return "\n".join(lines)

def build_week_plan(profile: Optional[dict]) -> str:
    blocked = parse_blocked_products(profile)
    safe_breakfasts = _filter_safe_items(BREAKFASTS, profile)
    safe_lunches = _filter_safe_items(LUNCHES, profile)
    safe_dinners = _filter_safe_items(DINNERS, profile)

    if not (safe_breakfasts or safe_lunches or safe_dinners):
        return _no_safe_food_text("меню на неделю", profile)

    lines = ["📅 Меню на неделю", ""]
    for day in DAYS_RU:
        breakfast = random.choice(safe_breakfasts) if safe_breakfasts else None
        lunch = random.choice(safe_lunches) if safe_lunches else None
        dinner = random.choice(safe_dinners) if safe_dinners else None
        lines.append(f"{day}:")
        lines.append(f"🥣 Завтрак: {breakfast['name']}" if breakfast else "🥣 Завтрак: нет безопасного варианта")
        lines.append(f"🍗 Обед: {lunch['name']}" if lunch else "🍗 Обед: нет безопасного варианта")
        lines.append(f"🌙 Ужин: {dinner['name']}" if dinner else "🌙 Ужин: нет безопасного варианта")
        lines.append("")
    if blocked:
        lines.append("🚫 Запреты учтены во всём меню.")
    return "\n".join(lines).strip()

def build_shopping_list(profile: Optional[dict]) -> str:
    allowed = [item for item in SHOPPING_BASE if _safe_item_name(item, profile)]
    vegetables = [x for x in ["огурцы", "помидоры", "листья салата", "морковь", "брокколи"] if _safe_item_name(x, profile)]
    fruits = [x for x in ["яблоки", "бананы", "груши", "ягоды"] if _safe_item_name(x, profile)]
    blocked = parse_blocked_products(profile)

    lines = ["🛒 Список покупок на неделю", ""]
    if allowed:
        for item in allowed:
            lines.append(f"• {item}")
    if vegetables:
        lines.append("• овощи на выбор: " + ", ".join(vegetables))
    if fruits:
        lines.append("• фрукты на выбор: " + ", ".join(fruits))

    if len(lines) == 2:
        return _no_safe_food_text("список покупок", profile)

    if blocked:
        lines.append("")
        lines.append("🚫 Запреты уже исключены из списка.")
    return "\n".join(lines)

def build_dish_idea(profile: Optional[dict]) -> str:
    item = _pick(LUNCHES + DINNERS, profile)
    blocked = parse_blocked_products(profile)
    if not item:
        return _no_safe_food_text("идею блюда", profile)

    text = (
        "🥗 Идея блюда\n\n"
        f"{item['name']}\n"
        f"🔥 {item['kcal']} ккал\n"
        f"🥩 Б {item['p']} / 🥑 Ж {item['f']} / 🍚 У {item['c']}\n\n"
        "👨‍🍳 Принцип: белок + овощи + умеренная порция гарнира."
    )
    if blocked:
        text += "\n\n🚫 Запреты учтены."
    return text

def build_recipe_text(profile: Optional[dict], user_query: str = "") -> str:
    query_hits = forbidden_hits_in_text(user_query, profile)
    if query_hits:
        return (
            "🚫 Этот продукт у тебя в запретах.\n\n"
            "Я не буду предлагать рецепт с ним. Попроси рецепт из разрешённых продуктов."
        )

    query = normalize_food_text(user_query)
    safe_recipes = []
    for recipe in RECIPE_LIBRARY:
        joined = " ".join([recipe["title"], " ".join(recipe["tags"]), " ".join(recipe["ingredients"]), " ".join(recipe["steps"])])
        if _safe_item_name(joined, profile):
            safe_recipes.append(recipe)

    if not safe_recipes:
        return _no_safe_food_text("рецепт", profile)

    candidates = []
    for recipe in safe_recipes:
        joined = normalize_food_text(recipe["title"] + " " + " ".join(recipe["tags"]) + " " + " ".join(recipe["ingredients"]))
        if query and any(word in joined for word in query.split() if len(word) > 2):
            candidates.append(recipe)
        elif not query:
            candidates.append(recipe)

    if not candidates:
        candidates = safe_recipes

    recipe = random.choice(candidates)
    lines = [
        f"👨‍🍳 Рецепт: {recipe['title']}",
        "",
        f"⏱ Время: {recipe['cook_time']}",
        f"🔥 Калории: {recipe['kcal']} ккал",
        "",
        "🛒 Ингредиенты:",
    ]
    lines.extend(f"• {ingredient}" for ingredient in recipe["ingredients"])
    lines.append("")
    lines.append("Как готовить:")
    lines.extend(f"{index}. {step}" for index, step in enumerate(recipe["steps"], start=1))
    blocked = parse_blocked_products(profile)
    if blocked:
        lines.append("")
        lines.append("🚫 Запреты учтены.")
    return "\n".join(lines)

def build_weight_history_text(telegram_id: int) -> str:
    rows = get_recent_weight_logs(telegram_id, 7)
    if not rows:
        return "История веса пока пустая. Нажми '⚖️ Записать вес'."
    lines = ["Последние записи веса", ""]
    for row in rows:
        lines.append(f"— {row['created_at'][:10]}: {row['weight_kg']} кг")
    if len(rows) >= 2:
        diff = rows[0]["weight_kg"] - rows[-1]["weight_kg"]
        lines.append("")
        lines.append(f"Изменение за период: {diff:+.1f} кг")
    return "\n".join(lines)


def build_meal_history_text(telegram_id: int) -> str:
    rows = get_recent_meal_logs(telegram_id, 10)
    if not rows:
        return "История питания пока пустая. Нажми '🍴 Сегодня ел'."
    lines = ["Последние записи питания", ""]
    for row in rows:
        lines.append(
            f"— {row['created_at'][5:16]} | {row['meal_type']} | {row['meal_text']} | "
            f"~{row['estimated_kcal']} ккал | БЖУ {round(float(row['estimated_protein']), 1)} / {round(float(row['estimated_fat']), 1)} / {round(float(row['estimated_carbs']), 1)}"
        )
    today_macros = get_today_meal_macros(telegram_id)
    lines.append("")
    lines.append(f"Сегодня всего примерно: {get_today_meal_kcal(telegram_id)} ккал")
    lines.append(f"Сегодня по БЖУ: {today_macros['protein']} / {today_macros['fat']} / {today_macros['carbs']}")
    return "\n".join(lines)


def build_sleep_history_text(telegram_id: int) -> str:
    rows = get_recent_sleep_logs(telegram_id, 7)
    if not rows:
        return "История сна пока пустая. Нажми '😴 Записать сон'."
    avg_sleep = get_average_sleep_hours(telegram_id, 7)
    lines = ["Последние записи сна", ""]
    for row in rows:
        quality = int(row['quality'])
        quality_text = f"качество {quality}/5" if quality else "качество не указано"
        lines.append(f"— {row['created_at'][:10]}: {row['sleep_hours']} ч, {quality_text}")
    lines.append("")
    lines.append(f"Средний сон за последние записи: {avg_sleep} ч")
    if avg_sleep < 7:
        lines.append("Совет: старайся выходить хотя бы на 7–8 часов сна.")
    elif avg_sleep > 9.5:
        lines.append("Сон длинный. Смотри ещё и на самочувствие, а не только на часы.")
    else:
        lines.append("Режим сна выглядит неплохо. Старайся ложиться примерно в одно время.")
    return "\n".join(lines)


def healthy_swap_answer(key: str, profile: Optional[dict] = None) -> str:
    variants = {
        "сладкое": ["фрукт", "йогурт без лишнего сахара", "немного тёмного шоколада после основного приёма пищи"],
        "вечером": ["яйца с овощами", "творог", "рыба с овощами", "курица с овощами"],
        "завтрак": ["яйца и хлеб", "овсянка и йогурт", "творог и фрукт"],
        "перекус": ["фрукт", "йогурт", "орехи", "хлебец с сыром"],
        "вода": ["вода небольшими порциями в течение дня"],
        "после тренировки": ["йогурт и банан", "курица и рис", "творог и фрукт"],
    }
    safe_variants = [item for item in variants[key] if _safe_item_name(item, profile)]
    if not safe_variants:
        return _no_safe_food_text("подсказку без запрещённых продуктов", profile)
    if key == "вода":
        return "Пей воду небольшими порциями в течение дня. Держи бутылку рядом и делай несколько глотков после каждого приёма пищи."
    if key == "сладкое":
        return "Варианты замены сладкого без твоих запретов: " + ", ".join(safe_variants) + "."
    if key == "вечером":
        return "Вечером можно выбрать лёгкий сытный вариант без твоих запретов: " + ", ".join(safe_variants) + "."
    if key == "завтрак":
        return "Хороший завтрак = белок + источник энергии. Под твои запреты подходят: " + ", ".join(safe_variants) + "."
    if key == "перекус":
        return "Перекус без твоих запретов: " + ", ".join(safe_variants) + "."
    if key == "после тренировки":
        return "После тренировки подойдут варианты без твоих запретов: " + ", ".join(safe_variants) + "."
    return ", ".join(safe_variants)


def detect_risky_health_request(text: str) -> bool:
    triggers = ["экстремально похуд", "быстро похуд", "не есть", "голодать", "очистк", "таблетк", "слабитель", "вызвать рвоту"]
    return any(t in text for t in triggers)


async def generate_rule_reply(user_question: str, profile: Optional[dict], telegram_id: int) -> str:
    text = user_question.lower().strip()
    if detect_risky_health_request(text):
        return "Я не помогаю с опасными способами похудения или жёсткими ограничениями. Лучше идти через обычную еду, режим и постепенные изменения."
    if _contains_any(text, ["калории", "бжу", "белки", "жиры", "углеводы"]):
        return build_targets_text(profile, telegram_id)
    if _contains_any(text, ["вес", "история веса"]):
        return build_weight_history_text(telegram_id)
    if _contains_any(text, ["сон", "спал", "история сна"]):
        return build_sleep_history_text(telegram_id)
    if _contains_any(text, ["ел", "дневник еды", "история питания"]):
        return build_meal_history_text(telegram_id)
    if _contains_any(text, ["меню на неделю", "на неделю", "недел"]):
        return build_week_plan(profile)
    if _contains_any(text, ["рецепт", "как приготовить", "готовить"]):
        return build_recipe_text(profile, text)
    if _contains_any(text, ["план", "меню", "на день", "рацион"]):
        return build_day_plan(profile)
    if _contains_any(text, ["список покуп", "что купить", "продукты"]):
        return build_shopping_list(profile)
    if _contains_any(text, ["идея блюда", "что приготовить", "блюдо", "приготовить"]):
        return build_dish_idea(profile)
    if _contains_any(text, ["сладкое", "сахар"]):
        return healthy_swap_answer("сладкое", profile)
    if _contains_any(text, ["вечером", "ужин", "на ужин"]):
        return healthy_swap_answer("вечером", profile)
    if _contains_any(text, ["завтрак", "утром"]):
        return healthy_swap_answer("завтрак", profile)
    if _contains_any(text, ["перекус"]):
        return healthy_swap_answer("перекус", profile)
    if _contains_any(text, ["вода", "пить"]):
        return healthy_swap_answer("вода", profile)
    if _contains_any(text, ["после тренировки", "после зала", "трениров"]):
        return healthy_swap_answer("после тренировки", profile)
    return (
        "Я понимаю такие запросы:\n"
        "— калории и БЖУ\n"
        "— меню на день\n"
        "— меню на неделю\n"
        "— список покупок\n"
        "— рецепт\n"
        "— история веса\n"
        "— история сна\n"
        "— история питания\n"
        "— чем заменить сладкое"
    )


# =========================
# REMINDERS


# =========================
# REMINDERS
# =========================
async def reminder_loop(user_id: int) -> None:
    try:
        while True:
            profile = get_user_profile(user_id)
            if not profile or not int(profile.get("reminder_enabled", 0)):
                break
            now = datetime.now()
            target = now.replace(
                hour=int(profile.get("reminder_hour", 14)),
                minute=int(profile.get("reminder_minute", 0)),
                second=0,
                microsecond=0,
            )
            if target <= now:
                target += timedelta(days=1)
            wait_seconds = (target - now).total_seconds()
            await asyncio.sleep(wait_seconds)
            profile = get_user_profile(user_id)
            if not profile or not int(profile.get("reminder_enabled", 0)):
                break
            try:
                await bot.send_message(user_id, str(profile.get("reminder_text") or "Пора поесть и выпить воды 💧"))
            except Exception as e:
                logger.exception("Failed to send reminder to %s: %s", user_id, e)
                break
    except asyncio.CancelledError:
        pass
    finally:
        REMINDER_TASKS.pop(user_id, None)


def start_reminder_task(user_id: int) -> None:
    old_task = REMINDER_TASKS.get(user_id)
    if old_task and not old_task.done():
        old_task.cancel()
    REMINDER_TASKS[user_id] = asyncio.create_task(reminder_loop(user_id))


def stop_reminder_task(user_id: int) -> None:
    task = REMINDER_TASKS.get(user_id)
    if task and not task.done():
        task.cancel()
    REMINDER_TASKS.pop(user_id, None)


# =========================
# STATES
# =========================
class ProfileForm(StatesGroup):
    name = State()
    age = State()
    sex = State()
    height = State()
    weight = State()
    activity = State()
    goal = State()
    allergies = State()
    dislikes = State()
    meals_per_day = State()


class WeightForm(StatesGroup):
    value = State()


class MealForm(StatesGroup):
    text = State()


class SleepForm(StatesGroup):
    hours = State()
    quality = State()


class ReminderForm(StatesGroup):
    time = State()
    text = State()


class RestrictionForm(StatesGroup):
    allergies = State()
    dislikes = State()


# =========================
# KEYBOARDS
# =========================
def main_menu() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="📋 Анкета"), KeyboardButton(text="👤 Профиль"))
    builder.row(KeyboardButton(text="🚫 Мои запреты"), KeyboardButton(text="🔥 Калории и БЖУ"))
    builder.row(KeyboardButton(text="🍽 План на день"), KeyboardButton(text="📅 Меню на неделю"))
    builder.row(KeyboardButton(text="🛒 Список покупок"), KeyboardButton(text="🥗 Идея блюда"))
    builder.row(KeyboardButton(text="👨‍🍳 Рецепт"), KeyboardButton(text="💧 Норма воды"))
    builder.row(KeyboardButton(text="💧 +250 мл"), KeyboardButton(text="💧 +500 мл"))
    builder.row(KeyboardButton(text="🚰 История воды"), KeyboardButton(text="⚖️ Записать вес"))
    builder.row(KeyboardButton(text="📈 История веса"), KeyboardButton(text="↩️ Удалить вес"))
    builder.row(KeyboardButton(text="🍴 Сегодня ел"), KeyboardButton(text="📓 История питания"))
    builder.row(KeyboardButton(text="↩️ Удалить еду"), KeyboardButton(text="😴 Записать сон"))
    builder.row(KeyboardButton(text="🛌 История сна"), KeyboardButton(text="↩️ Удалить сон"))
    builder.row(KeyboardButton(text="⏰ Напоминания ВКЛ"), KeyboardButton(text="🔕 Напоминания ВЫКЛ"))
    builder.row(KeyboardButton(text="💬 Спросить бота"))
    return builder.as_markup(resize_keyboard=True)


def sex_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="Мужской"), KeyboardButton(text="Женский"))
    return builder.as_markup(resize_keyboard=True)


def activity_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="Низкая"), KeyboardButton(text="Средняя"))
    builder.row(KeyboardButton(text="Высокая"))
    return builder.as_markup(resize_keyboard=True)


def goal_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="Сбалансированное питание"))
    builder.row(KeyboardButton(text="Безопасное снижение веса"))
    builder.row(KeyboardButton(text="Набор массы"))
    builder.row(KeyboardButton(text="Больше энергии"))
    return builder.as_markup(resize_keyboard=True)


def meals_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="3"), KeyboardButton(text="4"), KeyboardButton(text="5"))
    return builder.as_markup(resize_keyboard=True)


MAIN_MENU_BUTTONS = {
    "📋 Анкета",
    "👤 Профиль",
    "🚫 Мои запреты",
    "🔥 Калории и БЖУ",
    "🍽 План на день",
    "📅 Меню на неделю",
    "🛒 Список покупок",
    "🥗 Идея блюда",
    "👨‍🍳 Рецепт",
    "💧 Норма воды",
    "💧 +250 мл",
    "💧 +500 мл",
    "🚰 История воды",
    "⚖️ Записать вес",
    "📈 История веса",
    "↩️ Удалить вес",
    "🍴 Сегодня ел",
    "📓 История питания",
    "↩️ Удалить еду",
    "😴 Записать сон",
    "🛌 История сна",
    "↩️ Удалить сон",
    "⏰ Напоминания ВКЛ",
    "🔕 Напоминания ВЫКЛ",
    "💬 Спросить бота",
}


# =========================
# BOT SETUP
# =========================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# =========================
# HANDLERS
# =========================
@dp.message(CommandStart())
async def start_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "Привет! Это FoodBalance — твой помощник по питанию и режиму.\n\n"
        "Я помогу считать калории и БЖУ, подбирать меню без запрещённых продуктов, сохранять вес, еду, сон и напоминания.\n\n"
        "Нажми нужную кнопку ниже и начнём.",
        reply_markup=main_menu(),
    )


@dp.message(Command("help"))
async def help_handler(message: Message) -> None:
    await message.answer(
        "Вот что умеет FoodBalance:\n\n"
        "📋 Анкета и профиль\n"
        "🔥 Калории, БЖУ и дневной баланс\n"
        "🍽 План на день и меню на неделю\n"
        "🛒 Список покупок\n"
        "👨‍🍳 Рецепты с шагами\n"
        "🚫 Глобальные запреты продуктов\n"
        "⚖️ Вес и 🍴 дневник питания\n"
        "😴 Трекинг сна\n"
        "⏰ Напоминания\n\n"
        "Для выхода из любого ввода используй /cancel"
    )


@dp.message(StateFilter("*"), Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Текущее действие отменено.", reply_markup=main_menu())


@dp.message(StateFilter("*"), F.text.in_(MAIN_MENU_BUTTONS))
async def main_menu_button_router(message: Message, state: FSMContext) -> None:
    """Главные кнопки должны работать всегда, даже если пользователь застрял в анкете или другом вводе."""
    await state.clear()
    text = (message.text or "").strip()
    user_id = message.from_user.id

    if text == "📋 Анкета":
        await state.set_state(ProfileForm.name)
        await message.answer("Как тебя зовут?")
        return

    if text == "👤 Профиль":
        profile = get_user_profile(user_id)
        if not profile:
            await message.answer("Профиль пока не заполнен. Нажми: 📋 Анкета", reply_markup=main_menu())
            return
        await message.answer("Твой профиль:\n\n" + profile_summary(profile), reply_markup=main_menu())
        return

    if text == "🚫 Мои запреты":
        profile = get_user_profile(user_id)
        if not profile:
            await message.answer("Сначала заполни анкету. Потом можно будет отдельно менять запреты.", reply_markup=main_menu())
            return
        await state.set_state(RestrictionForm.allergies)
        await message.answer(
            f"Текущие аллергии: {profile.get('allergies', 'нет')}\n"
            f"Текущие нелюбимые продукты: {profile.get('dislikes', 'нет')}\n\n"
            "Напиши аллергии через запятую. Если нет, напиши: нет"
        )
        return

    if text == "🔥 Калории и БЖУ":
        profile = get_user_profile(user_id)
        await message.answer(build_targets_text(profile, user_id), reply_markup=main_menu())
        return

    if text == "🍽 План на день":
        await message.answer(build_day_plan(get_user_profile(user_id)), reply_markup=main_menu())
        return

    if text == "📅 Меню на неделю":
        await message.answer(build_week_plan(get_user_profile(user_id)), reply_markup=main_menu())
        return

    if text == "🛒 Список покупок":
        await message.answer(build_shopping_list(get_user_profile(user_id)), reply_markup=main_menu())
        return

    if text == "🥗 Идея блюда":
        await message.answer(build_dish_idea(get_user_profile(user_id)), reply_markup=main_menu())
        return

    if text == "👨‍🍳 Рецепт":
        await message.answer(build_recipe_text(get_user_profile(user_id)), reply_markup=main_menu())
        return

    if text == "💧 Норма воды":
        profile = get_user_profile(user_id)
        await message.answer(build_water_text(profile, user_id), reply_markup=main_menu())
        return

    if text == "💧 +250 мл":
        save_water_log(user_id, 250)
        await message.answer(build_water_text(get_user_profile(user_id), user_id), reply_markup=main_menu())
        return

    if text == "💧 +500 мл":
        save_water_log(user_id, 500)
        await message.answer(build_water_text(get_user_profile(user_id), user_id), reply_markup=main_menu())
        return

    if text == "🚰 История воды":
        await message.answer(build_water_history_text(get_user_profile(user_id), user_id), reply_markup=main_menu())
        return

    if text == "⚖️ Записать вес":
        await state.set_state(WeightForm.value)
        await message.answer("Напиши текущий вес числом, например: 64.5")
        return

    if text == "📈 История веса":
        await message.answer(build_weight_history_text(user_id), reply_markup=main_menu())
        return

    if text == "↩️ Удалить вес":
        latest = delete_last_weight_log(user_id)
        if latest is None:
            await message.answer("История веса уже пустая.", reply_markup=main_menu())
        else:
            await message.answer(f"Последняя запись веса удалена. Текущий вес в профиле: {latest} кг", reply_markup=main_menu())
        return

    if text == "🍴 Сегодня ел":
        await state.set_state(MealForm.text)
        await message.answer("Напиши, что ты сегодня ел. Например: 150 г курицы, 200 г риса и салат")
        return

    if text == "📓 История питания":
        await message.answer(build_meal_history_text(user_id), reply_markup=main_menu())
        return

    if text == "↩️ Удалить еду":
        row = delete_last_meal_log(user_id)
        if not row:
            await message.answer("История питания уже пустая.", reply_markup=main_menu())
        else:
            await message.answer(f"Удалил последнюю запись еды: {row['meal_text']} (~{row['estimated_kcal']} ккал)", reply_markup=main_menu())
        return

    if text == "😴 Записать сон":
        await state.set_state(SleepForm.hours)
        await message.answer("Сколько часов ты спал? Напиши числом, например: 7.5")
        return

    if text == "🛌 История сна":
        await message.answer(build_sleep_history_text(user_id), reply_markup=main_menu())
        return

    if text == "↩️ Удалить сон":
        row = delete_last_sleep_log(user_id)
        if not row:
            await message.answer("История сна уже пустая.", reply_markup=main_menu())
        else:
            await message.answer(f"Удалил последнюю запись сна: {row['sleep_hours']} ч, качество {row['quality']}/5", reply_markup=main_menu())
        return

    if text == "⏰ Напоминания ВКЛ":
        profile = get_user_profile(user_id)
        if not profile:
            await message.answer("Сначала заполни анкету, потом включим напоминания.", reply_markup=main_menu())
            return
        await state.set_state(ReminderForm.time)
        await message.answer("Напиши время напоминания в формате ЧЧ:ММ. Например: 14:30")
        return

    if text == "🔕 Напоминания ВЫКЛ":
        profile = get_user_profile(user_id)
        if not profile:
            await message.answer("Профиль пока не заполнен.", reply_markup=main_menu())
            return
        update_reminder_settings(
            telegram_id=user_id,
            enabled=False,
            hour=int(profile.get("reminder_hour", 14)),
            minute=int(profile.get("reminder_minute", 0)),
            text=str(profile.get("reminder_text") or "Пора поесть и выпить воды 💧"),
        )
        stop_reminder_task(user_id)
        await message.answer("Напоминания выключены.", reply_markup=main_menu())
        return

    if text == "💬 Спросить бота":
        await message.answer(
            "Напиши вопрос обычным сообщением.\n\n"
            "Примеры:\n"
            "— рассчитай калории\n"
            "— рецепт из курицы\n"
            "— история сна\n"
            "— что есть вечером\n"
            "— меню на день",
            reply_markup=main_menu(),
        )
        return

    await message.answer("Главное меню открыто.", reply_markup=main_menu())


@dp.message(F.text == "📋 Анкета")
async def fill_form_start(message: Message, state: FSMContext) -> None:
    await state.set_state(ProfileForm.name)
    await message.answer("Как тебя зовут?")


@dp.message(ProfileForm.name)
async def form_name(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if not value:
        await message.answer("Напиши имя текстом.")
        return
    await state.update_data(name=value)
    await state.set_state(ProfileForm.age)
    await message.answer("Сколько тебе лет?")


@dp.message(ProfileForm.age)
async def form_age(message: Message, state: FSMContext) -> None:
    try:
        age = int((message.text or "").strip())
        if age < 10 or age > 120:
            raise ValueError
    except ValueError:
        await message.answer("Напиши возраст числом, например: 16")
        return
    await state.update_data(age=age)
    await state.set_state(ProfileForm.sex)
    await message.answer("Выбери пол.", reply_markup=sex_keyboard())


@dp.message(ProfileForm.sex)
async def form_sex(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if value not in {"Мужской", "Женский"}:
        await message.answer("Выбери вариант на клавиатуре.", reply_markup=sex_keyboard())
        return
    await state.update_data(sex=value)
    await state.set_state(ProfileForm.height)
    await message.answer("Какой у тебя рост в сантиметрах?")


@dp.message(ProfileForm.height)
async def form_height(message: Message, state: FSMContext) -> None:
    try:
        height = int((message.text or "").strip())
        if height < 100 or height > 250:
            raise ValueError
    except ValueError:
        await message.answer("Напиши рост числом, например: 170")
        return
    await state.update_data(height_cm=height)
    await state.set_state(ProfileForm.weight)
    await message.answer("Какой у тебя вес в килограммах?")


@dp.message(ProfileForm.weight)
async def form_weight(message: Message, state: FSMContext) -> None:
    try:
        weight = float((message.text or "").strip().replace(",", "."))
        if weight < 25 or weight > 400:
            raise ValueError
    except ValueError:
        await message.answer("Напиши вес числом, например: 65")
        return
    await state.update_data(weight_kg=weight)
    await state.set_state(ProfileForm.activity)
    await message.answer("Какой у тебя уровень активности?", reply_markup=activity_keyboard())


@dp.message(ProfileForm.activity)
async def form_activity(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    if value not in {"Низкая", "Средняя", "Высокая"}:
        await message.answer("Выбери вариант на клавиатуре.", reply_markup=activity_keyboard())
        return
    await state.update_data(activity_level=value)
    await state.set_state(ProfileForm.goal)
    await message.answer("Какая у тебя цель?", reply_markup=goal_keyboard())


@dp.message(ProfileForm.goal)
async def form_goal(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip()
    allowed = {"Сбалансированное питание", "Безопасное снижение веса", "Набор массы", "Больше энергии"}
    if value not in allowed:
        await message.answer("Выбери цель на клавиатуре.", reply_markup=goal_keyboard())
        return
    await state.update_data(goal=value)
    await state.set_state(ProfileForm.allergies)
    await message.answer("Есть аллергии? Напиши через запятую. Если нет, напиши: нет")


@dp.message(ProfileForm.allergies)
async def form_allergies(message: Message, state: FSMContext) -> None:
    await state.update_data(allergies=((message.text or "").strip() or "нет"))
    await state.set_state(ProfileForm.dislikes)
    await message.answer("Какие продукты ты не любишь или не ешь? Напиши через запятую. Если нет, напиши: нет")


@dp.message(ProfileForm.dislikes)
async def form_dislikes(message: Message, state: FSMContext) -> None:
    await state.update_data(dislikes=((message.text or "").strip() or "нет"))
    await state.set_state(ProfileForm.meals_per_day)
    await message.answer(
        "Сколько приёмов пищи тебе удобно в день?\n\n"
        "Продукты из полей 'аллергия' и 'не люблю' будут полностью исключаться из меню, рецептов, списка покупок, идей блюд и подсказок.",
        reply_markup=meals_keyboard(),
    )


@dp.message(ProfileForm.meals_per_day)
async def form_meals(message: Message, state: FSMContext) -> None:
    try:
        meals_per_day = int((message.text or "").strip())
        if meals_per_day not in {3, 4, 5}:
            raise ValueError
    except ValueError:
        await message.answer("Выбери 3, 4 или 5 на клавиатуре.", reply_markup=meals_keyboard())
        return

    data = await state.get_data()
    temp_profile = {
        "age": data["age"],
        "sex": data["sex"],
        "height_cm": data["height_cm"],
        "weight_kg": data["weight_kg"],
        "activity_level": data["activity_level"],
        "goal": data["goal"],
    }
    targets = calculate_targets(temp_profile)

    save_user_profile(
        telegram_id=message.from_user.id,
        name=data["name"],
        age=data["age"],
        sex=data["sex"],
        height_cm=data["height_cm"],
        weight_kg=data["weight_kg"],
        activity_level=data["activity_level"],
        goal=data["goal"],
        allergies=data["allergies"],
        dislikes=data["dislikes"],
        meals_per_day=meals_per_day,
        water_goal_ml=targets["water"],
        calories_goal=targets["calories"],
        protein_g=targets["protein"],
        fat_g=targets["fat"],
        carbs_g=targets["carbs"],
    )
    save_weight_log(message.from_user.id, float(data["weight_kg"]))

    await state.clear()
    await message.answer(
        "Анкета сохранена.\n\n"
        f"Калории: {targets['calories']} ккал\n"
        f"БЖУ: {targets['protein']} / {targets['fat']} / {targets['carbs']}\n"
        f"Вода: {targets['water']} мл",
        reply_markup=main_menu(),
    )


@dp.message(F.text == "🚫 Мои запреты")
async def restrictions_start(message: Message, state: FSMContext) -> None:
    profile = get_user_profile(message.from_user.id)
    if not profile:
        await message.answer("Сначала заполни анкету. Потом можно будет отдельно менять запреты.")
        return
    await state.set_state(RestrictionForm.allergies)
    current_allergies = profile.get("allergies", "нет")
    current_dislikes = profile.get("dislikes", "нет")
    await message.answer(
        f"Текущие аллергии: {current_allergies}\n"
        f"Текущие нелюбимые продукты: {current_dislikes}\n\n"
        "Напиши аллергии через запятую. Если нет, напиши: нет"
    )


@dp.message(RestrictionForm.allergies)
async def restrictions_allergies(message: Message, state: FSMContext) -> None:
    await state.update_data(allergies=((message.text or "").strip() or "нет"))
    await state.set_state(RestrictionForm.dislikes)
    await message.answer("Теперь напиши продукты, которые ты не ешь или не любишь, через запятую. Если нет, напиши: нет")


@dp.message(RestrictionForm.dislikes)
async def restrictions_dislikes(message: Message, state: FSMContext) -> None:
    dislikes = ((message.text or "").strip() or "нет")
    data = await state.get_data()
    profile = get_user_profile(message.from_user.id)
    if not profile:
        await state.clear()
        await message.answer("Профиль не найден. Сначала заполни анкету.", reply_markup=main_menu())
        return

    save_user_profile(
        telegram_id=message.from_user.id,
        name=profile["name"],
        age=int(profile["age"]),
        sex=profile["sex"],
        height_cm=int(profile["height_cm"]),
        weight_kg=float(profile["weight_kg"]),
        activity_level=profile["activity_level"],
        goal=profile["goal"],
        allergies=data["allergies"],
        dislikes=dislikes,
        meals_per_day=int(profile["meals_per_day"]),
        water_goal_ml=int(profile["water_goal_ml"]),
        calories_goal=int(profile["calories_goal"]),
        protein_g=int(profile["protein_g"]),
        fat_g=int(profile["fat_g"]),
        carbs_g=int(profile["carbs_g"]),
    )

    await state.clear()
    blocked = parse_blocked_products({"allergies": data["allergies"], "dislikes": dislikes})
    await message.answer(
        "Запреты обновлены.\n\n" + ("Исключены продукты: " + ", ".join(blocked[:12]) if blocked else "Сейчас запретов нет."),
        reply_markup=main_menu(),
    )


@dp.message(F.text == "👤 Профиль")
async def show_profile(message: Message) -> None:
    profile = get_user_profile(message.from_user.id)
    if not profile:
        await message.answer("Профиль пока не заполнен. Нажми: 📋 Анкета")
        return
    await message.answer("Твой профиль:\n\n" + profile_summary(profile))


@dp.message(F.text == "🔥 Калории и БЖУ")
async def targets_handler(message: Message) -> None:
    profile = get_user_profile(message.from_user.id)
    await message.answer(build_targets_text(profile, message.from_user.id))


@dp.message(F.text == "🍽 План на день")
async def meal_plan_handler(message: Message) -> None:
    profile = get_user_profile(message.from_user.id)
    await message.answer(build_day_plan(profile))


@dp.message(F.text == "📅 Меню на неделю")
async def week_plan_handler(message: Message) -> None:
    profile = get_user_profile(message.from_user.id)
    await message.answer(build_week_plan(profile))


@dp.message(F.text == "🛒 Список покупок")
async def shopping_list_handler(message: Message) -> None:
    profile = get_user_profile(message.from_user.id)
    await message.answer(build_shopping_list(profile))


@dp.message(F.text == "🥗 Идея блюда")
async def dish_idea_handler(message: Message) -> None:
    profile = get_user_profile(message.from_user.id)
    await message.answer(build_dish_idea(profile))


@dp.message(F.text == "👨‍🍳 Рецепт")
async def recipe_handler(message: Message) -> None:
    profile = get_user_profile(message.from_user.id)
    await message.answer(build_recipe_text(profile))


@dp.message(F.text == "💧 Норма воды")
async def water_handler(message: Message) -> None:
    profile = get_user_profile(message.from_user.id)
    value = estimate_water_goal(profile)
    await message.answer(f"Твоя ориентировочная норма воды: около {value} мл в день.")


@dp.message(F.text == "⚖️ Записать вес")
async def start_weight_log(message: Message, state: FSMContext) -> None:
    await state.set_state(WeightForm.value)
    await message.answer("Напиши текущий вес числом, например: 64.5")


@dp.message(WeightForm.value)
async def save_weight(message: Message, state: FSMContext) -> None:
    try:
        weight = float((message.text or "").strip().replace(",", "."))
        if weight < 25 or weight > 400:
            raise ValueError
    except ValueError:
        await message.answer("Напиши вес числом, например: 64.5")
        return

    save_weight_log(message.from_user.id, weight)
    profile = get_user_profile(message.from_user.id)
    if profile:
        profile.update({"weight_kg": weight})
        targets = calculate_targets(profile)
        save_user_profile(
            telegram_id=message.from_user.id,
            name=profile["name"],
            age=int(profile["age"]),
            sex=profile["sex"],
            height_cm=int(profile["height_cm"]),
            weight_kg=weight,
            activity_level=profile["activity_level"],
            goal=profile["goal"],
            allergies=profile["allergies"],
            dislikes=profile["dislikes"],
            meals_per_day=int(profile["meals_per_day"]),
            water_goal_ml=targets["water"],
            calories_goal=targets["calories"],
            protein_g=targets["protein"],
            fat_g=targets["fat"],
            carbs_g=targets["carbs"],
        )

    await state.clear()
    await message.answer(
        f"Вес сохранён: {weight} кг\n\n" + build_weight_history_text(message.from_user.id),
        reply_markup=main_menu(),
    )


@dp.message(F.text == "📈 История веса")
async def weight_history(message: Message) -> None:
    await message.answer(build_weight_history_text(message.from_user.id))


@dp.message(F.text == "🍴 Сегодня ел")
async def start_meal_log(message: Message, state: FSMContext) -> None:
    await state.set_state(MealForm.text)
    await message.answer("Напиши, что ты сегодня ел. Например: 150 г курицы, 200 г риса и 100 г салата")


@dp.message(MealForm.text)
async def save_meal(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Напиши, что ты ел.")
        return

    profile = get_user_profile(message.from_user.id)
    hits = forbidden_hits_in_text(text, profile)
    if hits:
        await state.clear()
        await message.answer(
            "Эта запись содержит продукт из твоих запретов, поэтому я не буду сохранять её в дневник.\n\n"
            "Чтобы изменить запреты, нажми: 🚫 Мои запреты",
            reply_markup=main_menu(),
        )
        return

    details = estimate_meal_details(text)

    if details["precision"] == "none":
        await message.answer(
            "Я не могу точно посчитать калории и БЖУ по такому описанию.\n\n"
            "Напиши в формате:\n"
            "• 150 г курицы\n"
            "• 120 г риса\n"
            "• 100 г салата\n"
            "или\n"
            "• 2 яйца\n"
            "• 250 мл кефира"
        )
        return

    save_meal_log(
        message.from_user.id,
        text,
        "приём пищи",
        int(details["kcal"]),
        float(details["protein"]),
        float(details["fat"]),
        float(details["carbs"]),
    )

    today_total = get_today_meal_kcal(message.from_user.id)
    items_text = "\n".join(
        f"— {item['name']}: {item['grams']} г ≈ {item['kcal']} ккал | Б {item['protein']} / Ж {item['fat']} / У {item['carbs']}"
        for item in details["items"]
    )

    if details["precision"] == "high":
        precision_text = "✅ Точный расчёт по граммовкам"
    elif details["precision"] == "medium":
        precision_text = "⚠️ Частично точный расчёт: часть порций определена автоматически"
    else:
        precision_text = "⚠️ Приблизительный расчёт"

    await state.clear()
    await message.answer(
        f"{precision_text}\n\n"
        f"Записал: {text}\n"
        f"Калории: {details['kcal']} ккал\n"
        f"БЖУ: {details['protein']} / {details['fat']} / {details['carbs']}\n\n"
        f"Состав:\n{items_text}\n\n"
        f"Сегодня всего: {today_total} ккал",
        reply_markup=main_menu(),
    )


@dp.message(F.text == "📓 История питания")
async def meal_history(message: Message) -> None:
    await message.answer(build_meal_history_text(message.from_user.id))


@dp.message(F.text == "😴 Записать сон")
async def start_sleep_log(message: Message, state: FSMContext) -> None:
    await state.set_state(SleepForm.hours)
    await message.answer("Сколько часов ты спал? Напиши числом, например: 7.5")


@dp.message(SleepForm.hours)
async def save_sleep_hours(message: Message, state: FSMContext) -> None:
    try:
        hours = float((message.text or "").strip().replace(",", "."))
        if hours < 0.5 or hours > 16:
            raise ValueError
    except ValueError:
        await message.answer("Напиши часы сна числом, например: 7.5")
        return
    await state.update_data(hours=hours)
    await state.set_state(SleepForm.quality)
    await message.answer("Оцени качество сна от 1 до 5. Если не хочешь, отправь 0")


@dp.message(SleepForm.quality)
async def save_sleep_quality(message: Message, state: FSMContext) -> None:
    try:
        quality = int((message.text or "").strip())
        if quality < 0 or quality > 5:
            raise ValueError
    except ValueError:
        await message.answer("Напиши число от 0 до 5.")
        return
    data = await state.get_data()
    hours = float(data["hours"])
    save_sleep_log(message.from_user.id, hours, quality)
    await state.clear()
    quality_text = f", качество {quality}/5" if quality else ""
    await message.answer(
        f"Сон сохранён: {hours} ч{quality_text}\n\n" + build_sleep_history_text(message.from_user.id),
        reply_markup=main_menu(),
    )


@dp.message(F.text == "🛌 История сна")
async def sleep_history(message: Message) -> None:
    await message.answer(build_sleep_history_text(message.from_user.id))


@dp.message(F.text == "⏰ Напоминания ВКЛ")
async def reminder_on_start(message: Message, state: FSMContext) -> None:
    profile = get_user_profile(message.from_user.id)
    if not profile:
        await message.answer("Сначала заполни анкету, потом включим напоминания.")
        return
    await state.set_state(ReminderForm.time)
    await message.answer("Напиши время напоминания в формате ЧЧ:ММ. Например: 14:30")


@dp.message(ReminderForm.time)
async def reminder_time(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    try:
        hour_str, minute_str = text.split(":")
        hour = int(hour_str)
        minute = int(minute_str)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except Exception:
        await message.answer("Нужен формат ЧЧ:ММ. Например: 14:30")
        return
    await state.update_data(reminder_hour=hour, reminder_minute=minute)
    await state.set_state(ReminderForm.text)
    await message.answer("Теперь напиши текст напоминания или отправь: стандарт")


@dp.message(ReminderForm.text)
async def reminder_text_save(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text or text.lower() == "стандарт":
        text = "Пора поесть и выпить воды 💧"
    data = await state.get_data()
    update_reminder_settings(
        telegram_id=message.from_user.id,
        enabled=True,
        hour=int(data["reminder_hour"]),
        minute=int(data["reminder_minute"]),
        text=text,
    )
    start_reminder_task(message.from_user.id)
    await state.clear()
    await message.answer(
        f"Напоминания включены на {int(data['reminder_hour']):02d}:{int(data['reminder_minute']):02d}",
        reply_markup=main_menu(),
    )


@dp.message(F.text == "🔕 Напоминания ВЫКЛ")
async def reminder_off(message: Message) -> None:
    profile = get_user_profile(message.from_user.id)
    if not profile:
        await message.answer("Профиль пока не заполнен.")
        return
    update_reminder_settings(
        telegram_id=message.from_user.id,
        enabled=False,
        hour=int(profile.get("reminder_hour", 14)),
        minute=int(profile.get("reminder_minute", 0)),
        text=str(profile.get("reminder_text") or "Пора поесть и выпить воды 💧"),
    )
    stop_reminder_task(message.from_user.id)
    await message.answer("Напоминания выключены.", reply_markup=main_menu())


@dp.message(F.text == "💬 Спросить бота")
async def ask_bot_hint(message: Message) -> None:
    await message.answer(
        "Напиши вопрос обычным сообщением.\n\n"
        "Примеры:\n"
        "— рассчитай калории\n"
        "— рецепт из курицы\n"
        "— история сна\n"
        "— что есть вечером\n"
        "— меню на день"
    )


@dp.message(Command("admin"))
async def admin_handler(message: Message) -> None:
    if not is_admin(message.from_user.id):
        await message.answer(
            "Команда /admin доступна только администратору. Добавь свой Telegram ID в переменную окружения ADMIN_IDS.",
            reply_markup=main_menu(),
        )
        return
    await message.answer(build_admin_stats_text(), reply_markup=main_menu())


@dp.message(Command("menu"))
async def menu_handler(message: Message) -> None:
    await message.answer("Главное меню открыто.", reply_markup=main_menu())


@dp.message(Command("profile"))
async def profile_command(message: Message) -> None:
    profile = get_user_profile(message.from_user.id)
    if not profile:
        await message.answer("Профиль пока не заполнен. Нажми: 📋 Анкета")
        return
    await message.answer("Твой профиль:\n\n" + profile_summary(profile))


@dp.message(Command("day"))
async def day_command(message: Message) -> None:
    profile = get_user_profile(message.from_user.id)
    await message.answer(build_day_plan(profile))


@dp.message(Command("week"))
async def week_command(message: Message) -> None:
    profile = get_user_profile(message.from_user.id)
    await message.answer(build_week_plan(profile))


@dp.message(Command("shopping"))
async def shopping_command(message: Message) -> None:
    profile = get_user_profile(message.from_user.id)
    await message.answer(build_shopping_list(profile))


@dp.message(Command("recipe"))
async def recipe_command(message: Message) -> None:
    profile = get_user_profile(message.from_user.id)
    await message.answer(build_recipe_text(profile))


@dp.message(Command("sleep"))
async def sleep_command(message: Message) -> None:
    await message.answer(build_sleep_history_text(message.from_user.id))


@dp.message()
async def free_text_handler(message: Message) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Напиши вопрос текстом.")
        return
    profile = get_user_profile(message.from_user.id)
    answer = await generate_rule_reply(text, profile, message.from_user.id)
    await message.answer(answer)



# =========================
# STRICT GLOBAL RESTRICTIONS OVERRIDE
# =========================
# Этот блок специально стоит ниже старых функций и переопределяет их.
# Теперь запреты работают по категориям и по корням слов во всех разделах.
def _restriction_library() -> dict[str, list[str]]:
    return {
        "молочные продукты": [
            "молоч", "молоко", "лактоз", "кефир", "йогурт", "творог", "творож",
            "сыр", "сырн", "сметан", "сливк", "сливоч", "ряженка", "айран", "тан",
            "морожен", "простокваша", "брынза", "фета", "моцарелла", "пармезан", "сулугуни",
        ],
        "мясные продукты": [
            "мяс", "говядин", "свинин", "баранин", "телятина", "фарш", "стейк",
            "котлет", "колбас", "сосиск", "сардельк", "бекон", "ветчин", "карбонад",
            "птиц", "кур", "куриц", "курин", "грудка", "индей", "утка", "гусь",
        ],
        "мясо": [
            "мяс", "говядин", "свинин", "баранин", "телятина", "фарш", "стейк",
            "котлет", "колбас", "сосиск", "сардельк", "бекон", "ветчин", "карбонад",
        ],
        "птица": [
            "птиц", "кур", "куриц", "курин", "грудка", "индей", "утка", "гусь",
        ],
        "рыба": [
            "рыб", "лосос", "семг", "форел", "тунец", "треск", "хек", "минтай",
            "скумбр", "сельд", "сардин", "карп", "судак",
        ],
        "морепродукты": [
            "морепродукт", "кревет", "кальмар", "миди", "краб", "устриц", "осьминог",
        ],
        "яйца": [
            "яйц", "яич", "омлет", "желток", "белок яйца",
        ],
        "крупы": [
            "круп", "рис", "греч", "овсян", "овес", "геркулес", "пшено", "пшенн",
            "перлов", "булгур", "кускус", "манк", "киноа",
        ],
        "мучное": [
            "мучн", "мука", "пшен", "рожь", "ячмен", "глютен", "хлеб", "тост",
            "хлебец", "хлебц", "булк", "батон", "лаваш", "лепеш", "выпеч",
            "макарон", "паста", "спагет", "лапш", "вермиш", "блин", "олад",
        ],
        "бобовые": [
            "бобов", "фасол", "чечев", "нут", "горох", "маш", "соя", "соев", "тофу",
        ],
        "овощи": [
            "овощ", "салат", "огурц", "помид", "томат", "брокк", "морков", "капуст",
            "перец", "лук", "чеснок", "кабач", "баклаж", "свекл", "зелень", "шпинат", "гриб", "шампин",
        ],
        "фрукты": [
            "фрукт", "яблок", "банан", "груш", "апельс", "мандарин", "лимон",
            "виноград", "персик", "абрикос", "цитрус",
        ],
        "ягоды": [
            "ягод", "клубник", "малин", "черник", "смород", "вишн",
        ],
        "орехи": [
            "орех", "миндал", "арахис", "фундук", "кешью", "фисташ", "грецк",
        ],
        "семечки": [
            "семеч", "семена", "кунжут", "чиа", "лен", "тыквен", "подсолнеч",
        ],
        "сладости": [
            "слад", "сахар", "сироп", "мед", "варень", "джем", "конфет", "шоколад",
            "торт", "печень", "пирожн", "кекс", "морожен",
        ],
        "фастфуд": [
            "фастфуд", "бургер", "пицц", "шаурм", "фри", "наггет", "хот дог", "хот-дог",
        ],
        "соусы": [
            "соус", "майонез", "кетчуп", "горчиц",
        ],
        "масла": [
            "масло", "оливков", "растительн", "подсолнеч", "сливоч",
        ],
        "напитки": [
            "напит", "газиров", "кола", "лимонад", "сок", "нектар", "кофе", "кофеин", "энергет",
        ],
    }


def parse_blocked_products(profile: Optional[dict]) -> list[str]:
    """Возвращает расширенный список запретов: категория -> все продукты внутри неё."""
    blocked_text = _blocked_text(profile)
    if not blocked_text:
        return []

    normalized = normalize_food_text(blocked_text)
    empty_words = {"нет", "не", "none", "no", "ничего", "нет нет"}
    if normalized in empty_words:
        return []

    library = _restriction_library()
    parts = [normalized]
    parts += [p.strip() for p in re.split(r"[,;\n/]+", normalized) if p.strip()]

    result: set[str] = set()

    def add_category(category: str) -> None:
        result.add(category)
        for token in library.get(category, []):
            if token:
                result.add(token)

    # 1) Если в тексте есть название категории или корень категории — добавляем всю категорию.
    category_stems = {
        "молоч": "молочные продукты", "лактоз": "молочные продукты", "кисломолоч": "молочные продукты",
        "мяс": "мясные продукты", "колбас": "мясные продукты", "сосиск": "мясные продукты",
        "птиц": "птица", "кур": "птица", "индей": "птица",
        "рыб": "рыба", "морепродукт": "морепродукты",
        "яйц": "яйца", "яич": "яйца", "омлет": "яйца",
        "круп": "крупы", "злак": "крупы", "мучн": "мучное", "глютен": "мучное",
        "бобов": "бобовые", "овощ": "овощи", "фрукт": "фрукты", "ягод": "ягоды",
        "орех": "орехи", "семеч": "семечки", "слад": "сладости", "сахар": "сладости",
        "фастфуд": "фастфуд", "соус": "соусы", "масл": "масла", "напит": "напитки",
        "газиров": "напитки", "кофе": "напитки", "сок": "напитки",
    }

    for part in parts:
        for category in library:
            if category in part:
                add_category(category)
        for stem, category in category_stems.items():
            if stem in part:
                add_category(category)

    # 2) Если написан конкретный продукт — добавляем его и всю категорию, где он встречается.
    for part in parts:
        words = [w for w in re.split(r"\s+", part) if len(w) >= 3]
        for word in words + [part]:
            clean = word.strip(" .,!?:()-—")
            if not clean or clean in empty_words or clean in {"под", "без", "для", "или", "что", "это", "есть", "еда", "продукт", "продукты", "запрет", "запретом", "запрещено", "нельзя"}:
                continue
            for category, tokens in library.items():
                if any(clean == token or (len(clean) >= 4 and (clean in token or token in clean)) for token in tokens if len(token) >= 3):
                    add_category(category)
            result.add(clean)

    return sorted(result)


def forbidden_hits_in_text(text: str, profile: Optional[dict]) -> list[str]:
    """Находит запретные продукты в любом названии, рецепте, ингредиентах или списке покупок."""
    blocked_tokens = parse_blocked_products(profile)
    if not blocked_tokens:
        return []

    normalized_text = normalize_food_text(text)
    hits: set[str] = set()
    for token in blocked_tokens:
        normalized_token = normalize_food_text(token)
        if len(normalized_token) >= 3 and normalized_token in normalized_text:
            hits.add(token)
    return sorted(hits)


def _safe_item_name(item_name: str, profile: Optional[dict]) -> bool:
    return len(forbidden_hits_in_text(item_name, profile)) == 0


def _item_full_text(item: dict) -> str:
    pieces = []
    for key in ("name", "title", "meal_type", "category"):
        if item.get(key):
            pieces.append(str(item.get(key)))
    for key in ("tags", "ingredients", "steps"):
        value = item.get(key)
        if isinstance(value, list):
            pieces.extend(str(x) for x in value)
    return " ".join(pieces)


def _filter_safe_items(options: list[dict], profile: Optional[dict]) -> list[dict]:
    return [item for item in options if _safe_item_name(_item_full_text(item), profile)]


def _pick(options: list[dict], profile: Optional[dict]) -> Optional[dict]:
    allowed = _filter_safe_items(options, profile)
    return random.choice(allowed) if allowed else None


# =========================
# MAIN
# =========================
async def main() -> None:
    init_db()
    for user_id in get_users_with_enabled_reminders():
        start_reminder_task(user_id)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
