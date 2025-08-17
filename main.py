import asyncio
import logging
import gspread

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

# --- НАСТРОЙКИ ---
BOT_TOKEN = "ТВОЙ_ТОКЕН"
ADMIN_ID = 123456789   # твой id
GSHEET_KEY = "ТВОЙ_КЛЮЧ_ОТ_ТАБЛИЦЫ"  # ключ от Google Sheets

logging.basicConfig(level=logging.INFO)

# --- Telegram bot ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Google Sheets ---
gc = gspread.service_account(filename="creds.json")  # creds.json нужно загрузить в проект
sh = gc.open_by_key(GSHEET_KEY)
worksheet = sh.sheet1

# --- КНОПКИ ---
def admin_keyboard():
    kb = [
        [KeyboardButton(text="📋 Посмотреть данные")],
        [KeyboardButton(text="➕ Добавить запись")],
        [KeyboardButton(text="❌ Удалить запись")],
        [KeyboardButton(text="🚪 Выйти")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- КОМАНДА /start ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Привет 👋 Это бот с Google Sheets!")

# --- КОМАНДА /admin ---
@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer("🔑 Админ-панель:", reply_markup=admin_keyboard())
    else:
        await message.answer("⛔ У вас нет доступа!")

# --- ОБРАБОТКА КНОПОК ---
@dp.message(F.text.in_(["📋 Посмотреть данные", "➕ Добавить запись", "❌ Удалить запись", "🚪 Выйти"]))
async def handle_admin_buttons(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет доступа!")
        return

    if message.text == "📋 Посмотреть данные":
        rows = worksheet.get_all_values()
        text = "\n".join([", ".join(row) for row in rows]) if rows else "📂 Таблица пуста"
        await message.answer(f"Данные из таблицы:\n\n{text}")

    elif message.text == "➕ Добавить запись":
        worksheet.append_row(["Новая запись"])
        await message.answer("✅ Запись добавлена!")

    elif message.text == "❌ Удалить запись":
        if len(worksheet.get_all_values()) > 1:
            worksheet.delete_rows(len(worksheet.get_all_values()))
            await message.answer("❌ Последняя запись удалена")
        else:
            await message.answer("⚠️ Удалять нечего")

    elif message.text == "🚪 Выйти":
        await message.answer("Вы вышли из админки.", reply_markup=ReplyKeyboardRemove())

# --- ЗАПУСК ---
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

