import os
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
import telebot

# === CONFIG ===
TOKEN = os.getenv("BOT_TOKEN")  # токен Telegram
PAYFORM_URL = "https://menyayrealnost.payform.ru"
CHANNEL_ID = -1002681575953  # ID канала
PRICE = 50  # цена ₽
ACCESS_DAYS = 1  # дней доступа

bot = telebot.TeleBot(TOKEN)
app = FastAPI()

# Хранилище активных пользователей
active_users = {}


# === Генерация ссылки на оплату ===
def generate_payment_link(user_id: int):
    return (
        f"{PAYFORM_URL}/?do=pay"
        f"&products[0][name]=Доступ в канал Меняя реальность"
        f"&products[0][price]={PRICE}"
        f"&products[0][quantity]=1"
        f"&order_id={user_id}"
        f"&customer_extra=Оплата от пользователя {user_id}"
    )


# === Telegram webhook ===
@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    json_data = await request.json()
    update = telebot.types.Update.de_json(json_data)
    bot.process_new_updates([update])
    return {"ok": True}


# === Prodamus webhook (без проверки подписи) ===
@app.post("/webhook")
async def prodamus_webhook(request: Request):
    try:
        # Читаем JSON или form-data
        try:
            data = await request.json()
        except:
            form = await request.form()
            data = dict(form)

        # --- user_id строго из customer_extra ---
        customer_extra = str(data.get("customer_extra", ""))

        if "пользователя" in customer_extra:
            user_id = int(customer_extra.split()[-1])
        else:
            return {"status": "error", "message": f"Не удалось определить user_id. customer_extra={customer_extra}"}

        # Создаём одноразовую ссылку
        bot.unban_chat_member(CHANNEL_ID, user_id)
        invite = bot.create_chat_invite_link(
            chat_id=CHANNEL_ID,
            expire_date=None,
            member_limit=1
        )
        bot.send_message(user_id, f"Оплата успешна! Вот ссылка для входа: {invite.invite_link}")

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
    bot.send_message(
        message.chat.id,
        f"Привет! Оплати подписку {PRICE}₽, чтобы попасть в канал.\n"
        f"Твой ID: {message.from_user.id}",
        reply_markup=markup
    )


# === Корневой эндпоинт ===
@app.get("/")
async def home():
    return {"status": "Bot is running!"}
