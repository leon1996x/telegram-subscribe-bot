import os
import logging
from typing import Optional, List

from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO)

# -------------------- ENV --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7145469393"))
GSHEET_ID = os.getenv("GSHEET_ID")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан")
if not GSHEET_ID:
    raise RuntimeError("GSHEET_ID не задан")

# -------------------- FastAPI --------------------
app = FastAPI()

# -------------------- Aiogram --------------------
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher(storage=MemoryStorage())

# -------------------- Google Sheets auth (автопоиск ключа) --------------------
def find_credentials_path() -> str:
    env_path = os.getenv("GSPREAD_CREDENTIALS", "").strip()
    candidates = [
        env_path,
        "/etc/secrets/GSPREAD_CREDENTIALS.json",
        "/etc/secrets/credentials.json",
        "/etc/secrets/creds.json",
        "GSPREAD_CREDENTIALS.json",
        "credentials.json",
        "creds.json",
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    raise FileNotFoundError(
        "Файл сервисного аккаунта не найден. "
        "Положи его в Secret Files как GSPREAD_CREDENTIALS.json "
        "или задай путь в переменной окружения GSPREAD_CREDENTIALS."
    )

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
CREDENTIALS_FILE = find_credentials_path()
creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
gc = gspread.authorize(creds)
sh = gc.open_by_key(GSHEET_ID)
ws = sh.sheet1  # работаем с первым листом

# -------------------- Таблица: гарантируем заголовки --------------------
REQUIRED_HEADERS = [
    "id",
    "name",
    "file_url",
    "subscription_type",
    "subscription_end",
    "post_id",
    "post_text",
    "post_photo",
]

def ensure_headers():
    values = ws.get_all_values()
    if not values:
        ws.append_row(REQUIRED_HEADERS)
        return
    headers = values[0]
    if headers != REQUIRED_HEADERS:
        # если пусто/другие — перезапишем первой строкой нужные
        # (данные ниже остаются)
        ws.resize(rows=len(values), cols=len(REQUIRED_HEADERS))
        ws.update("1:1", [REQUIRED_HEADERS])

ensure_headers()

def get_all_records() -> List[dict]:
    # get_all_records использует первую строку как заголовки
    return ws.get_all_records()

def next_post_id() -> int:
    recs = get_all_records()
    exist = [int(r["post_id"]) for r in recs if str(r.get("post_id", "")).strip().isdigit()]
    return (max(exist) + 1) if exist else 1

def get_all_user_ids() -> List[int]:
    recs = get_all_records()
    result = []
    for r in recs:
        val = str(r.get("id", "")).strip()
        if val.isdigit():
            result.append(int(val))
    return sorted(set(result))

def upsert_user(chat_id: int, name: str):
    recs = ws.get_all_values()
    headers = recs[0] if recs else []
    id_col = headers.index("id") + 1
    name_col = headers.index("name") + 1
    # ищем пользователя
    for i in range(2, len(recs) + 1):
        if str(ws.cell(i, id_col).value).strip() == str(chat_id):
            # обновим name при необходимости
            ws.update_cell(i, name_col, name or "")
            return
    # не нашли — добавим
    ws.append_row([str(chat_id), name, "", "", "", "", "", ""])

def add_post_row(post_id: int, text: str, photo: str):
    ws.append_row(["", "", "", "", "", post_id, text, photo])

def delete_post_row(post_id: str) -> bool:
    recs = get_all_records()
    # enumerate with start=2 (вторая строка — первая запись)
    for i, row in enumerate(recs, start=2):
        if str(row.get("post_id")) == str(post_id):
            ws.delete_rows(i)
            return True
    return False

# -------------------- FSM --------------------
class PostForm(StatesGroup):
    waiting_for_text = State()
    waiting_for_photo = State()

# -------------------- Клавиатуры --------------------
def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить пост", callback_data="add_post")],
        [InlineKeyboardButton(text="📋 Показать посты (удалить)", callback_data="list_posts")],
    ])

def del_kb(post_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del:{post_id}")]
    ])

# -------------------- Хендлеры --------------------
@dp.message(F.text == "/start")
async def start_cmd(m: Message):
    # регистрируем пользователя
    name = (m.from_user.full_name or "").strip()
    upsert_user(m.chat.id, name)

    # показываем все посты
    recs = get_all_records()
    posts = [r for r in recs if str(r.get("post_id", "")).strip() != ""]
    if not posts:
        await m.answer("Постов пока нет 🚫")
        return
    for p in posts:
        text = p.get("post_text", "") or ""
        photo = p.get("post_photo", "") or ""
        if photo:
            await m.answer_photo(photo, caption=text)
        else:
            await m.answer(text)

@dp.message(F.text == "/admin")
async def admin_cmd(m: Message):
    if m.from_user.id != ADMIN_ID:
        return await m.answer("⛔ Нет доступа")
    await m.answer("⚙️ Панель админа", reply_markup=admin_menu_kb())

# --- Добавление поста ---
@dp.callback_query(F.data == "add_post")
async def add_post_cb(cq: types.CallbackQuery, state: FSMContext):
    if cq.from_user.id != ADMIN_ID:
        return await cq.answer("⛔ Нет доступа", show_alert=True)
    await state.set_state(PostForm.waiting_for_text)
    await cq.message.answer("✍️ Введи текст поста")

@dp.message(PostForm.waiting_for_text)
async def add_post_text(m: Message, state: FSMContext):
    await state.update_data(text=m.text or "")
    await state.set_state(PostForm.waiting_for_photo)
    await m.answer("📷 Пришли фото или напиши <b>нет</b>")

@dp.message(PostForm.waiting_for_photo)
async def add_post_photo(m: Message, state: FSMContext):
    data = await state.get_data()
    text = data.get("text", "") or ""
    photo_url = ""

    if m.photo:
        file = await bot.get_file(m.photo[-1].file_id)
        photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file.file_path}"
    elif (m.text or "").strip().lower() == "нет":
        photo_url = ""

    pid = next_post_id()
    add_post_row(pid, text, photo_url)
    await m.answer(f"✅ Пост добавлен (id={pid})")
    await state.clear()

    # рассылаем всем
    for uid in get_all_user_ids():
        try:
            if photo_url:
                await bot.send_photo(uid, photo_url, caption=text)
            else:
                await bot.send_message(uid, text)
        except Exception as e:
            logging.warning(f"Не смог отправить {uid}: {e}")

    # админу дубль с кнопкой удалить
    if photo_url:
        await bot.send_photo(ADMIN_ID, photo_url, caption=text, reply_markup=del_kb(pid))
    else:
        await bot.send_message(ADMIN_ID, text, reply_markup=del_kb(pid))

# --- Список постов для удаления ---
@dp.callback_query(F.data == "list_posts")
async def list_posts_cb(cq: types.CallbackQuery):
    if cq.from_user.id != ADMIN_ID:
        return await cq.answer("⛔ Нет доступа", show_alert=True)
    posts = [r for r in get_all_records() if str(r.get("post_id", "")).strip() != ""]
    if not posts:
        return await cq.message.answer("🚫 Нет постов")
    for p in posts:
        pid = p.get("post_id")
        text = p.get("post_text", "") or ""
        photo = p.get("post_photo", "") or ""
        if photo:
            await cq.message.answer_photo(photo, caption=f"{text}\n\n(id={pid})", reply_markup=del_kb(pid))
        else:
            await cq.message.answer(f"{text}\n\n(id={pid})", reply_markup=del_kb(pid))

# --- Удаление поста ---
@dp.callback_query(F.data.startswith("del:"))
async def delete_post_cb(cq: types.CallbackQuery):
    if cq.from_user.id != ADMIN_ID:
        return await cq.answer("⛔ Нет доступа", show_alert=True)
    pid = cq.data.split(":", 1)[1]
    ok = delete_post_row(pid)
    if ok:
        await cq.answer("Удалено")
        await cq.message.edit_reply_markup(reply_markup=None)
        await cq.message.answer(f"🗑 Пост {pid} удалён из базы")
    else:
        await cq.answer("Пост не найден", show_alert=True)

# -------------------- Webhook --------------------
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
RENDER_HOSTNAME = os.getenv("RENDER_EXTERNAL_HOSTNAME")
WEBHOOK_URL = f"https://{RENDER_HOSTNAME}{WEBHOOK_PATH}" if RENDER_HOSTNAME else None

@app.on_event("startup")
async def on_startup():
    if WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
        logging.info(f"Webhook установлен: {WEBHOOK_URL}")
    else:
        logging.error("RENDER_EXTERNAL_HOSTNAME не задан — webhook не установлен")

@app.on_event("shutdown")
async def on_shutdown():
    await bot.delete_webhook()
    await bot.session.close()

@app.post(WEBHOOK_PATH)
async def webhook_handler(request: Request):
    data = await request.json()
    update = types.Update(**data)
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.get("/")
async def root():
    return {"status": "ok", "webhook": WEBHOOK_URL}
