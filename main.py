import os
import gspread
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# === Конфиг ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))  # твой Telegram ID

# Google Sheets
gc = gspread.service_account(filename="creds.json")
sh = gc.open("TelegramBotDB")   # название таблицы
try:
    worksheet = sh.worksheet("BotData")
except gspread.exceptions.WorksheetNotFound:
    worksheet = sh.add_worksheet(title="BotData", rows="100", cols="10")
    worksheet.append_row(["id", "name", "file_url", "subscription_type", "subscription_end",
                          "post_id", "post_text", "post_photo"])

# === Bot + Dispatcher ===
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

app = FastAPI()

# === FSM для постов ===
class PostForm(StatesGroup):
    waiting_for_text = State()
    waiting_for_photo = State()

# === /start ===
@router.message(F.text == "/start")
async def cmd_start(message: Message):
    records = worksheet.get_all_records()
    posts = [r for r in records if r.get("post_id")]
    if not posts:
        await message.answer("📭 Постов пока нет")
        return
    for post in posts:
        text = post.get("post_text", "")
        photo = post.get("post_photo", "")
        if photo:
            await message.answer_photo(photo, caption=text)
        else:
            await message.answer(text)

# === /admin ===
@router.message(F.text == "/admin")
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("⛔ У тебя нет доступа")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить пост", callback_data="add_post")],
        [InlineKeyboardButton(text="🗑 Удалить пост", callback_data="del_post_menu")],
    ])
    await message.answer("⚙️ Панель админа", reply_markup=kb)

# === Добавление поста ===
@router.callback_query(F.data == "add_post")
async def add_post_handler(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer("⛔ Нет доступа")
    await state.set_state(PostForm.waiting_for_text)
    await callback.message.answer("✍️ Отправь текст поста")
    await callback.answer()

@router.message(PostForm.waiting_for_text)
async def process_post_text(message: Message, state: FSMContext):
    await state.update_data(post_text=message.text)
    await state.set_state(PostForm.waiting_for_photo)
    await message.answer("📎 Отправь фото (или напиши 'нет')")

@router.message(PostForm.waiting_for_photo)
async def process_post_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    text = data.get("post_text", "")
    photo = None
    if message.photo:
        photo = message.photo[-1].file_id
    elif message.text and message.text.lower() == "нет":
        photo = ""
    else:
        return await message.answer("Отправь фото или напиши 'нет'")

    records = worksheet.get_all_records()
    new_id = len(records) + 1
    worksheet.append_row(["", "", "", "", "", str(new_id), text, photo])

    await state.clear()
    await message.answer("✅ Пост добавлен!")

# === Удаление постов ===
@router.callback_query(F.data == "del_post_menu")
async def del_post_menu(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer("⛔ Нет доступа")
    records = worksheet.get_all_records()
    posts = [r for r in records if r.get("post_id")]
    if not posts:
        return await callback.message.answer("📭 Нет постов для удаления")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"❌ {p['post_text'][:20]}", callback_data=f"del_{p['post_id']}")]
        for p in posts
    ])
    await callback.message.answer("Выбери пост для удаления:", reply_markup=kb)
    await callback.answer()

@router.callback_query(F.data.startswith("del_"))
async def del_post(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return await callback.answer("⛔ Нет доступа")
    post_id = callback.data.split("_")[1]
    cell = worksheet.find(post_id)
    if cell:
        worksheet.delete_rows(cell.row)
        await callback.message.answer("🗑 Пост удалён")
    else:
        await callback.message.answer("⚠️ Пост не найден")
    await callback.answer()

# === Webhook ===
@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = dp.updates_factory.create(data)
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.get("/")
async def root():
    return {"status": "ok"}
