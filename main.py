import os
import logging
import asyncio

from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
import gspread
from google.oauth2.service_account import Credentials

# --- НАСТРОЙКИ ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7145469393"))
GSHEET_ID = os.getenv("GSHEET_ID")
CREDENTIALS_FILE = "GSPREAD_CREDENTIALS.json"

logging.basicConfig(level=logging.INFO)

# --- Инициализация ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()

# --- Google Sheets ---
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
credentials = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
gc = gspread.authorize(credentials)
sh = gc.open_by_key(GSHEET_ID)
worksheet = sh.worksheet("BotData")  # используем твой лист BotData

# --- FSM для добавления поста ---
class PostForm(StatesGroup):
    waiting_for_text = State()
    waiting_for_photo = State()

# --- Хендлер /start (показывает все посты) ---
@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    records = worksheet.get_all_records()
    posts = [r for r in records if r.get("post_id")]
    if not posts:
        await message.answer("Постов пока нет 🚫")
        return

    for post in posts:
        text = post.get("post_text", "")
        photo = post.get("post_photo", "")
        if photo:
            await message.answer_photo(photo, caption=text)
        else:
            await message.answer(text)

# --- Хендлер /admin ---
@dp.message(F.text == "/admin")
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("⛔ У тебя нет доступа")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить пост", callback_data="add_post")],
        [InlineKeyboardButton(text="🗑 Удалить пост", callback_data="del_post_menu")],
    ])
    await message.answer("⚙️ Панель админа", reply_markup=kb)

# --- Добавление поста ---
@dp.callback_query(F.data == "add_post")
async def process_add_post(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer("⛔ Нет доступа", show_alert=True)
    await state.set_state(PostForm.waiting_for_text)
    await callback.message.answer("✍️ Введи текст поста:")

@dp.message(PostForm.waiting_for_text)
async def process_post_text(message: Message, state: FSMContext):
    await state.update_data(text=message.text)
    await state.set_state(PostForm.waiting_for_photo)
    await message.answer("📷 Теперь пришли фото (или напиши 'нет'): ")

@dp.message(PostForm.waiting_for_photo)
async def process_post_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    text = data["text"]
    photo_url = ""

    if message.photo:
        file = await bot.get_file(message.photo[-1].file_id)
        photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
    elif message.text.lower() == "нет":
        photo_url = ""

    # Генерируем post_id = текущее количество строк + 1
    records = worksheet.get_all_records()
    post_id = len([r for r in records if r.get("post_id")]) + 1

    worksheet.append_row(["", "", "", "", "", post_id, text, photo_url])
    await message.answer("✅ Пост добавлен!")
    await state.clear()

# --- Удаление постов ---
@dp.callback_query(F.data == "del_post_menu")
async def process_del_menu(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer("⛔ Нет доступа", show_alert=True)

    records = worksheet.get_all_records()
    posts = [r for r in records if r.get("post_id")]

    if not posts:
        return await callback.message.answer("🚫 Нет постов для удаления")

    for post in posts:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Удалить", callback_data=f"del_{post['post_id']}")]
        ])
        text = post.get("post_text", "")
        photo = post.get("post_photo", "")
        if photo:
            await callback.message.answer_photo(photo, caption=text, reply_markup=kb)
        else:
            await callback.message.answer(text, reply_markup=kb)

@dp.callback_query(F.data.startswith("del_"))
async def process_delete_post(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer("⛔ Нет доступа", show_alert=True)

    post_id = callback.data.split("_")[1]
    records = worksheet.get_all_records()
    for i, row in enumerate(records, start=2):  # с 2-й строки, т.к. 1-я — заголовки
        if str(row.get("post_id")) == post_id:
            worksheet.delete_rows(i)
            await callback.message.answer(f"🗑 Пост {post_id} удалён")
            return

    await callback.message.answer("⚠️ Пост не найден!")

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

@app.get("/")
async def root():
    return {"status": "ok", "webhook": WEBHOOK_URL}
