import os
import logging

from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ============ CONFIG ============
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7145469393"))  # твой Telegram ID

# Google Sheets
SHEET_NAME = "BotData"
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("creds.json", scope)
client = gspread.authorize(creds)
sheet = client.open(SHEET_NAME).sheet1

# ============ BOT ============
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

# ============ FSM ============
class AdminStates(StatesGroup):
    waiting_text = State()
    waiting_photo = State()

# ============ HELPERS ============
def add_post_to_sheet(text: str, photo: str = None):
    sheet.append_row([ "", "", "", "", "", "", text, photo ])

def get_posts():
    return sheet.get_all_records()

def delete_post(row_index: int):
    sheet.delete_rows(row_index)

# ============ HANDLERS ============

@router.message(F.text == "/start")
async def cmd_start(message: Message):
    sheet.append_row([str(message.from_user.id), message.text])
    await message.answer("✅ Данные записаны в Google Sheets!")

@router.message(F.text == "/admin")
async def cmd_admin(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("⛔ У тебя нет доступа")

    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Добавить пост", callback_data="add_post")
    for i, post in enumerate(get_posts(), start=2):  # начиная со 2 строки
        kb.button(text=f"🗑 Удалить {post.get('post_text', '')[:10]}", callback_data=f"del_{i}")

    kb.adjust(1)
    await message.answer("📋 Админ панель", reply_markup=kb.as_markup())

@router.callback_query(F.data == "add_post")
async def cb_add_post(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("✍️ Пришли текст поста")
    await state.set_state(AdminStates.waiting_text)

@router.message(AdminStates.waiting_text)
async def admin_text(message: Message, state: FSMContext):
    await state.update_data(post_text=message.text)
    await message.answer("📸 Отправь фото (или напиши 'нет')")
    await state.set_state(AdminStates.waiting_photo)

@router.message(AdminStates.waiting_photo)
async def admin_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    text = data["post_text"]
    photo = None

    if message.photo:
        photo = message.photo[-1].file_id
    elif message.text.lower() == "нет":
        photo = None

    add_post_to_sheet(text, photo)
    await message.answer("✅ Пост добавлен!")
    await state.clear()

@router.callback_query(F.data.startswith("del_"))
async def cb_delete(callback: CallbackQuery):
    row_index = int(callback.data.split("_")[1])
    delete_post(row_index)
    await callback.message.answer("🗑 Пост удалён!")

# ============ FASTAPI ============
app = FastAPI()

@app.on_event("startup")
async def on_startup():
    webhook_url = f"{os.getenv('RENDER_EXTERNAL_URL')}/webhook/{BOT_TOKEN}"
    await bot.set_webhook(webhook_url)

@app.post("/webhook/{token}")
async def telegram_webhook(token: str, request: Request):
    if token != BOT_TOKEN:
        return {"status": "forbidden"}
    update = await request.json()
    await dp.feed_update(bot, update)
    return {"status": "ok"}

@app.get("/")
async def root():
    return {"status": "ok"}
