import os
import logging
from typing import List, Optional

from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
import gspread
from google.oauth2.service_account import Credentials

# --- НАСТРОЙКИ ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7145469393"))
GSHEET_ID = "1YkIDFyCc561vPVNnKWsjFtFmHQeXl5vlH_0Rc7wXihE"  # ID вашей таблицы
CREDENTIALS_FILE = "GSPREAD_CREDENTIALS.json"

logging.basicConfig(level=logging.INFO)

# --- Инициализация ---
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

app = FastAPI()

# --- Google Sheets ---
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
credentials = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
gc = gspread.authorize(credentials)
sh = gc.open_by_key(GSHEET_ID)
worksheet = sh.worksheet("BotData")  # Работаем с листом BotData

# --- FSM (Состояния для добавления поста) ---
class PostStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_photo = State()

# --- Команда /start (показывает последний пост) ---
@dp.message_handler(commands=['start'])
async def start_handler(message: Message):
    last_post = worksheet.get_all_records()[-1]  # Берём последний пост
    post_text = last_post["post_text"]
    post_photo = last_post["post_photo"]

    if post_photo:  # Если есть фото
        await bot.send_photo(
            chat_id=message.chat.id,
            photo=post_photo,
            caption=post_text,
            reply_markup=create_delete_button(last_post["post_id"]) if str(message.from_user.id) == ADMIN_ID else None
        )
    else:  # Если только текст
        await message.answer(
            post_text,
            reply_markup=create_delete_button(last_post["post_id"]) if str(message.from_user.id) == ADMIN_ID else None
        )

# --- Команда /admin (только для админа) ---
@dp.message_handler(commands=['admin'])
async def admin_panel(message: Message):
    if str(message.from_user.id) != ADMIN_ID:
        await message.answer("🚫 Доступ запрещён!")
        return

    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("➕ Добавить пост", callback_data="add_post"))
    await message.answer("Админ-панель:", reply_markup=keyboard)

# --- Обработка кнопки "Добавить пост" ---
@dp.callback_query_handler(lambda c: c.data == "add_post")
async def add_post_callback(callback_query: types.CallbackQuery):
    await bot.answer_callback_query(callback_query.id)
    await bot.send_message(callback_query.from_user.id, "📝 Введите текст поста:")
    await PostStates.waiting_for_text.set()

# --- Ожидание текста поста ---
@dp.message_handler(state=PostStates.waiting_for_text)
async def process_post_text(message: Message, state: FSMContext):
    await state.update_data(post_text=message.text)
    await message.answer("📤 Теперь отправьте фото (или нажмите /skip, если без фото):")
    await PostStates.waiting_for_photo.set()

# --- Ожидание фото (или пропуск) ---
@dp.message_handler(content_types=['photo'], state=PostStates.waiting_for_photo)
async def process_post_photo(message: Message, state: FSMContext):
    photo_id = message.photo[-1].file_id  # Берём самое высокое качество
    data = await state.get_data()
    await save_post_to_sheets(data["post_text"], photo_id)
    await message.answer("✅ Пост добавлен!")
    await state.finish()

@dp.message_handler(commands=['skip'], state=PostStates.waiting_for_photo)
async def skip_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    await save_post_to_sheets(data["post_text"], "")
    await message.answer("✅ Пост добавлен (без фото)!")
    await state.finish()

# --- Сохранение поста в Google Sheets ---
async def save_post_to_sheets(post_text: str, post_photo: str):
    last_id = len(worksheet.get_all_records()) + 1
    worksheet.append_row([last_id, "", "", "", "", last_id, post_text, post_photo])

# --- Кнопка "Удалить пост" (только для админа) ---
def create_delete_button(post_id: int) -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🗑️ Удалить пост", callback_data=f"delete_post_{post_id}"))
    return keyboard

# --- Обработка удаления поста ---
@dp.callback_query_handler(lambda c: c.data.startswith('delete_post_'))
async def delete_post(callback_query: types.CallbackQuery):
    post_id = int(callback_query.data.split('_')[-1])
    records = worksheet.get_all_records()
    for idx, row in enumerate(records, start=2):  # Начинаем с 2 строки (1 - заголовки)
        if row["post_id"] == post_id:
            worksheet.delete_rows(idx)
            break
    await bot.answer_callback_query(callback_query.id, "🗑️ Пост удалён!")
    await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)

# --- Webhook (оставляем как было) ---
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

@app.get("/")
async def root():
    return {"status": "ok", "webhook": WEBHOOK_URL}
