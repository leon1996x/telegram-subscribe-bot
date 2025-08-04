import os
import json
import threading
import time
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
import telebot

# === CONFIG ===
TOKEN = os.getenv("BOT_TOKEN")
PAYFORM_URL = "https://menyayrealnost.payform.ru"
CHANNEL_ID = -1002681575953
PRICE = 50
ACCESS_MINUTES = 10  # тестовая подписка 10 мин
USERS_FILE = "users.json"

ADMIN_ID = 513148972  # твой ID

bot = telebot.TeleBot(TOKEN)
app = FastAPI()

# Хранилище активных пользователей
active_users = {}

# === Устойчивая отправка сообщений ===
def send_safe(chat_id: int, text: str):
    """Отправка сообщения с 3 попытками и логом ошибок"""
    for attempt in range(3):
        try:
            bot.send_message(chat_id, text)
            return True
        except Exception as e:
            print(f"[send_safe] Ошибка при отправке ({attempt+1}/3): {e}")
            time.sleep(2)
    # Если все попытки не удались — уведомляем админа
    if chat_id != ADMIN_ID:
        try:
            bot.send_message(ADMIN_ID, f"[ALERT] Не удалось отправить сообщение пользователю {chat_id}. Ошибка: {e}")
        except:
            pass
    return False

# === Загрузка/сохранение данных ===
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
        print(f"[CHECK] Найдены истёкшие: {expired_users}")

    for uid in expired_users:
        try:
            print(f"[CHECK] Кикаю {uid}")
            bot.ban_chat_member(CHANNEL_ID, uid)   # кик
            bot.unban_chat_member(CHANNEL_ID, uid) # анбан
            send_safe(uid, "Срок подписки истёк. Чтобы продлить — оплатите снова /start.")
            send_safe(ADMIN_ID, f"Пользователь {uid} удалён из канала — подписка истекла.")
        except Exception as e:
            print(f"[CHECK] Ошибка при кике {uid}: {e}")
            send_safe(ADMIN_ID, f"Ошибка при кике {uid}: {e}")
        del active_users[uid]
        save_users()

# === Фоновый цикл проверки ===
def subscription_watcher():
    print("[WATCHER] Запущен фоновый мониторинг")
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

        # Определяем user_id
        raw_order = str(data.get("order_id", ""))
        customer_extra = str(data.get("customer_extra", ""))

        if raw_order.isdigit() and len(raw_order) > 9:
            user_id = int(raw_order)
        elif "пользователя" in customer_extra:
            user_id = int(customer_extra.split()[-1])
        else:
            return {"status": "error", "message": "Не удалось определить user_id"}

        # Создаём одноразовую ссылку
        bot.unban_chat_member(CHANNEL_ID, user_id)
        invite = bot.create_chat_invite_link(
            chat_id=CHANNEL_ID,
            expire_date=None,
            member_limit=1
        )
        send_safe(user_id, f"Оплата успешна! Вот ссылка для входа: {invite.invite_link}")

        # Сохраняем дату окончания подписки
        active_users[user_id] = datetime.now() + timedelta(minutes=ACCESS_MINUTES)
        save_users()

        return {"status": "success"}

    except Exception as e:
        send_safe(ADMIN_ID, f"[ALERT] Ошибка вебхука: {e}")
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
    send_safe(
        message.chat.id,
        f"Привет! Оплати подписку {PRICE}₽, чтобы попасть в канал.\n"
        f"Твой ID: {message.from_user.id}"
    )
    bot.send_message(message.chat.id, "Нажми кнопку ниже для оплаты:", reply_markup=markup)

# === Корневой эндпоинт ===
@app.get("/")
async def home():
    return {"status": "Bot is running!"}

# === Запуск при старте приложения ===
load_users()
threading.Thread(target=subscription_watcher, daemon=True).start()
