import os
import logging
import asyncio

from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types
from aiogram.types import Message
import gspread
from google.oauth2.service_account import Credentials

# --- НАСТРОЙКИ ---
BOT_TOKEN = os.getenv("BOT_TOKEN")  # положи токен в переменные окружения Render
ADMIN_ID = int(os.getenv("ADMIN_ID", "7145469393"))
GSHEET_ID = os.getenv("GSHEET_ID")  # ID таблицы из ссылки
CREDENTIALS_FILE = "GSPREAD_CREDENTIALS.json"  # лежит в Secret Files на Render

logging.basicConfig(level=logging.INFO)

# --- Инициализация ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

app = FastAPI()

# --- Google Sheets ---
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
credentials = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
gc = gspread.authorize(credentials)
sh = gc.open_by_key(GSHEET_ID)
worksheet = sh.sheet1  # первая страница

# --- Хендлеры бота ---
@dp.message()
async def echo_handler(message: Message):
    text = f"Пользователь {message.from_user.id} написал: {message.text}"
    worksheet.append_row([str(message.from_user.id), message.text])  # лог в гугл
    await message.answer("✅ Данные записаны в Google Sheets!")


# --- Webhook ---
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
RENDER_HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
WEBHOOK_URL = f"https://{RENDER_HOSTNAME}{WEBHOOK_PATH}" if RENDER_HOSTNAME else None


@app.on_event("startup")
async def on_startup():
    if WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
        logging.info(f"Webhook установлен: {WEBHOOK_URL}")
    else:
        logging.error("Не найден RENDER_EXTERNAL_HOSTNAME — webhook не установлен!")


@app.on_event("shutdown")
async def on_shutdown():
    await bot.delete_webhook()
    await bot.session.close()


@app.post(WEBHOOK_PATH)
async def webhook_handler(request: Request):
    data = await request.json()
    update = types.Update(**data)
    await dp.feed_update(bot, update)
    return {"ok": True}


# --- Для теста на / (GET) ---
@app.get("/")
async def root():
    return {"status": "ok", "webhook": WEBHOOK_URL}

