import os
import hmac
import hashlib
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
import telebot

# === CONFIG ===
TOKEN = os.getenv("BOT_TOKEN")  # токен Telegram
PRODAMUS_SECRET = os.getenv("PRODAMUS_SECRET")  # секретный ключ Prodamus
PAYFORM_URL = "https://menyayrealnost.payform.ru"
CHANNEL_ID = -1002681575953  # ID канала
PRICE = 50  # цена ₽
ACCESS_DAYS = 1  # дней доступа

bot = telebot.TeleBot(TOKEN)
app = FastAPI()

# Хранилище активных пользователей (в проде — БД/Redis)
active_users = {}


# === Генерация ссылки на оплату ===
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
    sorted_items = sorted(data.items())
    msg = "".join(f"{k}={v}" for k, v in sorted_items)
    digest = hmac.new(PRODAMUS_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return digest == signature


# === Telegram webhook ===
@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    json_data = await request.json()
    update = telebot.types.Update.de_json(json_data)
    bot.process_new_updates([update])
    return {"ok": True}


# === Prodamus webhook (фикс 404 + поддержка JSON и form-data) ===
@app.post("/webhook")
async def prodamus_webhook(request: Request):
    try:
        # Пытаемся прочитать JSON, иначе form-data
        try:
            data = await request.json()
        except:
            form = await request.form()
            data = dict(form)

        # Проверка подписи
        signature = request.headers.get("Sign")
        if PRODAMUS_SECRET and signature:
            clean_signature = signature.replace("Sign: ", "")
            if not verify_signature(data, clean_signature):
                return {"status": "invalid signature"}

        # user_id из order_id или из customer_extra
        user_id = int(data.get("order_id") or data.get("customer_extra").split()[-1])

        # Создаем ссылку-приглашение с лимитом 1 использование
        try:
            bot.unban_chat_member(CHANNEL_ID, user_id)
            invite = bot.create_chat_invite_link(
                chat_id=CHANNEL_ID,
                expire_date=None,
                member_limit=1  # Одноразовая ссылка
            )
            bot.send_message(user_id, f"Оплата успешна! Вот ссылка для входа: {invite.invite_link}")
        except Exception:
            bot.send_message(user_id, "Оплата прошла, но не удалось добавить — напишите админу.")

        # Сохраняем дату окончания подписки
        active_users[user_id] = datetime.now() + timedelta(days=ACCESS_DAYS)

        return {"status": "success"}

    except Exception as e:
        return {"status": "error", "message": str(e)}


# === Команда /start ===
@bot.message_handler(commands=["start"])
def start(message):
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(
        telebot.types.InlineKeyboardButton(
            f"Оплатить {PRICE}₽ / месяц", url=generate_payment_link(message.from_user.id)
        )
    )
    bot.send_message(message.chat.id, "Привет! Оплати подписку, чтобы попасть в канал:", reply_markup=markup)


# === Корневой эндпоинт ===
@app.get("/")
async def home():
    return {"status": "Bot is running!"}
