import os
import logging
import re
from typing import List, Optional, Dict
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
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

def create_buttons_keyboard(buttons_data: str) -> Optional[InlineKeyboardMarkup]:
    """Создает клавиатуру из данных кнопок"""
    if not buttons_data or buttons_data == "нет":
        return None
    
    keyboard = []
    try:
        buttons = buttons_data.split('|')
        for button in buttons:
            if ':' in button:
                parts = button.split(':')
                if len(parts) >= 4:
                    # Формат: тип:текст:цена:дни/файл/url
                    btn_type, text, price, extra = parts[0], parts[1], parts[2], parts[3]
                    
                    if btn_type == "file":
                        # Для файлов используем короткий идентификатор (хэш)
                        short_id = hash(extra) % 10000
                        keyboard.append([InlineKeyboardButton(text=text, callback_data=f"file:{price}:{short_id}")])
                    
                    elif btn_type == "channel":
                        # Для каналов
                        keyboard.append([InlineKeyboardButton(text=text, callback_data=f"chan:{price}:{extra}")])
                    
                    elif btn_type == "url":
                        # ДЛЯ URL КНОПОК ИСПОЛЬЗУЕМ url, А НЕ callback_data!
                        keyboard.append([InlineKeyboardButton(text=text, url=extra)])
                        
    except Exception as e:
        logger.error(f"Ошибка создания клавиатуры: {e}")
        return None
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None

# Состояния FSM
class PostStates(StatesGroup):
    waiting_text = State()
    waiting_photo = State()
    waiting_buttons_choice = State()
    waiting_button_type = State()
    waiting_button_text = State()
    waiting_button_price = State()
    waiting_button_file = State()
    waiting_button_days = State()
    waiting_button_url = State()

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
        
        if not any(str(r.get("id", "")).strip() == user_id for r in records):
            ws.append_row([
                user_id,
                user.username or "",
                "",  # file_url
                "",  # subscription_type
                "",  # subscription_end
                "",  # post_id
                "",  # post_text
                "",  # post_photo
                ""   # post_buttons
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
            buttons_data = post.get("post_buttons", "").strip()
            
            keyboard = create_buttons_keyboard(buttons_data)
            
            try:
                if photo_id:
                    await message.answer_photo(
                        photo=photo_id,
                        caption=text,
                        reply_markup=keyboard if keyboard else (delete_kb(post["post_id"]) if message.from_user.id == ADMIN_ID else None)
                    )
                else:
                    await message.answer(
                        text=text,
                        reply_markup=keyboard if keyboard else (delete_kb(post["post_id"]) if message.from_user.id == ADMIN_ID else None)
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
        buttons_data = post.get("post_buttons", "").strip()
        
        keyboard = create_buttons_keyboard(buttons_data)
        
        try:
            if photo_id:
                await callback.message.answer_photo(
                    photo_id,
                    caption=f"{text}\n\nID: {post_id}\nКнопки: {buttons_data if buttons_data else 'нет'}",
                    reply_markup=keyboard if keyboard else delete_kb(post_id))
            else:
                await callback.message.answer(
                    f"{text}\n\nID: {post_id}\nКнопки: {buttons_data if buttons_data else 'нет'}",
                    reply_markup=keyboard if keyboard else delete_kb(post_id))
        except Exception as e:
            logger.error(f"Ошибка отправки поста {post_id}: {e}")
            await callback.message.answer(
                f"📄 {text[:300]}...\n\nID: {post_id}\nКнопки: {buttons_data if buttons_data else 'нет'}",
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
    await message.answer("📷 Отправьте фото или напишите 'пропустить':")

@dp.message(PostStates.waiting_photo)
async def process_post_photo(message: Message, state: FSMContext):
    try:
        if message.photo:
            await state.update_data(photo_id=message.photo[-1].file_id)
        elif message.text and message.text.lower() == "пропустить":
            await state.update_data(photo_id="")
        else:
            await message.answer("❌ Отправьте фото или напишите 'пропустить'")
            return

        # Предлагаем добавить кнопки
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да", callback_data="add_buttons_yes")],
            [InlineKeyboardButton(text="❌ Нет", callback_data="add_buttons_no")]
        ])
        
        await state.set_state(PostStates.waiting_buttons_choice)
        await message.answer("📌 Хотите добавить кнопки к посту?", reply_markup=keyboard)
            
    except Exception as e:
        logger.error(f"Ошибка обработки фото: {e}")
        await message.answer("❌ Ошибка обработки")
        await state.clear()

@dp.callback_query(PostStates.waiting_buttons_choice, F.data.in_(["add_buttons_yes", "add_buttons_no"]))
async def process_buttons_choice(callback: types.CallbackQuery, state: FSMContext):
    try:
        if callback.data == "add_buttons_no":
            # Сохраняем пост без кнопок
            await state.update_data(buttons="нет")
            await process_final_post(callback.message, state)
        else:
            # Предлагаем выбрать тип кнопки
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📁 Продаваемый файл", callback_data="button_type_file")],
                [InlineKeyboardButton(text="🔐 Приглашение в канал", callback_data="button_type_channel")],
                [InlineKeyboardButton(text="🔗 Обычная ссылка", callback_data="button_type_url")],
                [InlineKeyboardButton(text="✅ Готово", callback_data="buttons_done")]
            ])
            await state.set_state(PostStates.waiting_button_type)
            await state.update_data(buttons_data=[])
            await callback.message.answer("🎛 Выберите тип кнопки:", reply_markup=keyboard)
        
        await callback.answer()
    except Exception as e:
        logger.error(f"Ошибка выбора кнопок: {e}")
        await callback.message.answer("❌ Ошибка")

@dp.callback_query(PostStates.waiting_button_type, F.data.startswith("button_type_"))
async def process_button_type(callback: types.CallbackQuery, state: FSMContext):
    try:
        btn_type = callback.data.split("_")[2]
        await state.update_data(current_button_type=btn_type)
        
        if btn_type in ["file", "channel", "url"]:
            await state.set_state(PostStates.waiting_button_text)
            await callback.message.answer("📝 Введите текст для кнопки:")
        elif btn_type == "done":
            await process_final_post(callback.message, state)
        
        await callback.answer()
    except Exception as e:
        logger.error(f"Ошибка выбора типа: {e}")
        await callback.message.answer("❌ Ошибка")

@dp.message(PostStates.waiting_button_text)
async def process_button_text(message: Message, state: FSMContext):
    try:
        await state.update_data(current_button_text=message.text)
        data = await state.get_data()
        btn_type = data.get("current_button_type")
        
        if btn_type in ["file", "channel"]:
            await state.set_state(PostStates.waiting_button_price)
            await message.answer("💰 Введите цену в рублях:")
        elif btn_type == "url":
            await state.set_state(PostStates.waiting_button_url)
            await message.answer("🔗 Введите URL:")
            
    except Exception as e:
        logger.error(f"Ошибка текста кнопки: {e}")
        await message.answer("❌ Ошибка")

@dp.message(PostStates.waiting_button_price)
async def process_button_price(message: Message, state: FSMContext):
    try:
        price = message.text.strip()
        if not price.isdigit():
            await message.answer("❌ Введите корректную цену (число):")
            return
            
        await state.update_data(current_button_price=price)
        data = await state.get_data()
        btn_type = data.get("current_button_type")
        
        if btn_type == "file":
            await state.set_state(PostStates.waiting_button_file)
            await message.answer("📎 Отправьте файл для продажи:")
        elif btn_type == "channel":
            await state.set_state(PostStates.waiting_button_days)
            await message.answer("📅 Введите количество дней доступа (или 'навсегда'):")
            
    except Exception as e:
        logger.error(f"Ошибка цены: {e}")
        await message.answer("❌ Ошибка")

@dp.message(PostStates.waiting_button_file)
async def process_button_file(message: Message, state: FSMContext):
    try:
        if not (message.document or message.photo):
            await message.answer("❌ Отправьте файл или фото:")
            return
            
        file_id = message.document.file_id if message.document else message.photo[-1].file_id
        await state.update_data(current_button_file=file_id)
        
        # Добавляем кнопку в список
        data = await state.get_data()
        buttons_data = data.get("buttons_data", [])
        btn_type = data.get("current_button_type")
        text = data.get("current_button_text")
        price = data.get("current_button_price")
        file_id = data.get("current_button_file")
        
        buttons_data.append(f"{btn_type}:{text}:{price}:{file_id}")
        await state.update_data(buttons_data=buttons_data)
        
        # Возвращаемся к выбору типа
        await offer_more_buttons(message, state)
            
    except Exception as e:
        logger.error(f"Ошибка файла: {e}")
        await message.answer("❌ Ошибка")

@dp.message(PostStates.waiting_button_days)
async def process_button_days(message: Message, state: FSMContext):
    try:
        days = message.text.strip()
        if days.lower() != "навсегда" and not days.isdigit():
            await message.answer("❌ Введите число дней или 'навсегда':")
            return
            
        # Добавляем кнопку в список
        data = await state.get_data()
        buttons_data = data.get("buttons_data", [])
        btn_type = data.get("current_button_type")
        text = data.get("current_button_text")
        price = data.get("current_button_price")
        
        buttons_data.append(f"{btn_type}:{text}:{price}:{days}")
        await state.update_data(buttons_data=buttons_data)
        
        # Возвращаемся к выбору типа
        await offer_more_buttons(message, state)
            
    except Exception as e:
        logger.error(f"Ошибка дней: {e}")
        await message.answer("❌ Ошибка")

@dp.message(PostStates.waiting_button_url)
async def process_button_url(message: Message, state: FSMContext):
    try:
        url = message.text.strip()
        if not (url.startswith('http://') or url.startswith('https://')):
            await message.answer("❌ URL должен начинаться с http:// или https://")
            return
        
        # Добавляем кнопку в список (правильный формат)
        data = await state.get_data()
        buttons_data = data.get("buttons_data", [])
        btn_type = data.get("current_button_type")
        text = data.get("current_button_text")
        
        # Сохраняем в формате: url:текст:0:url_адрес
        buttons_data.append(f"{btn_type}:{text}:0:{url}")
        await state.update_data(buttons_data=buttons_data)
        
        # Возвращаемся к выбору типа
        await offer_more_buttons(message, state)
            
    except Exception as e:
        logger.error(f"Ошибка URL: {e}")
        await message.answer("❌ Ошибка")

async def offer_more_buttons(message: Message, state: FSMContext):
    """Предлагает добавить еще кнопки"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📁 Продаваемый файл", callback_data="button_type_file")],
        [InlineKeyboardButton(text="🔐 Приглашение в канал", callback_data="button_type_channel")],
        [InlineKeyboardButton(text="🔗 Обычная ссылка", callback_data="button_type_url")],
        [InlineKeyboardButton(text="✅ Готово", callback_data="button_type_done")]
    ])
    await state.set_state(PostStates.waiting_button_type)
    await message.answer("🎛 Добавить еще кнопку или завершить?", reply_markup=keyboard)

async def process_final_post(message: Message, state: FSMContext):
    """Финальное сохранение поста"""
    try:
        data = await state.get_data()
        text = data.get("text", "")
        photo_id = data.get("photo_id", "")
        buttons_data = data.get("buttons_data", [])
        
        if ws:
            records = ws.get_all_records()
            
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
            
            # Сохраняем в таблицу
            buttons_str = "|".join(buttons_data) if buttons_data else "нет"
            ws.append_row(["", "", "", "", "", post_id, text, photo_id, buttons_str])
            
            # Создаем клавиатуру для рассылки
            keyboard = create_buttons_keyboard(buttons_str)
            
            # Рассылаем пост
            success = 0
            for user_id in user_ids:
                try:
                    if photo_id:
                        await bot.send_photo(
                            user_id, 
                            photo=photo_id, 
                            caption=text,
                            reply_markup=keyboard
                        )
                    else:
                        await bot.send_message(
                            user_id, 
                            text=text,
                            reply_markup=keyboard
                        )
                    success += 1
                except Exception as e:
                    logger.error(f"Не удалось отправить пост пользователю {user_id}: {e}")

            await message.answer(
                f"✅ Пост добавлен (ID: {post_id})\n"
                f"Кнопки: {len(buttons_data)} шт.\n"
                f"Отправлено: {success}/{len(user_ids)}"
            )
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
