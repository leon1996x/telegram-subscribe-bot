import os
import logging
import json
import asyncio
from typing import Optional, List

from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
import gspread
from google.oauth2.service_account import Credentials

# Настройка логгирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# -------------------- Конфигурация --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7145469393"))
GSHEET_ID = os.getenv("GSHEET_ID")

# Проверка обязательных переменных
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан")
if not GSHEET_ID:
    raise RuntimeError("GSHEET_ID не задан")

# -------------------- Инициализация --------------------
app = FastAPI()
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher(storage=MemoryStorage())

# -------------------- Работа с Google Sheets --------------------
def init_google_sheets():
    """Инициализация подключения к Google Sheets"""
    try:
        # Вариант 1: Через переменную окружения (для Render)
        creds_json = os.getenv("GSPREAD_CREDENTIALS_JSON")
        if creds_json:
            creds_dict = json.loads(creds_json)
            credentials = Credentials.from_service_account_info(creds_dict)
            return gspread.authorize(credentials)
        
        # Вариант 2: Через файл (для локального тестирования)
        creds_file = "service_account.json"
        if os.path.exists(creds_file):
            credentials = Credentials.from_service_account_file(creds_file)
            return gspread.authorize(credentials)
            
        raise ValueError("Не найдены учетные данные Google Sheets")
    except Exception as e:
        logger.error(f"Ошибка инициализации Google Sheets: {e}")
        raise

try:
    gc = init_google_sheets()
    sh = gc.open_by_key(GSHEET_ID)
    ws = sh.sheet1
except Exception as e:
    logger.critical(f"Не удалось подключиться к Google Sheets: {e}")
    ws = None  # Режим без базы данных

# -------------------- Вспомогательные функции --------------------
def ensure_headers():
    """Проверяем наличие нужных заголовков в таблице"""
    if not ws:
        return
        
    try:
        headers = ws.row_values(1)
        required_headers = [
            "id", "name", "file_url", 
            "subscription_type", "subscription_end",
            "post_id", "post_text", "post_photo"
        ]
        
        if headers != required_headers:
            ws.clear()
            ws.append_row(required_headers)
    except Exception as e:
        logger.error(f"Ошибка проверки заголовков: {e}")

def get_all_posts() -> List[dict]:
    """Получаем все посты из таблицы"""
    if not ws:
        return []
        
    try:
        records = ws.get_all_records()
        return [r for r in records if str(r.get("post_id", "")).strip()]
    except Exception as e:
        logger.error(f"Ошибка получения постов: {e}")
        return []

def add_post(text: str, photo_url: str = "") -> Optional[int]:
    """Добавляем новый пост и возвращаем его ID"""
    if not ws:
        return None
        
    try:
        posts = get_all_posts()
        post_id = max([p.get("post_id", 0) for p in posts] + [0]) + 1
        ws.append_row(["", "", "", "", "", post_id, text, photo_url])
        return post_id
    except Exception as e:
        logger.error(f"Ошибка добавления поста: {e}")
        return None

def delete_post(post_id: int) -> bool:
    """Удаляем пост по ID"""
    if not ws:
        return False
        
    try:
        records = ws.get_all_values()
        for idx, row in enumerate(records[1:], start=2):  # Пропускаем заголовки
            if str(row[5]) == str(post_id):  # post_id в 6 колонке (индекс 5)
                ws.delete_rows(idx)
                return True
        return False
    except Exception as e:
        logger.error(f"Ошибка удаления поста: {e}")
        return False

# -------------------- Состояния FSM --------------------
class PostStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_photo = State()

# -------------------- Клавиатуры --------------------
def admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить пост", callback_data="add_post")],
        [InlineKeyboardButton(text="📋 Список постов", callback_data="list_posts")]
    ])

def delete_button(post_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_{post_id}")]
    ])

# -------------------- Обработчики команд --------------------
@dp.message(F.text == "/start")
async def start_command(message: Message):
    try:
        posts = get_all_posts()
        if not posts:
            await message.answer("Пока нет доступных постов.")
            return
            
        for post in posts:
            text = post.get("post_text", "")
            photo = post.get("post_photo", "")
            
            if photo:
                await message.answer_photo(
                    photo=photo,
                    caption=text
                )
            else:
                await message.answer(text)
    except Exception as e:
        logger.error(f"Ошибка в /start: {e}")
        await message.answer("Произошла ошибка при загрузке постов.")

@dp.message(F.text == "/admin")
async def admin_command(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("Доступ запрещен.")
        return
        
    await message.answer(
        "Админ-панель:",
        reply_markup=admin_keyboard()
    )

# -------------------- Обработчики FSM --------------------
@dp.callback_query(F.data == "add_post")
async def add_post_callback(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещен.")
        return
        
    await callback.message.answer("Введите текст поста:")
    await state.set_state(PostStates.waiting_for_text)
    await callback.answer()

@dp.message(PostStates.waiting_for_text)
async def process_post_text(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, введите текст.")
        return
        
    await state.update_data(text=message.text)
    await message.answer("Отправьте фото для поста или напишите 'пропустить'.")
    await state.set_state(PostStates.waiting_for_photo)

@dp.message(PostStates.waiting_for_photo)
async def process_post_photo(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        text = data.get("text", "")
        
        if not text:
            await message.answer("Ошибка: текст не найден.")
            await state.clear()
            return
            
        photo_url = ""
        
        if message.photo:
            file = await bot.get_file(message.photo[-1].file_id)
            photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
        elif message.text and message.text.lower() == "пропустить":
            pass
        else:
            await message.answer("Пожалуйста, отправьте фото или напишите 'пропустить'.")
            return
            
        # Сохраняем пост
        post_id = add_post(text, photo_url)
        if post_id:
            await message.answer(f"Пост успешно добавлен (ID: {post_id})")
            
            # Рассылаем пост (асинхронно)
            asyncio.create_task(broadcast_post(text, photo_url, post_id))
        else:
            await message.answer("Не удалось сохранить пост.")
        
    except Exception as e:
        logger.error(f"Ошибка добавления поста: {e}")
        await message.answer("Произошла ошибка при добавлении поста.")
    finally:
        await state.clear()

async def broadcast_post(text: str, photo_url: str, post_id: int):
    """Рассылаем пост всем пользователям"""
    try:
        # Отправляем админу с кнопкой удаления
        if photo_url:
            await bot.send_photo(
                chat_id=ADMIN_ID,
                photo=photo_url,
                caption=f"{text}\n\nID: {post_id}",
                reply_markup=delete_button(post_id)
            )
        else:
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=f"{text}\n\nID: {post_id}",
                reply_markup=delete_button(post_id)
            )
    except Exception as e:
        logger.error(f"Ошибка рассылки поста: {e}")

# -------------------- Обработчики кнопок --------------------
@dp.callback_query(F.data == "list_posts")
async def list_posts_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещен.")
        return
        
    posts = get_all_posts()
    if not posts:
        await callback.message.answer("Нет доступных постов.")
        return
        
    for post in posts:
        text = post.get("post_text", "")
        photo = post.get("post_photo", "")
        post_id = post.get("post_id", "")
        
        if photo:
            await callback.message.answer_photo(
                photo=photo,
                caption=f"{text}\n\nID: {post_id}",
                reply_markup=delete_button(post_id)
            )
        else:
            await callback.message.answer(
                text=f"{text}\n\nID: {post_id}",
                reply_markup=delete_button(post_id)
            )
    await callback.answer()

@dp.callback_query(F.data.startswith("delete_"))
async def delete_post_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещен.")
        return
        
    post_id = callback.data.split("_")[1]
    if not post_id.isdigit():
        await callback.answer("Неверный ID поста.")
        return
        
    if delete_post(int(post_id)):
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("Пост удален.")
    else:
        await callback.answer("Не удалось удалить пост.")

# -------------------- Webhook --------------------
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}{WEBHOOK_PATH}"

@app.on_event("startup")
async def on_startup():
    ensure_headers()
    if os.getenv("RENDER"):
        await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
        logger.info(f"Webhook установлен: {WEBHOOK_URL}")

@app.post(WEBHOOK_PATH)
async def webhook_handler(request: Request):
    try:
        data = await request.json()
        update = types.Update(**data)
        await dp.feed_update(bot, update)
        return {"ok": True}
    except Exception as e:
        logger.error(f"Ошибка обработки webhook: {e}")
        return {"ok": False}

@app.get("/")
async def health_check():
    return {"status": "ok", "bot": "running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
