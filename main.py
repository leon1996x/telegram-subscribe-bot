import os
import asyncio
import logging
import gspread

from fastapi import FastAPI
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from google.oauth2.service_account import Credentials

# --- ЛОГИ ---
logging.basicConfig(level=logging.INFO)

# --- НАСТРОЙКИ ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7145469393"))
GSHEET_ID = os.getenv("GSHEET_ID")

if not BOT_TOKEN:
    raise ValueError("❌ Не найден BOT_TOKEN в переменных окружения")
if not GSHEET_ID:
    raise ValueError("❌ Не найден GSHEET_ID в переменных окружения")

# --- Telegram bot ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Google Sheets ---
CREDENTIALS_FILE = "/etc/secrets/GSPREAD_CREDENTIALS.json"
creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE,
    scopes=["https://www.googleapis.com/auth/spreadsheets"]
)
gc = gspread.authorize(creds)
worksheet = gc.open_by_key(GSHEET_ID).sheet1

# --- FastAPI ---
app = FastAPI()

@app.get("/")
async def root():
    return {"status": "ok", "message": "Бот работает 🚀"}

# --- КНОПКИ ---
def admin_keyboard():
    kb = [
        [KeyboardButton(text="📋 Посмотреть данные")],
        [KeyboardButton(text="➕ Добавить запись")],
        [KeyboardButton(text="❌ Удалить запись")],
        [KeyboardButton(text="🚪 Выйти")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- КОМАНДА /start ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Привет 👋 Это бот с Google Sheets!")

# --- КОМАНДА /admin ---
@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer("🔑 Админ-панель:", reply_markup=admin_keyboard())
    else:
        await message.answer("⛔ У вас нет доступа!")

# --- ОБРАБОТКА КНОПОК ---
@dp.message(F.text.in_(["📋 Посмотреть данные", "➕ Добавить запись", "❌ Удалить запись", "🚪 Выйти"]))
async def handle_admin_buttons(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет доступа!")
        return

    if message.text == "📋 Посмотреть данные":
        rows = worksheet.get_all_values()
        text = "\n".join([", ".join(row) for row in rows]) if rows else "📂 Таблица пуста"
        await message.answer(f"Данные из таблицы:\n\n{text}")

    elif message.text == "➕ Добавить запись":
        worksheet.append_row(["Новая запись"])
        await message.answer("✅ Запись добавлена!")

    elif message.text == "❌ Удалить запись":
        rows = worksheet.get_all_values()
        if len(rows) > 1:
            worksheet.delete_rows(len(rows))
            await message.answer("❌ Последняя запись удалена")
        else:
            await message.answer("⚠️ Удалять нечего")

    elif message.text == "🚪 Выйти":
        await message.answer("Вы вышли из админки.", reply_markup=ReplyKeyboardRemove())

# --- Фоновый запуск бота ---
@app.on_event("startup")
async def on_startup():
    loop = asyncio.get_event_loop()
    loop.create_task(dp.start_polling(bot))

