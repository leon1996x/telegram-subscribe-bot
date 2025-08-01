import os
import time
import threading
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
import uvicorn
from telebot import TeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import hmac
import hashlib

# === CONFIG ===
TOKEN = os.getenv("BOT_TOKEN")  # токен Telegram
PRODAMUS_SECRET = os.getenv("PRODAMUS_SECRET")  # секретный ключ Prodamus
PAYFORM_URL = "https://menyayrealnost.payform.ru"
CHANNEL_ID = -1002681575953  # ID твоего канала
PRICE = 890  # цена (₽)
ACCESS_DAYS = 1  # 1 день для теста

bot = TeleBot(TOKEN)
app = FastAPI()

# Хранилище пользователей (в проде лучше Redis/БД)
active_users = {}


# === Функция создания ссылки оплаты ===
def generate_payment_link(user_id):
    params = {
        "do": "pay",
        "products[0][name]": "Доступ в канал Меняя реальность",
        "products[0][price]": PRICE,
        "products[0][quantity]": 1,
        "order_id": str(user_id),
        "customer_extra": f"Оплата от пользователя {user_id}"
    }
    query = "&".join([f"{k}={v}" for k, v in params.items()])
    return f"{PAYFORM_URL}/?{query}"


# === Проверка подписи от Prodamus ===
def verify_signature(data: dict, signature: str):
    # Сортируем данные по ключу
    sorted_items = sorted(data.items())
    msg = "".join(f"{k}={v}" for k, v in sorted_items)

    digest = hmac.new(PRODAMUS_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return digest == signature


# === Webhook для Prodamus ===
@app.post("/webhook")
async def webhook(request: Request):
    form = await request.form()
    data = dict(form)
    signature = request.headers.get("Sign")

    if not verify_signature(data, signature):
        return {"status": "invalid signature"}

    user_id = int(data.get("order_id"))

    # Добавляем пользователя в канал
    try:
        bot.unban_chat_member(CHANNEL_ID, user_id)
        bot.invite_link = None
        bot.send_message(user_id, "Оплата прошла успешно! Доступ на 1 день открыт.")
        bot.add_chat_member(CHANNEL_ID, user_id)  # если не сработает — высылаем инвайт
    except Exception:
        invite_link = bot.create_chat_invite_link(CHANNEL_ID, expire_date=None).invite_link
        bot.send_message(user_id, f"Оплата успешна! Вот ссылка: {invite_link}")

    # Сохраняем дату окончания подписки
    expire_time = datetime.now() + timedelta(days=ACCESS_DAYS)
    active_users[user_id] = expire_time

    return {"status": "success"}


# === Фоновая задача — удаляет просроченных ===
def remove_expired_users():
    while True:
        now = datetime.now()
        to_remove = [uid for uid, exp in active_users.items() if now > exp]
        for uid in to_remove:
            try:
                bot.ban_chat_member(CHANNEL_ID, uid)
                bot.unban_chat_member(CHANNEL_ID, uid)
                bot.send_message(uid, "Подписка закончилась. Чтобы продлить — оплатите снова.")
                del active_users[uid]
            except Exception:
                pass
        time.sleep(60)


threading.Thread(target=remove_expired_users, daemon=True).start()


# === Команда /start ===
@bot.message_handler(commands=["start"])
def start(message):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(f"Оплатить {PRICE}₽ / месяц", url=generate_payment_link(message.from_user.id)))
    bot.send_message(message.chat.id, "Привет! Оплати подписку, чтобы попасть в канал:", reply_markup=markup)


# === Запуск бота ===
def run_bot():
    bot.infinity_polling()


threading.Thread(target=run_bot, daemon=True).start()


# === Запуск API ===
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
