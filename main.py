import os
import json
from datetime import datetime
from urllib.parse import unquote
from fastapi import FastAPI, Request
import telebot

# === CONFIG ===
TOKEN = os.getenv("BOT_TOKEN")  # токен бота
PAYFORM_URL = "https://menyayrealnost.payform.ru"
CHANNEL_ID = -1002681575953      # ID твоего канала
PRICE = 1590                     # цена (навсегда)
ADMIN_ID = 513148972              # твой Telegram ID

bot = telebot.TeleBot(TOKEN)
app = FastAPI()

# Хранилище пользователей (кто оплатил)
USERS_FILE = "users.json"
paid_users = set()

# === Загрузка пользователей ===
def load_users():
    global paid_users
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r") as f:
                paid_users = set(json.load(f))
        except:
            paid_users = set()

def save_users():
    with open(USERS_FILE, "w") as f:
        json.dump(list(paid_users), f)

# === Генерация ссылки на оплату ===
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

# === Telegram webhook ===
@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    json_data = await request.json()
    update = telebot.types.Update.de_json(json_data)
    bot.process_new_updates([update])
    return {"ok": True}

# === Prodamus webhook ===
@app.post("/webhook")
async def prodamus_webhook(request: Request):
    try:
        # Читаем JSON или form-data
        try:
            data = await request.json()
        except:
            form = await request.form()
            data = dict(form)

        # Декодируем
        raw_order = str(data.get("order_id", "")).strip()
        customer_extra = unquote(str(data.get("customer_extra", "")).strip())

        # Определяем user_id
        user_id = None
        if raw_order.isdigit():
            user_id = int(raw_order)
        else:
            # Ищем цифры в customer_extra
            import re
            match = re.search(r"\d{5,}", customer_extra)
            if match:
                user_id = int(match.group(0))

        if not user_id:
            bot.send_message(ADMIN_ID, f"[ALERT] Не удалось определить user_id: {data}")
            return {"status": "error", "message": "user_id not found"}

        # Если уже есть в списке — не выдаём повторно
        if user_id in paid_users:
            bot.send_message(user_id, "Вы уже получили доступ к каналу.")
            return {"status": "ok"}

        # Снимаем бан и выдаём ссылку
        bot.unban_chat_member(CHANNEL_ID, user_id)
        invite = bot.create_chat_invite_link(
            chat_id=CHANNEL_ID,
            expire_date=None,
            member_limit=1
        )

        bot.send_message(user_id, f"✅ Оплата успешна! Вот ссылка для входа: {invite.invite_link}")
        bot.send_message(ADMIN_ID, f"Оплатил пользователь {user_id}. Ссылка выдана.")

        paid_users.add(user_id)
        save_users()

        return {"status": "success"}

    except Exception as e:
        bot.send_message(ADMIN_ID, f"[ALERT] Ошибка вебхука: {e}")
        return {"status": "error", "message": str(e)}

# === /start ===
@bot.message_handler(commands=["start"])
def start(message):
    if message.from_user.id in paid_users:
        bot.send_message(message.chat.id, "У вас уже есть доступ к каналу.")
        return

    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton(
            f"Оплатить {PRICE}₽ (навсегда)", url=generate_payment_link(message.from_user.id)
        )
    )
    bot.send_message(
        message.chat.id,
        f"Привет! Оплати {PRICE}₽, чтобы получить доступ в канал <<Меняя реальность>>.\n"
        f"Доступ даётся навсегда.",
        reply_markup=markup
    )

# === Пингер ===
@app.get("/")
async def home():
    return {"status": "Bot is running!"}

# === Запуск ===
load_users()
