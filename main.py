import os
import json
import threading
import time
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
import telebot

# === CONFIG ===
TOKEN = os.getenv("BOT_TOKEN")  # токен бота
PAYFORM_URL = "https://menyayrealnost.payform.ru"
CHANNEL_ID = -1002681575953
PRICE = 50
ACCESS_MINUTES = 10  # тестовая подписка
USERS_FILE = "users.json"
ADMIN_ID = 513148972  # твой ID

bot = telebot.TeleBot(TOKEN)
app = FastAPI()

# Хранилище подписок
active_users = {}

# === Загрузка и сохранение пользователей ===
def load_users():
    global active_users
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r") as f:
                data = json.load(f)
                active_users = {int(uid): datetime.fromisoformat(ts) for uid, ts in data.items()}
        except:
            active_users = {}

def save_users():
    with open(USERS_FILE, "w") as f:
        json.dump({uid: ts.isoformat() for uid, ts in active_users.items()}, f)

# === Проверка истёкших подписок ===
def check_expired():
    now = datetime.now()
    expired_users = [uid for uid, expiry in active_users.items() if now >= expiry]

    if expired_users:
        print(f"[CHECK] Истёкшие подписки: {expired_users}")

    for uid in expired_users:
        try:
            bot.ban_chat_member(CHANNEL_ID, uid)   # кик
            bot.unban_chat_member(CHANNEL_ID, uid) # анбан
            bot.send_message(uid, "Срок подписки истёк. Чтобы продлить — оплатите снова /start.")
            bot.send_message(ADMIN_ID, f"Пользователь {uid} удалён из канала (подписка истекла).")
        except Exception as e:
            print(f"[CHECK] Ошибка при кике {uid}: {e}")
            bot.send_message(ADMIN_ID, f"Ошибка при кике {uid}: {e}")
        del active_users[uid]
        save_users()

# === Фоновый мониторинг ===
def subscription_watcher():
    print("[WATCHER] Мониторинг запущен")
    while True:
        check_expired()
        time.sleep(60)  # проверяем раз в минуту

# === Генерация ссылки на оплату ===
def generate_payment_link(user_id: int):
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

        # Логируем входящие данные
        print(f"[WEBHOOK] Пришло: {data}")
        bot.send_message(ADMIN_ID, f"[WEBHOOK] Пришло: {data}")

        # Достаём user_id из customer_extra
        customer_extra = str(data.get("customer_extra", ""))

        if "пользователя" in customer_extra:
            user_id = int(customer_extra.split()[-1])
        else:
            bot.send_message(ADMIN_ID, f"[WEBHOOK] Не удалось определить ID: {data}")
            return {"status": "error", "message": "Не удалось определить user_id"}

        # Создаём одноразовую ссылку
        bot.unban_chat_member(CHANNEL_ID, user_id)
        invite = bot.create_chat_invite_link(
            chat_id=CHANNEL_ID,
            expire_date=None,
            member_limit=1
        )
        bot.send_message(user_id, f"Оплата успешна! Вот ссылка для входа: {invite.invite_link}")

        # Сохраняем срок подписки (10 мин)
        active_users[user_id] = datetime.now() + timedelta(minutes=ACCESS_MINUTES)
        save_users()

        return {"status": "success"}

    except Exception as e:
        bot.send_message(ADMIN_ID, f"[WEBHOOK] Ошибка: {e}")
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

# === Запуск при старте ===
load_users()
threading.Thread(target=subscription_watcher, daemon=True).start()
