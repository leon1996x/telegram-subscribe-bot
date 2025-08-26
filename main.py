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

# === Функции для работы с Google Sheets ===
def get_gsheet():
    """Подключается к Google Sheets и возвращает worksheet"""
    try:
        creds_path = '/etc/secrets/GSPREAD_CREDENTIALS.json'
        creds = Credentials.from_service_account_file(creds_path, scopes=[
            "https://www.googleapis.com/auth/spreadsheets"
        ])
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(GSHEET_ID)
        return sh.sheet1
    except Exception as e:
        logger.error(f"Ошибка подключения к Google Sheets: {e}")
        return None

async def save_channel_access(user_id: int, channel_id: str, days: int):
    """Сохраняет доступ к каналу в Google Sheets"""
    ws = get_gsheet()
    if not ws:
        return None
    
    try:
        # Вычисляем дату истечения
        if days == 0:
            expiry_date = "forever"
        else:
            expiry_date = (datetime.now() + timedelta(days=days)).isoformat()
        
        # Формат: channel_id|expiry_date
        access_data = f"{channel_id}|{expiry_date}"
        
        # Ищем пользователя в таблице
        records = ws.get_all_records()
        row_index = 2  # Начинаем со 2 строки (после заголовков)
        
        for record in records:
            if str(record.get("id", "")) == str(user_id):
                # Обновляем колонку L (channel_access)
                ws.update(f'L{row_index}', [[access_data]])
                logger.info(f"Доступ сохранен в Google Sheets: user {user_id} -> {access_data}")
                return expiry_date
            row_index += 1
        
        # Если пользователь не найден, добавляем новую строку
        new_row = [user_id, "", "", "", "", "", "", "", "", "", "", access_data]
        ws.append_row(new_row)
        logger.info(f"Добавлен новый пользователь с доступом: {access_data}")
        return expiry_date
        
    except Exception as e:
        logger.error(f"Ошибка сохранения доступа в Google Sheets: {e}")
        return None

async def check_expired_access_gsheets():
    """Проверяет просроченные доступы из Google Sheets"""
    ws = get_gsheet()
    if not ws:
        return
    
    now = datetime.now()
    logger.info(f"[WATCHER] Проверка доступов из Google Sheets в {now}")
    
    try:
        records = ws.get_all_records()
        row_index = 2
        
        for record in records:
            user_id = str(record.get("id", ""))
            access_data = record.get("channel_access", "").strip()
            
            if access_data and "|" in access_data:
                channel_id, expiry_str = access_data.split("|", 1)
                
                if expiry_str != "forever":
                    try:
                        expiry_date = datetime.fromisoformat(expiry_str)
                        if now >= expiry_date:
                            # УДАЛЯЕМ ПОЛЬЗОВАТЕЛЯ
                            await bot.ban_chat_member(int(channel_id), int(user_id))
                            await bot.unban_chat_member(int(channel_id), int(user_id))
                            
                            # Очищаем запись в таблице
                            ws.update(f'L{row_index}', [[""]])
                            
                            # Уведомляем пользователя
                            await bot.send_message(int(user_id), "⏰ Срок вашего доступа к каналу истёк. Для продления оплатите подписку снова.")
                            
                            logger.info(f"Удален user {user_id} из channel {channel_id}")
                    except ValueError:
                        logger.error(f"Неверный формат даты: {expiry_str}")
            
            row_index += 1
        
        logger.info("Проверка доступов завершена")
        
    except Exception as e:
        logger.error(f"Ошибка проверки доступов из Google Sheets: {e}")

# === Фоновая проверка ===
def access_watcher():
    logger.info("[WATCHER] Запущен мониторинг доступов из Google Sheets")
    while True:
        try:
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(check_expired_access_gsheets())
            loop.close()
        except Exception as e:
            logger.error(f"Ошибка в мониторинге доступов: {e}")
        time.sleep(60)

# === Загрузка/сохранение данных ===
def load_data():
    global paid_files
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

def save_data():
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
        # Проверяем, не является ли пользователь владельцем/админом канала
        try:
            chat_member = await bot.get_chat_member(int(channel_id), user_id)
            if chat_member.status in ['creator', 'administrator']:
                logger.info(f"Пользователь {user_id} уже является админом канала {channel_id}")
        except:
            pass  # Если не можем получить информацию о пользователе
        
        # Пытаемся разбанить (если пользователь не админ)
        try:
            await bot.unban_chat_member(int(channel_id), user_id)
        except Exception as e:
            logger.info(f"Не удалось разбанить пользователя {user_id}: {e}")
        
        # Создаем одноразовую ссылку
        invite = await bot.create_chat_invite_link(
            chat_id=int(channel_id),
            expire_date=None,
            member_limit=1
        )
        
        # Сохраняем доступ в Google Sheets
        expiry_date = await save_channel_access(user_id, channel_id, days)
        
        return invite.invite_link
        
    except Exception as e:
        logger.error(f"Ошибка предоставления доступа к каналу: {e}")
        raise

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
    ws = get_gsheet()
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
                "",  # button_type
                "",  # button_data
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
        ws = get_gsheet()
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
    """Показать активные доступы пользователя из Google Sheets"""
    user_id = str(message.from_user.id)
    ws = get_gsheet()
    
    if not ws:
        await message.answer("❌ База данных недоступна")
        return
    
    try:
        records = ws.get_all_records()
        access_list = []
        
        for record in records:
            if str(record.get("id", "")) == user_id:
                access_data = record.get("channel_access", "").strip()
                if access_data and "|" in access_data:
                    channel_id, expiry_str = access_data.split("|", 1)
                    if expiry_str == "forever":
                        status = "✅ Бессрочный"
                    else:
                        try:
                            expiry_date = datetime.fromisoformat(expiry_str)
                            status = f"⏰ До {expiry_date.strftime('%d.%m.%Y %H:%M')}"
                        except:
                            status = "❌ Ошибка даты"
                    
                    access_list.append(f"📢 {channel_id} - {status}")
        
        if access_list:
            await message.answer(
                "🔐 Ваши активные доступы:\n\n" + "\n".join(access_list) +
                "\n\nНажмите на кнопку канала в посте для получения ссылки"
            )
        else:
            await message.answer("📭 У вас нет активных доступов к каналам")
            
    except Exception as e:
        logger.error(f"Ошибка получения доступов: {e}")
        await message.answer("❌ Ошибка получения информации о доступах")

@dp.message(Command("check_access"))
async def cmd_check_access(message: Message):
    """Принудительная проверка просроченных доступов"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("🚫 Доступ запрещен")
        return
    
    try:
        await check_expired_access_gsheets()
        await message.answer("✅ Проверка доступов выполнена. Результаты в логах.")
    except Exception as e:
        await message.answer(f"❌ Ошибка проверки: {e}")

@dp.message(Command("show_access"))
async def cmd_show_access(message: Message):
    """Показать все доступы из Google Sheets"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("🚫 Доступ запрещен")
        return
    
    ws = get_gsheet()
    if not ws:
        await message.answer("❌ База данных недоступна")
        return
    
    try:
        records = ws.get_all_records()
        access_list = []
        
        for record in records:
            user_id = str(record.get("id", ""))
            access_data = record.get("channel_access", "").strip()
            
            if access_data and "|" in access_data:
                channel_id, expiry_str = access_data.split("|", 1)
                access_list.append(f"👤 {user_id} → 📢 {channel_id} → ⏰ {expiry_str}")
        
        if access_list:
            await message.answer("🔐 Все доступы из Google Sheets:\n\n" + "\n".join(access_list))
        else:
            await message.answer("📭 Нет активных доступов")
            
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

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
        
        # Проверяем, есть ли уже доступ в Google Sheets
        ws = get_gsheet()
        if ws:
            records = ws.get_all_records()
            for record in records:
                if str(record.get("id", "")) == user_id:
                    access_data = record.get("channel_access", "").strip()
                    if access_data and "|" in access_data:
                        existing_channel, expiry_str = access_data.split("|", 1)
                        if existing_channel == channel_id:
                            if expiry_str == "forever" or (expiry_str != "forever" and datetime.now() < datetime.fromisoformat(expiry_str)):
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

# ... (остальные обработчики остаются без изменений) ...

# === Универсальный вебхук для всех платежей ===
@app.post("/webhook")
async def universal_webhook(request: Request):
    """Обрабатывает все типы платежей"""
    try:
        logger.info("=== ПОЛУЧЕН ВЕБХУК ОТ PRODAMUS ===")
        
        # Получаем данные из формы
        form_data = await request.form()
        data = dict(form_data)
        
        logger.info(f"Данные вебхука: {data}")
        
        # Проверяем статус оплаты
        if data.get('payment_status') != 'success':
            logger.warning(f"Платеж не успешен: {data.get('payment_status')}")
            return {"status": "error", "message": "Payment not successful"}
        
        # Извлекаем информацию о платеже
        payment_type, user_id, target_id, days = extract_payment_info(data)
        
        logger.info(f"Извлечено: type={payment_type}, user_id={user_id}, target_id={target_id}, days={days}")
        
        if payment_type == "file":
            # Обработка оплаты файла
            if user_id not in paid_files:
                paid_files[user_id] = {}
            paid_files[user_id][target_id] = "forever"
            save_data()
            
            # Отправляем файл
            await bot.send_message(user_id, "✅ Оплата файла прошла успешно! Вот ваш файл:")
            await send_file_to_user(user_id, target_id, "✅ Ваш файл")
            
            # Уведомляем админа
            await bot.send_message(
                ADMIN_ID,
                f"💰 Пользователь {user_id} оплатил файл\n"
                f"📁 File ID: {target_id}\n"
                f"💳 Сумма: {data.get('amount', 'N/A')}₽"
            )
            
        elif payment_type == "channel":
            # Обработка оплаты доступа к каналу
            try:
                invite_link = await grant_channel_access(int(user_id), target_id, days)
                
                # Отправляем ссылку
                period = "навсегда" if days == 0 else f"{days} дней"
                await bot.send_message(
                    user_id,
                    f"✅ Оплата доступа к каналу прошла успешно! Доступ предоставлен на {period}.\n"
                    f"Вот ваша ссылка для входа: {invite_link}"
                )
                
                # Уведомляем админа
                await bot.send_message(
                    ADMIN_ID,
                    f"💰 Пользователь {user_id} оплатил доступ к каналу\n"
                    f"📢 Канал: {target_id}\n"
                    f"⏰ Срок: {period}\n"
                    f"💳 Сумма: {data.get('amount', 'N/A')}₽"
                )
                
            except Exception as e:
                # Если ошибка, но пользователь уже админ - все равно отправляем ссылку
                if "can't remove chat owner" in str(e) or "administrator" in str(e):
                    logger.info(f"Пользователь {user_id} уже админ канала {target_id}")
                    
                    # Все равно создаем ссылку
                    invite = await bot.create_chat_invite_link(
                        chat_id=int(target_id),
                        expire_date=None,
                        member_limit=1
                    )
                    
                    await bot.send_message(
                        user_id,
                        f"✅ Оплата принята! Вы уже являетесь администратором канала.\n"
                        f"Ссылка для входа: {invite.invite_link}"
                    )
                    
                    await bot.send_message(
                        ADMIN_ID,
                        f"💰 Администратор {user_id} оплатил доступ к каналу\n"
                        f"📢 Канал: {target_id}\n"
                        f"⏰ Срок: {days} дней\n"
                        f"💳 Сумма: {data.get('amount', 'N/A')}₽"
                    )
                else:
                    raise  # Другие ошибки пробрасываем дальше
        
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
    
    # Загружаем данные и запускаем мониторинг
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
    return {"status": "ok", "paid_files_count": len(paid_files)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
