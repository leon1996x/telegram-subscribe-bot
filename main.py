import os
import logging
import gspread
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.utils import executor
from oauth2client.service_account import ServiceAccountCredentials

logging.basicConfig(level=logging.INFO)

# --- НАСТРОЙКИ ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 7145469393  # твой телеграм id
SPREADSHEET_NAME = "MyBotData"  # название Google-таблицы

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# --- GOOGLE SHEETS ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("google_credentials.json", scope)
client = gspread.authorize(creds)
sheet = client.open(SPREADSHEET_NAME).sheet1

# --- КНОПКИ ---
def admin_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("📋 Посмотреть данные"))
    kb.add(KeyboardButton("➕ Добавить запись"))
    kb.add(KeyboardButton("❌ Удалить запись"))
    kb.add(KeyboardButton("🚪 Выйти"))
    return kb

# --- КОМАНДА /admin ---
@dp.message_handler(commands=["admin"])
async def cmd_admin(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer("🔑 Админ-панель:", reply_markup=admin_keyboard())
    else:
        await message.answer("⛔ У вас нет доступа!")

# --- ОБРАБОТКА КНОПОК ---
@dp.message_handler(lambda message: message.text in ["📋 Посмотреть данные", "➕ Добавить запись", "❌ Удалить запись", "🚪 Выйти"])
async def handle_admin_buttons(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет доступа!")
        return
    
    if message.text == "📋 Посмотреть данные":
        data = sheet.get_all_values()
        if not data:
            await message.answer("Таблица пуста 📭")
        else:
            text = "\n".join([f"{i+1}. {row[0]}" for i, row in enumerate(data)])
            await message.answer(f"📋 Данные:\n{text}")

    elif message.text == "➕ Добавить запись":
        await message.answer("✍️ Введите текст новой записи:")
        dp.register_message_handler(add_record, state="add_record")

    elif message.text == "❌ Удалить запись":
        await message.answer("Введите номер строки для удаления:")
        dp.register_message_handler(delete_record, state="delete_record")

    elif message.text == "🚪 Выйти":
        await message.answer("Вы вышли из админки.", reply_markup=ReplyKeyboardRemove())

# --- ДОБАВЛЕНИЕ ---
async def add_record(message: types.Message):
    sheet.append_row([message.text])
    await message.answer("✅ Запись добавлена!", reply_markup=admin_keyboard())
    dp.unregister_message_handler(add_record, state="add_record")

# --- УДАЛЕНИЕ ---
async def delete_record(message: types.Message):
    try:
        row = int(message.text)
        sheet.delete_rows(row)
        await message.answer(f"✅ Запись {row} удалена!", reply_markup=admin_keyboard())
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
    dp.unregister_message_handler(delete_record, state="delete_record")

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
