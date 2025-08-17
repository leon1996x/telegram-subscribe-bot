import os
import logging
import json
from typing import Optional

from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import gspread
from google.oauth2.service_account import Credentials

# --- Конфигурация ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7145469393"))
GSHEET_ID = "1YkIDFyCc561vPVNnKWsjFtFmHQeXl5vlH_0Rc7wXihE"

# Получаем credentials из переменных окружения
GSPREAD_CREDENTIALS = os.getenv("GSPREAD_CREDENTIALS")
if not GSPREAD_CREDENTIALS:
    raise ValueError("Не заданы GSPREAD_CREDENTIALS в переменных окружения")

logging.basicConfig(level=logging.INFO)

# --- Инициализация ---
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

app = FastAPI()

# --- Google Sheets ---
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

try:
    # Создаем credentials из JSON строки
    credentials_dict = json.loads(GSPREAD_CREDENTIALS)
    credentials = Credentials.from_service_account_info(credentials_dict, scopes=SCOPES)
    gc = gspread.authorize(credentials)
    sh = gc.open_by_key(GSHEET_ID)
    worksheet = sh.worksheet("BotData")
except Exception as e:
    logging.error(f"Ошибка инициализации Google Sheets: {e}")
    raise

# --- FSM States ---
class PostStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_photo = State()

# --- Команда /start ---
@dp.message(Command("start"))
async def start_handler(message: Message):
    try:
        records = worksheet.get_all_records()
        if not records:
            await message.answer("Пока нет постов.")
            return
            
        last_post = records[-1]
        markup = None
        
        if str(message.from_user.id) == ADMIN_ID:
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🗑️ Удалить пост", callback_data=f"delete_{last_post['post_id']}")]
                ]
            )
        
        if last_post.get("post_photo"):
            await message.answer_photo(
                photo=last_post["post_photo"],
                caption=last_post["post_text"],
                reply_markup=markup
            )
        else:
            await message.answer(
                text=last_post["post_text"],
                reply_markup=markup
            )
    except Exception as e:
        logging.error(f"Ошибка в /start: {e}")
        await message.answer("Произошла ошибка")

# --- Команда /admin ---
@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if str(message.from_user.id) != ADMIN_ID:
        return
        
    await message.answer(
        "Админ-панель:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➕ Добавить пост", callback_data="add_post")]
            ]
        )
    )

# --- Добавление поста ---
@dp.callback_query(lambda c: c.data == "add_post")
async def add_post_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите текст поста:")
    await state.set_state(PostStates.waiting_for_text)
    await callback.answer()

@dp.message(PostStates.waiting_for_text)
async def process_post_text(message: Message, state: FSMContext):
    await state.update_data(post_text=message.text)
    await message.answer("Отправьте фото или нажмите /skip")
    await state.set_state(PostStates.waiting_for_photo)

@dp.message(PostStates.waiting_for_photo, Command("skip"))
async def skip_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    await save_post(data["post_text"], "")
    await message.answer("Пост добавлен без фото!")
    await state.clear()

@dp.message(PostStates.waiting_for_photo)
async def process_post_photo(message: Message, state: FSMContext):
    if not message.photo:
        await message.answer("Пожалуйста, отправьте фото или /skip")
        return
        
    data = await state.get_data()
    await save_post(data["post_text"], message.photo[-1].file_id)
    await message.answer("Пост с фото добавлен!")
    await state.clear()

async def save_post(text: str, photo: str):
    try:
        last_id = len(worksheet.get_all_records()) + 1
        worksheet.append_row([last_id, "", "", "", "", last_id, text, photo])
    except Exception as e:
        logging.error(f"Ошибка сохранения поста: {e}")

# --- Удаление поста ---
@dp.callback_query(lambda c: c.data.startswith("delete_"))
async def delete_post(callback: types.CallbackQuery):
    post_id = int(callback.data.split("_")[1])
    try:
        records = worksheet.get_all_records()
        for idx, row in enumerate(records, start=2):
            if row["post_id"] == post_id:
                worksheet.delete_rows(idx)
                break
        await callback.message.delete()
        await callback.answer("Пост удалён")
    except Exception as e:
        logging.error(f"Ошибка удаления поста: {e}")
        await callback.answer("Ошибка удаления")

# --- Webhook ---
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}{WEBHOOK_PATH}"

@app.on_event("startup")
async def startup():
    if os.getenv("RENDER"):
        await bot.set_webhook(WEBHOOK_URL)
        logging.info("Webhook установлен")

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    update = await request.json()
    await dp.feed_update(bot, types.Update(**update))
    return {"ok": True}

@app.get("/")
async def health_check():
    return {"status": "ok"}
