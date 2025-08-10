import os
import json
import threading
import time
import requests
from urllib.parse import unquote
from fastapi import FastAPI, Request
import telebot

# === CONFIG ===
TOKEN = os.getenv("BOT_TOKEN")
PAYFORM_URL = "https://menyayrealnost.payform.ru"
CHANNEL_ID = -1002681575953
PRICE = 1590
USERS_FILE = "users.json"
ADMIN_ID = 513148972
PING_URL = "https://telegram-subscribe-bot-5oh7.onrender.com"  # замени на свой адрес Render

bot = telebot.TeleBot(TOKEN)
app = FastAPI()

active_users = set()


# === Загрузка/сохранение ===
def load_users():
    global active_users
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r") as f:
                data = json.load(f)
                active_users = set(map(int, data))
        except:
            active_users = set()

def save_users():
    with open(USERS_FILE, "w") as f:
        json.dump(list(active_users), f)


# === Оплата ===
def generate_payment_link(user_id: int):
    params = {
        "do": "pay",
        "products[0][name]": "Оплата за гайд <<Меняя реальность>>",
        "products[0][price]": PRICE,
        "products[0][quantity]": 1,
        "order_id": str(user_id),
        "customer_extra": f"Оплата от пользователя {user_id}"
    }
    query = "&".join([f"{k}={v}" for k, v in params.items()])
    return f"{PAYFORM_URL}/?{query}"


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    json_data = await request.json()
    update = telebot.types.Update.de_json(json_data)
    bot.process_new_updates([update])
    return {"ok": True}


@app.post("/webhook")
async def prodamus_webhook(request: Request):
    try:
        try:
            data = await request.json()
        except:
            form = await request.form()
            data = dict(form)

        raw_order = str(data.get("order_id", ""))
        customer_extra = unquote(str(data.get("customer_extra", "")))

        if raw_order.isdigit():
            user_id = int(raw_order)
        elif "пользователя" in customer_extra:
            user_id = int(customer_extra.split()[-1])
        else:
            bot.send_message(ADMIN_ID, f"[ALERT] Не удалось определить user_id: {data}")
            return {"status": "error", "message": "Не удалось определить user_id"}

        bot.unban_chat_member(CHANNEL_ID, user_id)
        invite = bot.create_chat_invite_link(chat_id=CHANNEL_ID, expire_date=None, member_limit=1)

        bot.send_message(user_id, f"✅ Оплата прошла успешно!\n\nВот ваша ссылка:\n{invite.invite_link}")
        bot.send_message(ADMIN_ID, f"💰 Оплатил пользователь {user_id}. Ссылка выдана.")

        active_users.add(user_id)
        save_users()

        return {"status": "success"}

    except Exception as e:
        bot.send_message(ADMIN_ID, f"[ALERT] Ошибка вебхука: {e}")
        return {"status": "error", "message": str(e)}


@bot.message_handler(commands=["start"])
def start(message):
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton(
            f"Оплатить {PRICE}₽", url=generate_payment_link(message.from_user.id)
        )
    )
    bot.send_message(
        message.chat.id,
        f"Привет! 👋\n"
        f"Чтобы получить доступ к каналу <<Меняя реальность>>, оплатите {PRICE}₽.\n"
        f"Единоразовая оплата — доступ навсегда.\n\n"
        f"Ваш ID: {message.from_user.id}",
        reply_markup=markup
    )


@app.get("/")
async def home():
    return {"status": "Bot is running!"}


# === Пинг каждые 2 минуты ===
def ping_self():
    while True:
        try:
            requests.get(PING_URL, timeout=10)
        except:
            pass
        time.sleep(120)  # 2 минуты


# === Запуск ===
load_users()
threading.Thread(target=ping_self, daemon=True).start()
