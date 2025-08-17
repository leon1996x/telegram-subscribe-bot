import os
import logging

from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command, CommandStart
from aiogram.enums import ParseMode

import gspread
from google.oauth2.service_account import Credentials

# ---------- ЛОГИ ----------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ---------- ENV ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7145469393"))
GSHEET_ID = os.getenv("GSHEET_ID")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в переменных окружения")
if not GSHEET_ID:
    raise RuntimeError("GSHEET_ID не задан в переменных окружения")

# ---------- BOT / DP / APP ----------
bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()

# ---------- GOOGLE SHEETS ----------
def _get_creds_path() -> str:
    # Render Secret Files доступны тут
    path1 = "/etc/secrets/GSPREAD_CREDENTIALS.json"
    # и/или в корне репозитория (если добавил как Secret File без /etc/secrets)
    path2 = "GSPREAD_CREDENTIALS.json"
    if os.path.exists(path1):
        return path1
    if os.path.exists(path2):
        return path2
    raise FileNotFoundError(
        "Не найден GSPREAD_CREDENTIALS.json ни в /etc/secrets, ни в корне проекта"
    )

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
CREDENTIALS_FILE = _get_creds_path()
creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
gc = gspread.authorize(creds)
sh = gc.open_by_key(GSHEET_ID)
worksheet = sh.sheet1  # работаем с первой страницей

# Гарантируем заголовки, если лист пустой
headers = ["id", "name", "file_url", "subscription_type", "subscription_end",
           "post_id", "post_text", "post_photo"]
vals = worksheet.get_all_values()
if not vals:
    worksheet.append_row(headers)

# ---------- FSM ----------
class PostForm(StatesGroup):
    waiting_for_text = State()
    waiting_for_photo = State()

# ---------- ХЕЛПЕРЫ ----------
def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить пост", callback_data="add_post")],
        [InlineKeyboardButton(text="🗑 Удалить пост", callback_data="del_post_menu")]
    ])

# ---------- ХЕНДЛЕРЫ ----------
@dp.message(CommandStart())
async def on_start(message: Message):
    # Показываем все посты
    records = worksheet.get_all_records()
    posts = [r for r in records if str(r.get("post_id", "")).strip() != ""]
    if not posts:
        await message.answer("📭 Постов пока нет")
        return
    for post in posts:
        text = post.get("post_text", "") or ""
        photo_file_id = post.get("post_photo", "") or ""
        if photo_file_id:
            await message.answer_photo(photo_file_id, caption=text)
        else:
            await message.answer(text)

@dp.message(Command("admin"))
async def on_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("⛔ У тебя нет доступа")
    await message.answer("⚙️ Панель админа", reply_markup=admin_kb())

# --- Добавление поста ---
@dp.callback_query(F.data == "add_post")
async def cb_add_post(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer("⛔ Нет доступа", show_alert=True)
    await state.set_state(PostForm.waiting_for_text)
    await callback.message.answer("✍️ Введи текст поста:")
    await callback.answer()

@dp.message(PostForm.waiting_for_text)
async def get_post_text(message: Message, state: FSMContext):
    await state.update_data(text=message.text or "")
    await state.set_state(PostForm.waiting_for_photo)
    await message.answer("📷 Пришли фото (или напиши: нет)")

@dp.message(PostForm.waiting_for_photo)
async def get_post_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    text = data.get("text", "")
    photo_file_id = ""

    if message.photo:
        photo_file_id = message.photo[-1].file_id
    elif (message.text or "").strip().lower() == "нет":
        photo_file_id = ""
    else:
        return await message.answer("Отправь фото или напиши 'нет'")

    # Новый post_id — просто счётчик по существующим постам
    records = worksheet.get_all_records()
    existing = [r for r in records if str(r.get("post_id", "")).strip() != ""]
    post_id = len(existing) + 1

    # Пишем строку строго под твои колонки
    worksheet.append_row([
        "", "", "", "", "",              # id, name, file_url, subscription_type, subscription_end
        str(post_id),                    # post_id
        text,                            # post_text
        photo_file_id                    # post_photo (file_id, не URL!)
    ])

    await state.clear()
    await message.answer("✅ Пост добавлен!")

# --- Удаление постов ---
@dp.callback_query(F.data == "del_post_menu")
async def cb_del_menu(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer("⛔ Нет доступа", show_alert=True)

    records = worksheet.get_all_records()
    posts = [r for r in records if str(r.get("post_id", "")).strip() != ""]
    if not posts:
        return await callback.message.answer("📭 Нет постов для удаления")

    # Кнопки выбора поста по id
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"❌ Удалить #{p['post_id']}", callback_data=f"del_{p['post_id']}")]
        for p in posts
    ])
    await callback.message.answer("Выбери пост для удаления:", reply_markup=kb)
    await callback.answer()

@dp.callback_query(F.data.startswith("del_"))
async def cb_delete_post(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer("⛔ Нет доступа", show_alert=True)

    post_id = callback.data.split("_", 1)[1]
    records = worksheet.get_all_records()
    # строка данных начинается со второй (первая — заголовки)
    for idx, row in enumerate(records, start=2):
        if str(row.get("post_id")) == str(post_id):
            worksheet.delete_rows(idx)
            await callback.message.answer(f"🗑 Пост #{post_id} удалён")
            await callback.answer()
            return
    await callback.message.answer("⚠️ Пост не найден")
    await callback.answer()

# ---------- WEBHOOK ----------
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
RENDER_HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
WEBHOOK_URL = f"https://{RENDER_HOSTNAME}{WEBHOOK_PATH}" if RENDER_HOSTNAME else None

@app.on_event("startup")
async def on_startup():
    if not WEBHOOK_URL:
        log.error("Не найден RENDER_EXTERNAL_HOSTNAME — webhook не установлен!")
        return
    # на всякий случай сносим старый вебхук и ставим новый
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(WEBHOOK_URL)
    me = await bot.get_me()
    log.info(f"Webhook установлен: {WEBHOOK_URL} | бот @{me.username} ({me.id})")

@app.on_event("shutdown")
async def on_shutdown():
    try:
        await bot.delete_webhook()
    finally:
        await bot.session.close()

@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    update = bot.session.json_loads(request.body if isinstance(request.body, str) else None)  # заглушка, aiogram сам парсит
    # корректный способ:
    from aiogram.types import Update
    upd = Update(**data)
    await dp.feed_update(bot, upd)
    return {"ok": True}

@app.get("/")
async def root():
    return {"status": "ok", "webhook": WEBHOOK_URL}
