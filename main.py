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

# Регистрация пользователя
async def register_user(user: types.User):
    if not ws:
        return
        
    try:
        user_id = str(user.id)
        if not user_id.isdigit():
            logger.error(f"Invalid user_id: {user_id}")
            return

        records = ws.get_all_records()
        
        # Проверяем, есть ли уже пользователь
        if not any(str(r.get("id", "")).strip() == user_id for r in records):
            ws.append_row([
                user_id,
                user.username or "",
                "",  # file_url
                "",  # subscription_type
                "",  # subscription_end
                "",  # post_id
                "",  # post_text
                ""   # post_photo
            ])
            logger.info(f"Зарегистрирован новый пользователь: {user_id}")
    except Exception as e:
        logger.error(f"Ошибка регистрации пользователя: {e}")

# Обработчики команд
@dp.message(Command("start"))
async def cmd_start(message: Message):
    try:
        await register_user(message.from_user)
        records = ws.get_all_records() if ws else []
        posts = [p for p in records if str(p.get("post_id", "")).strip()]
        
        if not posts:
            await message.answer("📭 Пока нет опубликованных постов")
            return
            
        for post in posts:
            text = post.get("post_text", "Без текста")
            photo_id = post.get("post_photo", "").strip()
            
            try:
                if photo_id:
                    await message.answer_photo(
                        photo=photo_id,
                        caption=text,
                        reply_markup=delete_kb(post["post_id"]) if message.from_user.id == ADMIN_ID else None
                    )
                else:
                    await message.answer(
                        text=text,
                        reply_markup=delete_kb(post["post_id"]) if message.from_user.id == ADMIN_ID else None
                    )
            except Exception as e:
                logger.error(f"Ошибка отправки поста {post.get('post_id')}: {e}")
                await message.answer(f"📄 {text[:300]}" + ("..." if len(text) > 300 else ""))
                
    except Exception as e:
        logger.error(f"Ошибка в /start: {e}", exc_info=True)
        await message.answer("⚠️ Ошибка загрузки постов")

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
        photo_id = post.get("post_photo", "").strip()
        post_id = post.get("post_id", "N/A")
        
        try:
            if photo_id:
                await callback.message.answer_photo(
                    photo_id,
                    caption=f"{text}\n\nID: {post_id}",
                    reply_markup=delete_kb(post_id)
            else:
                await callback.message.answer(
                    f"{text}\n\nID: {post_id}",
                    reply_markup=delete_kb(post_id))
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
            photo_id = message.photo[-1].file_id
        elif message.text and message.text.lower() == "пропустить":
            photo_id = ""
        else:
            await message.answer("❌ Отправьте фото или напишите 'пропустить'")
            return

        if ws:
            records = ws.get_all_records()
            
            # Безопасное вычисление post_id
            post_ids = []
            for p in records:
                try:
                    post_id_str = str(p.get("post_id", "")).strip()
                    if post_id_str:
                        post_ids.append(int(post_id_str))
                except (ValueError, AttributeError):
                    continue
            post_id = max(post_ids + [0]) + 1
            
            user_ids = {str(r["id"]) for r in records if str(r.get("id", "")).strip()}
            ws.append_row(["", "", "", "", "", post_id, text, photo_id])
            
            success = 0
            for user_id in user_ids:
                try:
                    if photo_id:
                        await bot.send_photo(user_id, photo=photo_id, caption=text)
                    else:
                        await bot.send_message(user_id, text=text)
                    success += 1
                except Exception as e:
                    logger.error(f"Не удалось отправить пост пользователю {user_id}: {e}")

            await message.answer(f"✅ Пост добавлен (ID: {post_id})\nОтправлено: {success}/{len(user_ids)}")
        else:
            await message.answer("⚠️ База данных недоступна")
            
    except Exception as e:
        logger.error(f"Ошибка добавления поста: {e}", exc_info=True)
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
