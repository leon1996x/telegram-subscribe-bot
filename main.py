import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils import executor

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 7145469393  # 👉 сюда вставь свой телеграм id

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

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
        await message.answer("Тут будет просмотр данных 📋")
    elif message.text == "➕ Добавить запись":
        await message.answer("Тут будет добавление ➕")
    elif message.text == "❌ Удалить запись":
        await message.answer("Тут будет удаление ❌")
    elif message.text == "🚪 Выйти":
        await message.answer("Вы вышли из админки.", reply_markup=types.ReplyKeyboardRemove())

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
