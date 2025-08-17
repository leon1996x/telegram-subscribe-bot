import os
import logging
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO)

app = FastAPI()

# --- Настройки ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7145469393"))  # ID админа
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Google Sheets ---
scope = ["https://www.googleapis.com/auth/spreadsheets"]
creds = Credentials.from_service_account_file("credentials.json", scopes=scope)
client = gspread.authorize(creds)
worksheet = client.open("BotData").worksheet("BotData")

# --- Клавиатуры ---
def admin_keyboard():
    kb = ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(KeyboardButton("➕ Добавить пост"))
    return kb


def delete_keyboard(post_id: str):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("🗑 Удалить", callback_data=f"delete:{post_id}"))
    return kb


@app.get("/")
async def root():
    return {"status": "ok"}


@app.post(f"/webhook/{BOT_TOKEN}")
async def telegram_webhook(request: Request):
    update = await request.json()
    logging.info(update)

    if "message" in update:
        message = update["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "")

        # --- Админ ---
        if chat_id == ADMIN_ID:
            if text == "/admin":
                await bot.send_message(chat_id, "Админ панель", reply_markup=admin_keyboard())
            elif text == "➕ Добавить пост":
                await bot.send_message(chat_id, "Пришли текст или фото для поста")
                worksheet.update("D2", "waiting_post")  # флаг что ждём пост
            else:
                # Проверяем, ждём ли мы пост
                flag = worksheet.acell("D2").value
                if flag == "waiting_post":
                    worksheet.update("D2", "")  # сброс
                    post_id = str(message["message_id"])

                    # Рассылка
                    users = worksheet.col_values(1)[1:]  # все chat_id
                    for uid in users:
                        try:
                            if "photo" in message:
                                file_id = message["photo"][-1]["file_id"]
                                await bot.send_photo(uid, file_id, caption=text)
                            else:
                                await bot.send_message(uid, text)
                        except Exception as e:
                            logging.warning(f"Не смог отправить {uid}: {e}")

                    # Отправляем админу с кнопкой удалить
                    if "photo" in message:
                        file_id = message["photo"][-1]["file_id"]
                        await bot.send_photo(chat_id, file_id, caption=text, reply_markup=delete_keyboard(post_id))
                    else:
                        await bot.send_message(chat_id, text, reply_markup=delete_keyboard(post_id))

        # --- Пользователи ---
        else:
            if text == "/start":
                ids = worksheet.col_values(1)
                if str(chat_id) not in ids:
                    worksheet.append_row([str(chat_id)])
                await bot.send_message(chat_id, "Ты подписан и будешь получать посты")

    elif "callback_query" in update:
        cq = update["callback_query"]
        chat_id = cq["from"]["id"]
        data = cq["data"]

        if chat_id == ADMIN_ID and data.startswith("delete:"):
            msg_id = cq["message"]["message_id"]
            await bot.delete_message(chat_id, msg_id)
            await bot.answer_callback_query(cq["id"], "Пост удалён")

    return {"ok": True}

