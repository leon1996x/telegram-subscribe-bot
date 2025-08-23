import os
import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import unquote
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

# === CONFIG ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7145469393"))
GSHEET_ID = os.getenv("GSHEET_ID")
PAYFORM_URL = "https://menyayrealnost.payform.ru"
USERS_FILE = "paid_users.json"
CHANNEL_ACCESS_FILE = "channel_access.json"

# ID вашего канала (замените на реальный)
CHANNEL_ID = -1002681575953

# Проверка переменных
if not all([BOT_TOKEN, GSHEET_ID]):
    missing = [name for name, val in [("BOT_TOKEN", BOT_TOKEN), ("GSHEET_ID", GSHEET_ID)] if not val]
    raise RuntimeError(f"Не заданы: {', '.join(missing)}")

# Настройка логгирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()

# Хранилища
paid_files = {}
file_id_mapping = {}  # Маппинг short_id -> file_id
channel_access = {}   # Доступы к каналу: {user_id: expiry_date}

# === Загрузка/сохранение данных ===
def load_data():
    global paid_files, channel_access
    # Загрузка оплаченных файлов
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r") as f:
                paid_files = json.load(f)
                for user_id, files in paid_files.items():
                    for file_id, expiry_str in files.items():
                        if expiry_str and expiry_str != "forever":
                            paid_files[user_id][file_id] = datetime.fromisoformat(expiry_str)
        except Exception as e:
            logger.error(f"Ошибка загрузки файлов оплаты: {e}")
            paid_files = {}
    
    # Загрузка доступов к каналу
    if os.path.exists(CHANNEL_ACCESS_FILE):
        try:
            with open(CHANNEL_ACCESS_FILE, "r") as f:
                channel_access_data = json.load(f)
                for user_id, expiry_str in channel_access_data.items():
                    if expiry_str == "forever":
                        channel_access[int(user_id)] = "forever"
                    else:
                        channel_access[int(user_id)] = datetime.fromisoformat(expiry_str)
        except Exception as e:
            logger.error(f"Ошибка загрузки доступов к каналу: {e}")
            channel_access = {}

def save_data():
    try:
        # Сохранение оплаченных файлов
        save_files_data = {}
        for user_id, files in paid_files.items():
            save_files_data[user_id] = {}
            for file_id, expiry in files.items():
                save_files_data[user_id][file_id] = expiry.isoformat() if isinstance(expiry, datetime) else expiry
        
        with open(USERS_FILE, "w") as f:
            json.dump(save_files_data, f)
        
        # Сохранение доступов к каналу
        save_channel_data = {}
        for user_id, expiry in channel_access.items():
            save_channel_data[str(user_id)] = expiry.isoformat() if isinstance(expiry, datetime) else expiry
        
        with open(CHANNEL_ACCESS_FILE, "w") as f:
            json.dump(save_channel_data, f)
            
    except Exception as e:
        logger.error(f"Ошибка сохранения данных: {e}")

# === Проверка и удаление просроченных доступов ===
def check_expired_access():
    now = datetime.now()
    
    # Проверка файлов
    expired_files = []
    for user_id, files in paid_files.items():
        for file_id, expiry in files.items():
            if isinstance(expiry, datetime) and now >= expiry:
                expired_files.append((user_id, file_id))
    
    for user_id, file_id in expired_files:
        try:
            logger.info(f"Удален доступ пользователя {user_id} к файлу {file_id}")
            del paid_files[user_id][file_id]
            if not paid_files[user_id]:
                del paid_files[user_id]
        except Exception as e:
            logger.error(f"Ошибка при удалении доступа к файлу: {e}")
    
    # Проверка доступа к каналу
    expired_channel = []
    for user_id, expiry in channel_access.items():
        if isinstance(expiry, datetime) and now >= expiry:
            expired_channel.append(user_id)
    
    for user_id in expired_channel:
        try:
            logger.info(f"Удаляем доступ пользователя {user_id} к каналу")
            # Кикаем пользователя из канала
            await bot.ban_chat_member(CHANNEL_ID, user_id)
            await bot.unban_chat_member(CHANNEL_ID, user_id)
            await bot.send_message(user_id, "⏰ Срок вашей подписки истёк. Для продления оплатите снова.")
            del channel_access[user_id]
        except Exception as e:
            logger.error(f"Ошибка при удалении доступа к каналу: {e}")
    
    if expired_files or expired_channel:
        save_data()

# === Фоновая проверка каждую минуту ===
def access_watcher():
    logger.info("[WATCHER] Запущен мониторинг доступов")
    while True:
        check_expired_access()
        time.sleep(60)

# === Генерация ссылки на оплату ===
def generate_payment_link(user_id: int, item_id: str, price: int, item_name: str, item_type: str):
    webhook_url = "https://telegram-subscribe-bot-5oh7.onrender.com/webhook"
    
    params = {
        "do": "pay",
        "products[0][name]": f"{item_type}: {item_name}",
        "products[0][price]": price,
        "products[0][quantity]": 1,
        "order_id": f"{item_type}_{user_id}_{item_id}",
        "customer_extra": f"Оплата {item_type} {item_id} от пользователя {user_id}",
        "callback_url": webhook_url
    }
    query = "&".join([f"{k}={v}" for k, v in params.items()])
    return f"{PAYFORM_URL}/?{query}"

def extract_payment_info(data: dict) -> tuple:
    """Извлекает user_id и item_id из данных платежа"""
    order_id = data.get('order_id', '')
    customer_extra = unquote(data.get('customer_extra', ''))
    
    # Определяем тип оплаты (file или channel)
    if order_id.startswith('file_'):
        parts = order_id.split('_')
        if len(parts) >= 3:
            return parts[1], '_'.join(parts[2:]), 'file'
    elif order_id.startswith('channel_'):
        parts = order_id.split('_')
        if len(parts) >= 3:
            return parts[1], '_'.join(parts[2:]), 'channel'
    
    # Пытаемся извлечь из customer_extra
    patterns = [
        r'файла (.+?) от пользователя (\d+)',
        r'канала (.+?) от пользователя (\d+)',
        r'file_(.+?)_(\d+)',
        r'channel_(.+?)_(\d+)',
        r'user[:_](\d+).*(file|channel)[:_](.+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, customer_extra, re.IGNORECASE)
        if match:
            if len(match.groups()) >= 3:
                return match.group(2), match.group(1), match.group(3) if 'file' in match.group(3) or 'channel' in match.group(3) else 'file'
            else:
                return match.group(2), match.group(1), 'file'
    
    # Последняя попытка
    user_id_match = re.search(r'(\d{5,})', customer_extra)
    if user_id_match:
        user_id = user_id_match.group(1)
        # Пытаемся определить тип
        if 'канал' in customer_extra.lower():
            return user_id, 'channel_access', 'channel'
        else:
            return user_id, 'file_access', 'file'
    
    raise ValueError(f"Не могу извлечь информацию из: {order_id}, {customer_extra}")

# === Функции для работы с каналом ===
async def grant_channel_access(user_id: int, days: int = None):
    """Предоставляет доступ к каналу"""
    try:
        # Разбаниваем пользователя
        await bot.unban_chat_member(CHANNEL_ID, user_id)
        
        # Создаем одноразовую ссылку
        invite_link = await bot.create_chat_invite_link(
            chat_id=CHANNEL_ID,
            member_limit=1,
            creates_join_request=False
        )
        
        # Сохраняем доступ
        if days is None:
            channel_access[user_id] = "forever"
        else:
            channel_access[user_id] = datetime.now() + timedelta(days=days)
        
        save_data()
        
        return invite_link.invite_link
        
    except Exception as e:
        logger.error(f"Ошибка предоставления доступа к каналу: {e}")
        raise

async def revoke_channel_access(user_id: int):
    """Отзывает доступ к каналу"""
    try:
        await bot.ban_chat_member(CHANNEL_ID, user_id)
        await bot.unban_chat_member(CHANNEL_ID, user_id)
        if user_id in channel_access:
            del channel_access[user_id]
        save_data()
    except Exception as e:
        logger.error(f"Ошибка отзыва доступа к каналу: {e}")

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
        logger.info("Нет данных для создания клавиатуры")
        return None
    
    keyboard = []
    try:
        buttons = buttons_data.split('|')
        logger.info(f"Разделенные кнопки: {buttons}")
        
        i = 0
        while i < len(buttons):
            button = buttons[i]
            logger.info(f"Обрабатываю кнопку [{i}]: {button}")
            
            # Для URL кнопок
            if button == "url" and i + 2 < len(buttons):
                text = buttons[i + 1]
                url = buttons[i + 2]
                if url.startswith(('http://', 'https://')):
                    keyboard.append([InlineKeyboardButton(text=text, url=url)])
                    i += 3
                    continue
            
            # Для файловых кнопок
            elif button == "file" and i + 3 < len(buttons):
                text = buttons[i + 1]
                price = buttons[i + 2]
                short_id = buttons[i + 3]
                keyboard.append([InlineKeyboardButton(
                    text=f"{text} - {price}₽", 
                    callback_data=f"buy_file:{short_id}:{price}"
                )])
                i += 4
                continue
            
            # Для канальных кнопок
            elif button == "channel" and i + 4 < len(buttons):
                text = buttons[i + 1]
                price = buttons[i + 2]
                days = buttons[i + 3]
                channel_id = buttons[i + 4]
                callback_data = f"buy_channel:{channel_id}:{price}:{days}"
                keyboard.append([InlineKeyboardButton(
                    text=f"{text} - {price}₽", 
                    callback_data=callback_data
                )])
                i += 5
                continue
            
            i += 1
                        
    except Exception as e:
        logger.error(f"Ошибка создания клавиатуры: {e}", exc_info=True)
        return None
    
    logger.info(f"Итоговая клавиатура: {keyboard}")
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
    waiting_button_channel_days = State()

# Регистрация пользователя
async def register_user(user: types.User):
    if not ws:
        return
        
    try:
        user_id = str(user.id)
        records = ws.get_all_records()
        
        if not any(str(r.get("id", "")).strip() == user_id for r in records):
            ws.append_row([
                user_id,
                user.username or "",
                "", "", "", "", "", "", ""
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

@dp.message(Command("myfiles"))
async def cmd_myfiles(message: Message):
    """Показать оплаченные файлы и доступы"""
    user_id = str(message.from_user.id)
    response = []
    
    # Файлы
    if user_id in paid_files and paid_files[user_id]:
        files_list = []
        for file_id, expiry in paid_files[user_id].items():
            status = "✅ Бессрочный" if expiry == "forever" else f"⏰ До {expiry.strftime('%d.%m.%Y %H:%M')}"
            short_file_id = file_id[:20] + "..." if len(file_id) > 20 else file_id
            files_list.append(f"📁 {short_file_id} - {status}")
        response.append("📦 Ваши оплаченные файлы:\n" + "\n".join(files_list))
    
    # Доступ к каналу
    user_id_int = message.from_user.id
    if user_id_int in channel_access:
        expiry = channel_access[user_id_int]
        if expiry == "forever":
            response.append("🔐 Доступ к каналу: ✅ Бессрочный")
        else:
            response.append(f"🔐 Доступ к каналу: ⏰ До {expiry.strftime('%d.%m.%Y %H:%M')}")
    
    if response:
        await message.answer("\n\n".join(response) + "\n\nНажмите на кнопку в посте для использования")
    else:
        await message.answer("📭 У вас нет оплаченных файлов или доступов")

# Обработчики кнопок
@dp.callback_query(F.data.startswith("buy_file:"))
async def buy_file_callback(callback: types.CallbackQuery):
    try:
        parts = callback.data.split(':')
        if len(parts) < 3:
            await callback.answer("❌ Ошибка формата кнопки")
            return
            
        short_id = parts[1]
        price = parts[2]
        user_id = str(callback.from_user.id)
        
        file_id = file_id_mapping.get(short_id)
        if not file_id:
            await callback.answer("❌ Файл не найден")
            return
        
        # Проверяем доступ
        if user_id in paid_files and file_id in paid_files[user_id]:
            expiry = paid_files[user_id][file_id]
            if isinstance(expiry, datetime) and datetime.now() < expiry:
                await send_file_to_user(callback.from_user.id, file_id, "✅ Вот ваш файл!")
                await callback.answer()
                return
            elif expiry == "forever":
                await send_file_to_user(callback.from_user.id, file_id, "✅ Вот ваш файл!")
                await callback.answer()
                return
        
        # Предлагаем оплатить
        payment_url = generate_payment_link(callback.from_user.id, file_id, int(price), "Файл", "file")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 Оплатить {price}₽", url=payment_url)]
        ])
        
        await callback.message.answer(
            f"📦 Для получения файла необходимо оплатить {price}₽",
            reply_markup=keyboard
        )
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка обработки покупки файла: {e}")
        await callback.answer("❌ Ошибка при обработке запроса")

@dp.callback_query(F.data.startswith("buy_channel:"))
async def buy_channel_callback(callback: types.CallbackQuery):
    try:
        parts = callback.data.split(':')
        if len(parts) < 4:
            await callback.answer("❌ Ошибка формата кнопки")
            return
            
        channel_id = parts[1]
        price = parts[2]
        days = parts[3]
        user_id_int = callback.from_user.id
        
        # Проверяем доступ
        if user_id_int in channel_access:
            expiry = channel_access[user_id_int]
            if expiry == "forever" or (isinstance(expiry, datetime) and datetime.now() < expiry):
                try:
                    invite_link = await grant_channel_access(user_id_int)
                    await callback.message.answer(
                        f"✅ У вас уже есть доступ к каналу!\n" 
                        f"Ссылка для входа: {invite_link}"
                    )
                    await callback.answer()
                    return
                except Exception as e:
                    await callback.message.answer("❌ Ошибка доступа к каналу")
                    await callback.answer()
                    return
        
        # Предлагаем оплатить
        item_name = f"Доступ на {days} дней" if days != "forever" else "Бессрочный доступ"
        payment_url = generate_payment_link(user_id_int, f"{channel_id}_{days}", int(price), item_name, "channel")
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 Оплатить {price}₽", url=payment_url)]
        ])
        
        await callback.message.answer(
            f"🔐 Для доступа к каналу необходимо оплатить {price}₽\n"
            f"Срок доступа: {item_name}",
            reply_markup=keyboard
        )
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка обработки покупки доступа: {e}")
        await callback.answer("❌ Ошибка при обработке запроса")

# === Prodamus webhook ===
@app.post("/webhook/prodamus/files")
async def prodamus_webhook(request: Request):
    try:
        logger.info("=== ПОЛУЧЕН ВЕБХУК ОТ PRODAMUS ===")
        
        form_data = await request.form()
        data = dict(form_data)
        
        logger.info(f"Получен вебхук: {dict(data)}")
        
        if data.get('payment_status') != 'success':
            logger.warning(f"Платеж не успешен: {data.get('payment_status')}")
            return {"status": "error", "message": "Payment not successful"}
        
        # Извлекаем информацию о платеже
        user_id, item_id, item_type = extract_payment_info(data)
        user_id_int = int(user_id)
        
        logger.info(f"Извлечено: user_id={user_id}, item_id={item_id}, type={item_type}")
        
        if item_type == 'file':
            # Обработка файла
            if user_id not in paid_files:
                paid_files[user_id] = {}
            paid_files[user_id][item_id] = "forever"
            save_data()
            
            await bot.send_message(user_id_int, "✅ Оплата прошла успешно! Вот ваш файл:")
            await send_file_to_user(user_id_int, item_id, "✅ Ваш файл")
            
            await bot.send_message(
                ADMIN_ID,
                f"💰 Пользователь {user_id} оплатил файл\n"
                f"📁 File ID: {item_id}\n"
                f"💳 Сумма: {data.get('amount', 'N/A')}₽"
            )
            
        elif item_type == 'channel':
            # Обработка доступа к каналу
            parts = item_id.split('_')
            channel_id = parts[0]
            days_str = parts[1] if len(parts) > 1 else "forever"
            
            days = None if days_str == "forever" else int(days_str)
            invite_link = await grant_channel_access(user_id_int, days)
            
            await bot.send_message(
                user_id_int,
                f"✅ Оплата прошла успешно!\n"
                f"Ссылка для входа в канал: {invite_link}\n"
                f"Срок доступа: {'бессрочно' if days is None else f'{days} дней'}"
            )
            
            await bot.send_message(
                ADMIN_ID,
                f"💰 Пользователь {user_id} оплатил доступ к каналу\n"
                f"⏰ Срок: {'бессрочно' if days is None else f'{days} дней'}\n"
                f"💳 Сумма: {data.get('amount', 'N/A')}₽"
            )
        
        return {"status": "success"}
        
    except Exception as e:
        logger.error(f"Ошибка вебхука: {e}", exc_info=True)
        await bot.send_message(ADMIN_ID, f"🚨 Ошибка вебхука: {e}")
        return {"status": "error", "message": str(e)}

# Остальной код остается без изменений...
# [Здесь должен быть остальной код из вашего файла: состояния FSM, обработчики и т.д.]

# Webhook
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}{WEBHOOK_PATH}"

@app.on_event("startup")
async def startup():
    if os.getenv("RENDER"):
        await bot.set_webhook(WEBHOOK_URL)
        logger.info(f"Webhook установлен: {WEBHOOK_URL}")
    # Загружаем данные и запускаем мониторинг
    load_data()
    threading.Thread(target=access_watcher, daemon=True).start()

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    data = await request.json()
    update = types.Update(**data)
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.get("/")
async def health_check():
    return {"status": "ok", "sheets": bool(ws), "paid_files_count": len(paid_files), "channel_access_count": len(channel_access)}
