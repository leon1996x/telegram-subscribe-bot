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

# Основные каналы
CHANNELS = {
    "main": "-1002681575953",  # Основной канал "Меняя реальность"
}

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
file_id_mapping = {}
channel_access = {}  # {user_id: {channel_id: expiry_date}}

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
    
    # Загрузка доступа к каналам из Google Sheets
    channel_access = {}
    if ws:
        try:
            records = ws.get_all_values()
            for row in records[1:]:  # пропускаем заголовок
                if len(row) > 9 and row[9]:  # channel_access в 10-м столбце
                    user_id = str(row[0])
                    accesses = row[9].split(';')
                    
                    if user_id not in channel_access:
                        channel_access[user_id] = {}
                    
                    for access in accesses:
                        if ':' in access:
                            channel_id, expiry_str = access.split(':', 1)
                            if expiry_str == "forever":
                                channel_access[user_id][channel_id] = "forever"
                            else:
                                try:
                                    channel_access[user_id][channel_id] = datetime.fromisoformat(expiry_str)
                                except ValueError:
                                    logger.error(f"Неверный формат даты: {expiry_str}")
            
            logger.info(f"Загружено {sum(len(v) for v in channel_access.values())} доступов к каналам из Google Sheets")
        except Exception as e:
            logger.error(f"Ошибка загрузки доступа к каналам из Google Sheets: {e}")
    
    # Дополнительная загрузка из локального файла (для обратной совместимости)
    if os.path.exists(CHANNEL_ACCESS_FILE):
        try:
            with open(CHANNEL_ACCESS_FILE, "r") as f:
                local_access = json.load(f)
                for user_id, channels in local_access.items():
                    if user_id not in channel_access:
                        channel_access[user_id] = {}
                    
                    for channel_id, expiry_str in channels.items():
                        if expiry_str != "forever":
                            channel_access[user_id][channel_id] = datetime.fromisoformat(expiry_str)
                        else:
                            channel_access[user_id][channel_id] = "forever"
        except Exception as e:
            logger.error(f"Ошибка загрузки доступа к каналам из локального файла: {e}")

async def reload_channel_access():
    """Принудительно перезагружает доступы из Google Sheets"""
    global channel_access
    channel_access = {}
    
    if ws:
        try:
            records = ws.get_all_values()
            for row in records[1:]:  # пропускаем заголовок
                if len(row) > 9 and row[9]:  # channel_access в 10-м столбце
                    user_id = str(row[0])
                    accesses = row[9].split(';')
                    
                    if user_id not in channel_access:
                        channel_access[user_id] = {}
                    
                    for access in accesses:
                        if ':' in access:
                            channel_id, expiry_str = access.split(':', 1)
                            if expiry_str == "forever":
                                channel_access[user_id][channel_id] = "forever"
                            else:
                                try:
                                    channel_access[user_id][channel_id] = datetime.fromisoformat(expiry_str)
                                except ValueError:
                                    logger.error(f"Неверный формат даты: {expiry_str}")
            
            logger.info(f"✅ Перезагружено {sum(len(v) for v in channel_access.values())} доступов из Google Sheets")
        except Exception as e:
            logger.error(f"❌ Ошибка перезагрузки доступов: {e}")

def save_data():
    # Сохранение оплаченных файлов
    try:
        save_files = {}
        for user_id, files in paid_files.items():
            save_files[user_id] = {}
            for file_id, expiry in files.items():
                save_files[user_id][file_id] = expiry.isoformat() if isinstance(expiry, datetime) else expiry
        
        with open(USERS_FILE, "w") as f:
            json.dump(save_files, f)
    except Exception as e:
        logger.error(f"Ошибка сохранения файлов оплаты: {e}")
    
    # Сохранение доступа к каналам в локальный файл (оставляем для резервной копии)
    try:
        save_access = {}
        for user_id, channels in channel_access.items():
            save_access[user_id] = {}
            for channel_id, expiry in channels.items():
                save_access[user_id][channel_id] = expiry.isoformat() if isinstance(expiry, datetime) else expiry
        
        with open(CHANNEL_ACCESS_FILE, "w") as f:
            json.dump(save_access, f)
    except Exception as e:
        logger.error(f"Ошибка сохранения доступа к каналам: {e}")

# === Универсальная функция отправки файла ===
async def send_file_to_user(user_id: int, file_id: str, caption: str = "Ваш файл"):
    """Универсальная функция отправки файла любого типа"""
    try:
        await bot.send_document(user_id, file_id, caption=caption)
        logger.info(f"Файл отправлен как документ: {file_id}")
    except Exception as doc_error:
        try:
            await bot.send_photo(user_id, file_id, caption=caption)
            logger.info(f"Файл отправлен как фото: {file_id}")
        except Exception as photo_error:
            try:
                await bot.send_video(user_id, file_id, caption=caption)
                logger.info(f"Файл отправлен как видео: {file_id}")
            except Exception as video_error:
                try:
                    await bot.send_audio(user_id, file_id, caption=caption)
                    logger.info(f"Файл отправлен как аудио: {file_id}")
                except Exception as audio_error:
                    logger.error(f"Не удалось отправить файл {file_id}: {doc_error}, {photo_error}, {video_error}, {audio_error}")
                    await bot.send_message(user_id, "❌ Не удалось отправить файл. Свяжитесь с администратором.")

# === Проверка и удаление просроченных доступов ===
async def check_expired_access():
    # ПЕРЕЗАГРУЖАЕМ ДАННЫЕ ПЕРЕД КАЖДОЙ ПРОВЕРКОЙ
    await reload_channel_access()
    
    now = datetime.now()
    logger.info(f"🔍 [ПРОВЕРКА] Начало проверки в {now}")
    logger.info(f"🔍 [ДАННЫЕ] Загружено доступов: {len(channel_access)}")
    
    # Проверка файлов
    expired_files = []
    for user_id, files in paid_files.items():
        for file_id, expiry in files.items():
            if isinstance(expiry, datetime) and now >= expiry:
                expired_files.append((user_id, file_id))
                logger.info(f"📁 [ПРОСРОЧКА] Файл {file_id} у пользователя {user_id}")
    
    for user_id, file_id in expired_files:
        try:
            del paid_files[user_id][file_id]
            if not paid_files[user_id]:
                del paid_files[user_id]
            logger.info(f"✅ [УДАЛЕНО] Файл {file_id} у пользователя {user_id}")
        except Exception as e:
            logger.error(f"Ошибка при удалении доступа к файлу: {e}")
    
    # Проверка доступа к каналам
    expired_channels = []
    for user_id, channels in channel_access.items():
        for channel_id, expiry in channels.items():
            if isinstance(expiry, datetime) and now >= expiry:
                expired_channels.append((user_id, channel_id))
                logger.info(f"📢 [ПРОСРОЧКА] Канал {channel_id} у пользователя {user_id}")
            elif expiry == "forever":
                logger.info(f"✅ [БЕССРОЧНЫЙ] Канал {channel_id} у пользователя {user_id}")
    
    for user_id, channel_id in expired_channels:
        try:
            # Кикаем пользователя из канала
            await bot.ban_chat_member(int(channel_id), int(user_id))
            await bot.unban_chat_member(int(channel_id), int(user_id))
            
            # Уведомляем пользователя
            await bot.send_message(int(user_id), f"⏰ Срок вашего доступа к каналу истёк. Для продления оплатите подписку снова.")
            
            # Удаляем из хранилища
            del channel_access[user_id][channel_id]
            if not channel_access[user_id]:
                del channel_access[user_id]
            
            # Удаляем из Google Sheets
            if ws:
                try:
                    records = ws.get_all_values()
                    for idx, row in enumerate(records[1:], start=2):
                        if str(row[0]) == user_id:  # находим пользователя
                            current_access = row[9] if len(row) > 9 else ""  # channel_access в 10-м столбце
                            if current_access:
                                # Удаляем конкретный канал из списка
                                accesses = current_access.split(';')
                                new_accesses = [
                                    acc for acc in accesses 
                                    if not acc.startswith(f"{channel_id}:")
                                ]
                                ws.update_cell(idx, 10, ';'.join(new_accesses))
                                logger.info(f"✅ [GSHEET] Удален доступ к {channel_id} для {user_id}")
                            break
                except Exception as e:
                    logger.error(f"Ошибка удаления доступа из Google Sheets: {e}")
                
            logger.info(f"✅ [УДАЛЕНО] Пользователь {user_id} удалён из канала {channel_id}")
        except Exception as e:
            logger.error(f"❌ [ОШИБКА] При удалении доступа к каналу: {e}")
            # Если ошибка прав, попробуем хотя бы удалить из базы
            try:
                del channel_access[user_id][channel_id]
                if not channel_access[user_id]:
                    del channel_access[user_id]
                logger.info(f"✅ [БАЗА] Пользователь {user_id} удален из базы (канал {channel_id})")
            except:
                pass
    
    if expired_files or expired_channels:
        save_data()
        logger.info(f"💾 [СОХРАНЕНО] Данные обновлены")
    
    logger.info(f"🔍 [ПРОВЕРКА] Завершена. Найдено: {len(expired_files)} файлов, {len(expired_channels)} каналов")

# === Фоновая проверка ===
def access_watcher():
    logger.info("[WATCHER] Запущен мониторинг доступов")
    while True:
        try:
            import asyncio
            asyncio.run(check_expired_access())
        except Exception as e:
            logger.error(f"❌ [WATCHER] Ошибка: {e}")
        time.sleep(60)

# === Генерация ссылок на оплату ===
def generate_file_payment_link(user_id: int, file_id: str, price: int, file_name: str):
    params = {
        "do": "pay",
        "products[0][name]": f"Файл: {file_name}",
        "products[0][price]": price,
        "products[0][quantity]": 1,
        "order_id": f"file_{user_id}_{file_id}",
        "order_num": f"file_{user_id}_{file_id}",
        "customer_extra": f"Оплата файла {file_id} от пользователя {user_id}",
        "callback_url": "https://telegram-subscribe-bot-5oh7.onrender.com/webhook"
    }
    query = "&".join([f"{k}={v}" for k, v in params.items()])
    return f"{PAYFORM_URL}/?{query}"

def generate_channel_payment_link(user_id: int, channel_id: str, price: int, days: int):
    period = f"{days} дней" if days != 0 else "навсегда"
    params = {
        "do": "pay",
        "products[0][name]": f"Доступ к каналу ({period})",
        "products[0][price]": price,
        "products[0][quantity]": 1,
        "order_id": f"channel_{user_id}_{channel_id}_{days}",
        "order_num": f"channel_{user_id}_{channel_id}_{days}",
        "customer_extra": f"Оплата доступа к каналу {channel_id} на {period} от пользователя {user_id}",
        "callback_url": "https://telegram-subscribe-bot-5oh7.onrender.com/webhook"
    }
    query = "&".join([f"{k}={v}" for k, v in params.items()])
    return f"{PAYFORM_URL}/?{query}"

# === Извлечение информации о платеже ===
def extract_payment_info(data: dict) -> tuple:
    """Извлекает user_id и file_id из данных платежа"""
    order_id = data.get('order_id', '')
    order_num = data.get('order_num', '')
    customer_extra = unquote(data.get('customer_extra', ''))
    
    logger.info(f"DEBUG: order_id={order_id}, order_num={order_num}, customer_extra={customer_extra}")
    
    # Сначала проверяем order_num (там наш формат)
    if order_num.startswith('channel_'):
        parts = order_num.split('_')
        if len(parts) >= 4:
            return "channel", parts[1], parts[2], int(parts[3])
    
    # Затем проверяем order_id (старый формат)
    elif order_id.startswith('channel_'):
        parts = order_id.split('_')
        if len(parts) >= 4:
            return "channel", parts[1], parts[2], int(parts[3])
    
    # Для файлов
    elif order_num.startswith('file_'):
        parts = order_num.split('_')
        if len(parts) >= 3:
            return "file", parts[1], '_'.join(parts[2:]), None
    
    elif order_id.startswith('file_'):
        parts = order_id.split('_')
        if len(parts) >= 3:
            return "file", parts[1], '_'.join(parts[2:]), None
    
    # Пытаемся извлечь из customer_extra (резервный вариант)
    patterns = [
        r'канала (.+?) на (\d+) дней от пользователя (\d+)',
        r'канала (.+?) на (\d+) дн\. от пользователя (\d+)',
        r'канала (.+?) на (.+?) от пользователя (\d+)',
        r'файла (.+?) от пользователя (\d+)',
        r'channel_(.+?)_(\d+)_(\d+)',
        r'file_(.+?)_(\d+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, customer_extra, re.IGNORECASE)
        if match:
            logger.info(f"DEBUG: Pattern {pattern} matched: {match.groups()}")
            
            if 'канала' in pattern or 'channel' in pattern:
                if len(match.groups()) >= 3:
                    channel_id = match.group(1)
                    days_str = match.group(2)
                    user_id = match.group(3)
                    
                    # Обрабатываем "навсегда"
                    if 'навсегда' in days_str:
                        days = 0
                    else:
                        # Извлекаем число из строки
                        days_match = re.search(r'\d+', days_str)
                        days = int(days_match.group()) if days_match else 1
                    
                    return "channel", user_id, channel_id, days
            
            elif 'файла' in pattern or 'file' in pattern:
                if len(match.groups()) >= 2:
                    return "file", match.group(2), match.group(1), None
    
    # Последняя попытка - ищем числа в customer_extra
    logger.warning(f"Нестандартный формат данных, пробуем извлечь вручную...")
    
    # Ищем user_id (обычно 8-10 цифр)
    user_id_match = re.search(r'(\d{8,10})', customer_extra)
    if user_id_match:
        user_id = user_id_match.group(1)
        
        # Пытаемся найти channel_id (начинается с -100)
        channel_match = re.search(r'(-100\d+)', customer_extra)
        if channel_match:
            channel_id = channel_match.group(1)
            
            # Ищем количество дней
            days_match = re.search(r'на (\d+) дней', customer_extra)
            days = int(days_match.group(1)) if days_match else 1
            
            return "channel", user_id, channel_id, days
        
        # Пытаемся найти file_id (начинается с BQAC)
        file_match = re.search(r'(BQACAgI[A-Za-z0-9_-]+)', customer_extra)
        if file_match:
            return "file", user_id, file_match.group(1), None
    
    raise ValueError(f"Не могу извлечь данные из: order_id={order_id}, order_num={order_num}, customer_extra={customer_extra}")

# === Функции для работы с каналами ===
async def grant_channel_access(user_id: int, channel_id: str, days: int):
    """Предоставляет доступ к каналу и сохраняет в Google Sheets"""
    try:
        # Разбаниваем пользователя
        await bot.unban_chat_member(int(channel_id), user_id)
        
        # Создаем одноразовую ссылку
        invite = await bot.create_chat_invite_link(
            chat_id=int(channel_id),
            expire_date=None,
            member_limit=1
        )
        
        # Сохраняем доступ в памяти
        if str(user_id) not in channel_access:
            channel_access[str(user_id)] = {}
        
        if days == 0:  # навсегда
            channel_access[str(user_id)][channel_id] = "forever"
            expiry_date = "forever"
        else:
            expiry_date = datetime.now() + timedelta(days=days)
            channel_access[str(user_id)][channel_id] = expiry_date
        
        # Сохраняем доступ в Google Sheets
        if ws:
            try:
                # Находим запись пользователя
                records = ws.get_all_values()
                for idx, row in enumerate(records[1:], start=2):  # пропускаем заголовок
                    if str(row[0]) == str(user_id):  # проверяем ID в первом столбце
                        # Обновляем channel_access (10-й столбец, индекс 9)
                        current_access = row[9] if len(row) > 9 else ""
                        new_access = f"{channel_id}:{expiry_date}"
                        
                        if current_access:
                            # Проверяем, есть ли уже доступ к этому каналу
                            accesses = current_access.split(';')
                            updated = False
                            for i, acc in enumerate(accesses):
                                if acc.startswith(f"{channel_id}:"):
                                    accesses[i] = new_access
                                    updated = True
                                    break
                            
                            if not updated:
                                accesses.append(new_access)
                            
                            ws.update_cell(idx, 10, ';'.join(accesses))
                        else:
                            ws.update_cell(idx, 10, new_access)
                        break
                else:
                    # Если пользователь не найден, создаем новую запись
                    ws.append_row([
                        user_id, "", "", "", "", "", "", "", "", 
                        f"{channel_id}:{expiry_date}"
                    ])
            except Exception as e:
                logger.error(f"Ошибка сохранения доступа в Google Sheets: {e}")
        
        save_data()
        
        return invite.invite_link
        
    except Exception as e:
        logger.error(f"Ошибка предоставления доступа к каналу: {e}")
        raise

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
        i = 0
        
        while i < len(buttons):
            button = buttons[i]
            
            # URL кнопки: url|текст|url_адрес
            if button == "url" and i + 2 < len(buttons):
                text = buttons[i + 1]
                url = buttons[i + 2]
                if url.startswith(('http://', 'https://')):
                    keyboard.append([InlineKeyboardButton(text=text, url=url)])
                    i += 3
                    continue
            
            # Файловые кнопки: file|текст|цена|short_id
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
            
            # Канальные кнопки: channel|текст|цена|channel_id|дни
            elif button == "channel" and i + 4 < len(buttons):
                text = buttons[i + 1]
                price = buttons[i + 2]
                channel_id = buttons[i + 3]
                days = buttons[i + 4]
                
                period = "навсегда" if days == "0" else f"{days} дн."
                keyboard.append([InlineKeyboardButton(
                    text=f"{text} - {price}₽ ({period})", 
                    callback_data=f"buy_channel:{channel_id}:{price}:{days}"
                )])
                i += 5
                continue
            
            i += 1
                        
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
    waiting_button_channel = State()
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
                "",  # post_buttons
                ""   # channel_access
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
    """Показать оплаченные файлы пользователя"""
    user_id = str(message.from_user.id)
    
    if user_id in paid_files and paid_files[user_id]:
        files_list = []
        for file_id, expiry in paid_files[user_id].items():
            status = "✅ Бессрочный" if expiry == "forever" else f"⏰ До {expiry}"
            short_file_id = file_id[:20] + "..." if len(file_id) > 20 else file_id
            files_list.append(f"📁 {short_file_id} - {status}")
        
        await message.answer(
            "📦 Ваши оплаченные файлы:\n\n" + "\n".join(files_list) +
            "\n\nНажмите на кнопку файла в посте для скачивания"
        )
    else:
        await message.answer("📭 У вас нет оплаченных файлов")

@dp.message(Command("myaccess"))
async def cmd_myaccess(message: Message):
    """Показать активные доступы пользователя"""
    user_id = str(message.from_user.id)
    
    if user_id in channel_access and channel_access[user_id]:
        access_list = []
        for channel_id, expiry in channel_access[user_id].items():
            status = "✅ Бессрочный" if expiry == "forever" else f"⏰ До {expiry.strftime('%d.%m.%Y %H:%M')}"
            channel_name = next((name for name, cid in CHANNELS.items() if cid == channel_id), channel_id)
            access_list.append(f"📢 {channel_name} - {status}")
        
        await message.answer(
            "🔐 Ваши активные доступы:\n\n" + "\n".join(access_list) +
            "\n\nНажмите на кнопку канала в посте для получения ссылки"
        )
    else:
        await message.answer("📭 У вас нет активных доступов к каналам")

# Новые команды для отладки
@dp.message(Command("force_check"))
async def cmd_force_check(message: Message):
    """Принудительная проверка доступов"""
    if message.from_user.id != ADMIN_ID:
        return
        
    await check_expired_access()
    await message.answer("🔍 Принудительная проверка выполнена!")

@dp.message(Command("debug_time"))
async def cmd_debug_time(message: Message):
    """Показать текущее время сервера"""
    now = datetime.now()
    await message.answer(
        f"⏰ Время сервера: {now}\n"
        f"📅 Дата: {now.date()}\n"
        f"🕒 Время: {now.time()}\n"
        f"📊 Channel access: {len(channel_access)} пользователей"
    )

@dp.message(Command("debug_access"))
async def cmd_debug_access(message: Message):
    """Показать все доступы"""
    if message.from_user.id != ADMIN_ID:
        return
        
    debug_info = []
    for user_id, channels in channel_access.items():
        for channel_id, expiry in channels.items():
            debug_info.append(f"👤 {user_id} -> 📢 {channel_id} -> ⏰ {expiry}")
    
    if debug_info:
        await message.answer("\n".join(debug_info)[:4000])
    else:
        await message.answer("📭 Нет активных доступов")

@dp.message(Command("reload"))
async def cmd_reload(message: Message):
    """Принудительная перезагрузка данных из Google Sheets"""
    if message.from_user.id != ADMIN_ID:
        return
        
    await reload_channel_access()
    await message.answer("✅ Данные перезагружены из Google Sheets!")

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
        
        # Находим file_id по short_id
        file_id = file_id_mapping.get(short_id)
        if not file_id:
            await callback.answer("❌ Файл не найден")
            return
        
        # Проверяем, есть ли уже доступ к файлу
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
        payment_url = generate_file_payment_link(callback.from_user.id, file_id, int(price), "Файл")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 Оплатить {price}₽", url=payment_url)]
        ])
        
        await callback.message.answer(
            f"📦 Для получения файла необходимо оплатить {price}₽\n"
            f"После оплаты файл будет доступен для скачивания",
            reply_markup=keyboard
        )
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка обработки покупки файла: {e}")
        await callback.answer("❌ Ошибка при обработке запроса")

@dp.callback_query(F.data.startswith("buy_channel:"))
async def buy_channel_callback(callback: types.CallbackQuery):
    """Обработчик покупки доступа к каналу"""
    try:
        parts = callback.data.split(':')
        if len(parts) < 4:
            await callback.answer("❌ Ошибка формата кнопки")
            return
            
        channel_id = parts[1]
        price = parts[2]
        days = int(parts[3])
        user_id = str(callback.from_user.id)
        
        # Проверяем, есть ли уже доступ
        if user_id in channel_access and channel_id in channel_access[user_id]:
            expiry = channel_access[user_id][channel_id]
            if expiry == "forever" or (isinstance(expiry, datetime) and datetime.now() < expiry):
                # Обновляем ссылку
                invite_link = await grant_channel_access(callback.from_user.id, channel_id, days)
                await callback.message.answer(
                    f"✅ У вас уже есть доступ к каналу!\n"
                    f"Новая ссылка: {invite_link}"
                )
                await callback.answer()
                return
        
        # Предлагаем оплатить
        payment_url = generate_channel_payment_link(callback.from_user.id, channel_id, int(price), days)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 Оплатить {price}₽", url=payment_url)]
        ])
        
        period = "навсегда" if days == 0 else f"{days} дней"
        await callback.message.answer(
            f"🔐 Для доступа к каналу необходимо оплатить {price}₽\n"
            f"Доступ предоставляется на {period}\n"
            f"После оплаты вы получите ссылку для входа",
            reply_markup=keyboard
        )
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка обработки покупки канала: {e}")
        await callback.answer("❌ Ошибка при обработке запроса")

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
            await state.update_data(buttons="нет")
            await process_final_post(callback.message, state)
        else:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📁 Продаваемый файл", callback_data="button_type_file")],
                [InlineKeyboardButton(text="🔐 Приглашение в канал", callback_data="button_type_channel")],
                [InlineKeyboardButton(text="🔗 Обычная ссылка", callback_data="button_type_url")],
                [InlineKeyboardButton(text="✅ Готово", callback_data="button_type_done")]
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
            await state.set_state(PostStates.waiting_button_channel)
            await message.answer("🔗 Введите ID канала (например: -1002681575953):")
        elif btn_type == "url":
            await state.set_state(PostStates.waiting_button_url)
            await message.answer("🔗 Введите URL:")
            
    except Exception as e:
        logger.error(f"Ошибка цены: {e}")
        await message.answer("❌ Ошибка")

@dp.message(PostStates.waiting_button_channel)
async def process_button_channel(message: Message, state: FSMContext):
    try:
        channel_id = message.text.strip()
        if not channel_id.startswith('-100'):
            await message.answer("⚠️ ID канала обычно начинается с -100...\nНо продолжаем...")
        
        await state.update_data(current_button_channel=channel_id)
        await state.set_state(PostStates.waiting_button_days)
        await message.answer("📅 Введите количество дней доступа (0 для бессрочного):")
            
    except Exception as e:
        logger.error(f"Ошибка ID канала: {e}")
        await message.answer("❌ Ошибка")

@dp.message(PostStates.waiting_button_days)
async def process_button_days(message: Message, state: FSMContext):
    try:
        days_str = message.text.strip()
        if not days_str.isdigit():
            await message.answer("❌ Введите число дней (0 для бессрочного):")
            return
            
        days = int(days_str)
        
        data = await state.get_data()
        buttons_data = data.get("buttons_data", [])
        text = data.get("current_button_text")
        price = data.get("current_button_price")
        channel_id = data.get("current_button_channel")
        
        buttons_data.append(f"channel|{text}|{price}|{channel_id}|{days}")
        await state.update_data(buttons_data=buttons_data)
        
        await offer_more_buttons(message, state)
            
    except Exception as e:
        logger.error(f"Ошибка дней: {e}")
        await message.answer("❌ Ошибка")

@dp.message(PostStates.waiting_button_file)
async def process_button_file(message: Message, state: FSMContext):
    try:
        if not (message.document or message.photo):
            await message.answer("❌ Отправьте файл или фото:")
            return
            
        file_id = message.document.file_id if message.document else message.photo[-1].file_id
        await state.update_data(current_button_file=file_id)
        
        data = await state.get_data()
        buttons_data = data.get("buttons_data", [])
        text = data.get("current_button_text")
        price = data.get("current_button_price")
        file_id = data.get("current_button_file")
        
        short_id = hash(file_id) % 10000
        file_id_mapping[str(short_id)] = file_id
        
        buttons_data.append(f"file|{text}|{price}|{short_id}")
        await state.update_data(buttons_data=buttons_data)
        
        await offer_more_buttons(message, state)
            
    except Exception as e:
        logger.error(f"Ошибка файла: {e}")
        await message.answer("❌ Ошибка")

@dp.message(PostStates.waiting_button_url)
async def process_button_url(message: Message, state: FSMContext):
    try:
        url = message.text.strip()
        if not (url.startswith('http://') or url.startswith('https://')):
            await message.answer("❌ URL должен начинаться с http:// или https://")
            return
        
        data = await state.get_data()
        buttons_data = data.get("buttons_data", [])
        text = data.get("current_button_text")
        
        buttons_data.append(f"url|{text}|{url}")
        await state.update_data(buttons_data=buttons_data)
        
        await offer_more_buttons(message, state)
            
    except Exception as e:
        logger.error(f"Ошибка URL: {e}")
        await message.answer("❌ Ошибка")

async def offer_more_buttons(message: Message, state: FSMContext):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📁 Продаваемый файл", callback_data="button_type_file")],
        [InlineKeyboardButton(text="🔐 Приглашение в канал", callback_data="button_type_channel")],
        [InlineKeyboardButton(text="🔗 Обычная ссылка", callback_data="button_type_url")],
        [InlineKeyboardButton(text="✅ Готово", callback_data="button_type_done")]
    ])
    await state.set_state(PostStates.waiting_button_type)
    await message.answer("🎛 Добавить еще кнопку или завершить?", reply_markup=keyboard)

@dp.callback_query(PostStates.waiting_button_type, F.data == "button_type_done")
async def process_buttons_done(callback: types.CallbackQuery, state: FSMContext):
    await process_final_post(callback.message, state)
    await callback.answer()

async def process_final_post(message: Message, state: FSMContext):
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
            
            buttons_str = "|".join(buttons_data) if buttons_data else "нет"
            ws.append_row(["", "", "", "", "", post_id, text, photo_id, buttons_str, ""])
            keyboard = create_buttons_keyboard(buttons_str)
            
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

# === Универсальный вебхук для всех платежей ===
@app.post("/webhook")
async def universal_webhook(request: Request):
    try:
        logger.info("=== ПОЛУЧЕН ВЕБХУК ОТ PRODAMUS ===")
        
        form_data = await request.form()
        data = dict(form_data)
        
        logger.info(f"Данные вебхука: {data}")
        
        if data.get('payment_status') != 'success':
            logger.warning(f"Платеж не успешен: {data.get('payment_status')}")
            return {"status": "error", "message": "Payment not successful"}
        
        payment_type, user_id, target_id, days = extract_payment_info(data)
        
        logger.info(f"Извлечено: type={payment_type}, user_id={user_id}, target_id={target_id}, days={days}")
        
        if payment_type == "file":
            if user_id not in paid_files:
                paid_files[user_id] = {}
            paid_files[user_id][target_id] = "forever"
            save_data()
            
            await bot.send_message(user_id, "✅ Оплата файла прошла успешно! Вот ваш файл:")
            await send_file_to_user(user_id, target_id, "✅ Ваш файл")
            
            await bot.send_message(
                ADMIN_ID,
                f"💰 Пользователь {user_id} оплатил файл\n"
                f"📁 File ID: {target_id}\n"
                f"💳 Сумма: {data.get('amount', 'N/A')}₽"
            )
            
        elif payment_type == "channel":
            invite_link = await grant_channel_access(int(user_id), target_id, days)
            
            period = "навсегда" if days == 0 else f"{days} дней"
            await bot.send_message(
                user_id,
                f"✅ Оплата доступа к каналу прошла успешно! Доступ предоставлен на {period}.\n"
                f"Вот ваша ссылка для входа: {invite_link}"
            )
            
            await bot.send_message(
                ADMIN_ID,
                f"💰 Пользователь {user_id} оплатил доступ к каналу\n"
                f"📢 Канал: {target_id}\n"
                f"⏰ Срок: {period}\n"
                f"💳 Сумма: {data.get('amount', 'N/A')}₽"
            )
        
        return {"status": "success"}
        
    except Exception as e:
        logger.error(f"Ошибка вебхука: {e}", exc_info=True)
        await bot.send_message(ADMIN_ID, f"🚨 Ошибка вебхука: {e}\n\nДанные: {data}")
        return {"status": "error", "message": str(e)}

# === Webhook настройки ===
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}{WEBHOOK_PATH}"

@app.on_event("startup")
async def startup():
    if os.getenv("RENDER"):
        await bot.set_webhook(WEBHOOK_URL)
        logger.info(f"Webhook установлен: {WEBHOOK_URL}")
    
    load_data()
    threading.Thread(target=access_watcher, daemon=True).start()
    logger.info("Бот запущен!")

@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    update = types.Update(**data)
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.get("/")
async def health_check():
    return {"status": "ok", "sheets": bool(ws), "paid_files_count": len(paid_files), "channel_access_count": len(channel_access)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
