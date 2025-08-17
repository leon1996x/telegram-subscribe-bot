import os
import logging
from typing import List, Optional
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import gspread
from google.oauth2.service_account import Credentials

# Настройка логгирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Конфигурация
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7145469393"))
GSHEET_ID = os.getenv("GSHEET_ID")

# Проверка переменных
if not all([BOT_TOKEN, GSHEET_ID]):
    missing = [name for name, val in [("BOT_TOKEN", BOT_TOKEN), ("GSHEET_ID", GSHEET_ID)] if not val]
    raise RuntimeError(f"Не заданы: {', '.join(missing)}")

# Инициализация бота
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()

# Подключение к Google Sheets
try:
    creds_path = '/etc/secrets/GSPREAD_CREDENTIALS.json'
    creds = Credentials.from_service_account_file(creds_path, scopes=[
        "https://www.googleapis.com/auth/spreadsheets"
    ])
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(GSHEET_ID)
    ws = sh.sheet1
    logger.info("Успешное подключение к Google Sheets!")
except Exception as e:
    logger.error(f"Ошибка Google Sheets: {e}")
    ws = None

# Клавиатуры
def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить пост", callback_data="add_post")],
        [InlineKeyboardButton(text="📋 Список постов", callback_data="list_posts")]
    ])

def delete_kb(post_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_{post_id}")]
    ])

# Состояния FSM
class PostStates(StatesGroup):
    waiting_text = State()
    waiting_photo = State()

# Обработчики команд
@dp.message(Command("start"))
async def cmd_start(message: Message):
    try:
        if not ws:
            await message.answer("⚠️ База данных временно недоступна")
            return
            
        records = ws.get_all_records()
        posts = [p for p in records if str(p.get("post_id", "")).strip()]
        
        if not posts:
            await message.answer("📭 Пока нет опубликованных постов")
            return
            
        for post in posts:
            text = post.get("post_text", "Без текста")
            photo = post.get("post_photo", "").strip()
            
            try:
                if photo:
                    await message.answer_photo(
                        photo=photo,
                        caption=text
                    )
                else:
                    await message.answer(text)
            except Exception as e:
                logger.error(f"Ошибка отправки поста {post.get('post_id')}: {e}")
                await message.answer(f"📄 {text[:300]}" + ("..." if len(text) > 300 else ""))
                
    except gspread.exceptions.APIError as e:
        logger.error(f"Ошибка Google Sheets: {e}")
        await message.answer("⚠️ Ошибка загрузки данных. Попробуйте позже")
    except Exception as e:
        logger.error(f"Неожиданная ошибка в /start: {e}")
        await message.answer("❌ Произошла ошибка при загрузке постов")

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("🚫 Доступ запрещен")
        return
    await message.answer("👨‍💻 Админ-панель:", reply_markup=admin_kb())

# Обработчики кнопок
@dp.callback_query(F.data == "add_post")
async def add_post_callback(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("🚫 Нет доступа")
        return
    
    await state.set_state(PostStates.waiting_text)
    await callback.message.answer("📝 Введите текст поста:")
    await callback.answer()

@dp.callback_query(F.data == "list_posts")
async def list_posts_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("🚫 Нет доступа")
        return
        
    posts = ws.get_all_records() if ws else []
    posts = [p for p in posts if str(p.get("post_id", "")).strip()]
    
    if not posts:
        await callback.message.answer("📭 Нет постов для отображения")
        return
        
    for post in posts:
        text = post.get("post_text", "Без текста")
        photo = post.get("post_photo", "").strip()
        post_id = post.get("post_id", "N/A")
        
        try:
            if photo:
                await callback.message.answer_photo(
                    photo,
                    caption=f"{text}\n\nID: {post_id}",
                    reply_markup=delete_kb(post_id)
                )
            else:
                await callback.message.answer(
                    f"{text}\n\nID: {post_id}",
                    reply_markup=delete_kb(post_id)
                )
        except Exception as e:
            logger.error(f"Ошибка отправки поста {post_id}: {e}")
            await callback.message.answer(
                f"📄 {text[:300]}...\n\nID: {post_id}",
                reply_markup=delete_kb(post_id))
    await callback.answer()

@dp.callback_query(F.data.startswith("delete_"))
async def delete_post_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("🚫 Нет доступа")
        return
        
    post_id = callback.data.split("_")[1]
    try:
        if ws:
            records = ws.get_all_values()
            for idx, row in enumerate(records[1:], start=2):
                if str(row[5]) == str(post_id):
                    ws.delete_rows(idx)
                    await callback.message.delete()
                    await callback.answer("✅ Пост удален")
                    return
        await callback.answer("❌ Пост не найден")
    except Exception as e:
        logger.error(f"Ошибка удаления: {e}")
        await callback.answer("⚠️ Ошибка удаления")

# Обработчики состояний
@dp.message(PostStates.waiting_text)
async def process_post_text(message: Message, state: FSMContext):
    await state.update_data(text=message.text)
    await state.set_state(PostStates.waiting_photo)
    await message.answer("📷 Отправьте фото или напишите 'пропустить'")

@dp.message(PostStates.waiting_photo)
async def process_post_photo(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        text = data.get("text", "")
        
        if message.photo:
            file = await bot.get_file(message.photo[-1].file_id)
            photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
        elif message.text and message.text.lower() == "пропустить":
            photo_url = ""
        else:
            await message.answer("❌ Отправьте фото или напишите 'пропустить'")
            return

        if ws:
            post_id = max([int(p.get("post_id", 0)) for p in ws.get_all_records()] + [0]) + 1
            ws.append_row(["", "", "", "", "", post_id, text, photo_url])
            await message.answer(f"✅ Пост добавлен (ID: {post_id})")
            
            # Рассылка всем пользователям
            records = ws.get_all_records()
            user_ids = {str(r["id"]) for r in records if str(r.get("id", "")).isdigit()}
            
            for user_id in user_ids:
                try:
                    if photo_url:
                        await bot.send_photo(chat_id=user_id, photo=photo_url, caption=text)
                    else:
                        await bot.send_message(chat_id=user_id, text=text)
                except Exception as e:
                    logger.error(f"Не удалось отправить пост {post_id} пользователю {user_id}: {e}")
        else:
            await message.answer("⚠️ База данных недоступна, пост не сохранен")
            
    except Exception as e:
        logger.error(f"Ошибка добавления поста: {e}")
        await message.answer("❌ Ошибка при добавлении поста")
    finally:
        await state.clear()

# Webhook
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}{WEBHOOK_PATH}"

@app.on_event("startup")
async def startup():
    if os.getenv("RENDER"):
        await bot.set_webhook(WEBHOOK_URL)
        logger.info(f"Webhook установлен: {WEBHOOK_URL}")

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    data = await request.json()
    update = types.Update(**data)
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.get("/")
async def health_check():
    return {"status": "ok", "sheets": bool(ws)}
