import os
import json
import бота
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()

# Хранилище оплаченных файлов
paid_files = {}

# === Загрузка/сохранение оплаченных файлов ===
def load_paid_files():
    global paid_files
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r") as f:
                paid_files = json.load(f)
                # Конвертируем строки обратно в datetime
                for user_id, files in paid_files.items():
                    for file_id, expiry_str in files.items():
                        if expiry_str:  # Если есть срок действия
                            paid_files[user_id][file_id] = datetime.fromisoformat(expiry_str)
        except Exception as e:
            logger.error(f"Ошибка загрузки файлов оплаты: {e}")
            paid_files = {}

def save_paid_files():
    try:
        # Конвертируем datetime в строки для JSON
        save_data = {}
        for user_id, files in paid_files.items():
            save_data[user_id] = {}
            for file_id, expiry in files.items():
                save_data[user_id][file_id] = expiry.isoformat() if isinstance(expiry, datetime) else expiry
        
        with open(USERS_FILE, "w") as f:
            json.dump(save_data, f)
    except Exception as e:
        logger.error(f"Ошибка сохранения файлов оплаты: {e}")

# === Проверка и удаление просроченных доступов ===
def check_expired_files():
    now = datetime.now()
    expired_entries = []
    
    for user_id, files in paid_files.items():
        for file_id, expiry in files.items():
            if isinstance(expiry, datetime) and now >= expiry:
                expired_entries.append((user_id, file_id))
    
    for user_id, file_id in expired_entries:
        try:
            logger.info(f"Удален доступ пользователя {user_id} к файлу {file_id}")
            del paid_files[user_id][file_id]
            # Если у пользователя больше нет файлов, удаляем запись
            if not paid_files[user_id]:
                del paid_files[user_id]
        except Exception as e:
            logger.error(f"Ошибка при удалении доступа: {e}")
    
    if expired_entries:
        save_paid_files()

# === Фоновая проверка каждую минуту ===
def file_access_watcher():
    logger.info("[WATCHER] Запущен мониторинг доступов к файлам")
    while True:
        check_expired_files()
        time.sleep(60)

# === Генерация ссылки на оплату файла ===
def generate_file_payment_link(user_id: int, file_id: str, price: int, file_name: str):
    params = {
        "do": "pay",
        "products[0][name]": f"Файл: {file_name}",
        "products[0][price]": price,
        "products[0][quantity]": 1,
        "order_id": f"file_{user_id}_{file_id}",
        "customer_extra": f"Оплата файла от пользователя {user_id}"
    }
    query = "&".join([f"{k}={v}" for k, v in params.items()])
    return f"{PAYFORM_URL}/?{query}"

# Подключение к Google Sheets
try:
    creds_path = '/etc/secrets/GSPREAD_CREDENTIALS.json'
    creds = Credentials.from_service_account_file(creds_path, scopes=[
        "https://www.googleapis.com/auth/spreadsheets"
    ])
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(GSHEET_ID)
    ws = sh.sheet1
    logger.info("Успешное подключение к Google Sheets!")
except Exception as e:
    logger.error(f"Ошибка Google Sheets: {e}")
    ws = None

# Клавиатуры
def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить пост", callback_data="add_post")],
        [InlineKeyboardButton(text="📋 Список постов", callback_data="list_posts")]
    ])

def delete_kb(post_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_{post_id}")]
    ])

def create_buttons_keyboard(buttons_data: str) -> Optional[InlineKeyboardMarkup]:
    """Создает клавиатуру из данных кнопок"""
    if not buttons_data or buttons_data == "нет":
        logger.info("Нет данных для создания клавиатуры")
        return None
    
    keyboard = []
    try:
        # Автоматически исправляем старый формат данных
        if "url" in buttons_data and "https://" in buttons_data and "|" not in buttons_data:
            logger.info("Обнаружен старый формат данных, исправляем...")
            parts = buttons_data.split('\n')
            url_parts = []
            for part in parts:
                if part.strip() and part != "url":
                    url_parts.append(part.strip())
            
            if len(url_parts) >= 2:
                text, url = url_parts[0], url_parts[1]
                if url.startswith(('http://', 'https://')):
                    buttons_data = f"url|{text}|{url}"
                    logger.info(f"Исправленный формат: {buttons_data}")
                else:
                    # Ищем URL в оставшихся частях
                    for url_part in url_parts:
                        if url_part.startswith(('http://', 'https://')):
                            buttons_data = f"url|{text}|{url_part}"
                            logger.info(f"Исправленный формат: {buttons_data}")
                            break
        
        buttons = buttons_data.split('|')
        logger.info(f"Разделенные кнопки: {buttons}")
        
        i = 0
        while i < len(buttons):
            button = buttons[i]
            logger.info(f"Обрабатываю кнопку [{i}]: {button}")
            
            # Для URL кнопок используем формат: url|текст|url_адрес
            if button == "url" and i + 2 < len(buttons):
                logger.info("Обнаружена URL кнопка")
                text = buttons[i + 1]
                url = buttons[i + 2]
                logger.info(f"Текст: {text}, URL: {url}")
                
                if url.startswith(('http://', 'https://')):
                    keyboard.append([InlineKeyboardButton(text=text, url=url)])
                    logger.info(f"Добавлена URL кнопка: {text} -> {url}")
                    i += 3  # Пропускаем 3 элемента: url, текст, url
                    continue
                else:
                    logger.error(f"Invalid URL: {url}")
            
            # Для файловых кнопок используем формат: file|текст|цена|file_id
            elif button == "file" and i + 3 < len(buttons):
                logger.info("Обнаружена файловая кнопка")
                text = buttons[i + 1]
                price = buttons[i + 2]
                file_id = buttons[i + 3]
                logger.info(f"Текст: {text}, Цена: {price}, File ID: {file_id}")
                
                keyboard.append([InlineKeyboardButton(text=f"{text} - {price}₽", callback_data=f"buy_file:{file_id}:{price}")])
                i += 4  # Пропускаем 4 элемента
                continue
            
            # Для остальных кнопок используем старый формат с :
            elif ':' in button:
                logger.info("Обнаружена кнопка с разделителем :")
                parts = button.split(':')
                logger.info(f"Части кнопки: {parts}")
                
                if len(parts) >= 4:
                    btn_type, text, price, extra = parts[0], parts[1], parts[2], parts[3]
                    
                    if btn_type == "file":
                        keyboard.append([InlineKeyboardButton(text=f"{text} - {price}₽", callback_data=f"buy_file:{extra}:{price}")])
                        logger.info(f"Добавлена файловая кнопка: {text}")
                    
                    elif btn_type == "channel":
                        keyboard.append([InlineKeyboardButton(text=text, callback_data=f"chan:{price}:{extra}")])
                        logger.info(f"Добавлена канальная кнопка: {text}")
            
            i += 1  # Переходим к следующему элементу
                        
    except Exception as e:
        logger.error(f"Ошибка создания клавиатуры: {e}", exc_info=True)
        return None
    
    logger.info(f"Итоговая клавиатура: {keyboard}")
    return InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None

# Состояния FSM
class PostStates(StatesGroup):
    waiting_text = State()
    waiting_photo = State()
    waiting_buttons_choice = State()
    waiting_button_type = State()
    waiting_button_text = State()
    waiting_button_price = State()
    waiting_button_file = State()
    waiting_button_days = State()
    waiting_button_url = State()

# Регистрация пользователя
async def register_user(user: types.User):
    if not ws:
        return
        
    try:
        user_id = str(user.id)
        if not user_id.isdigit():
            logger.error(f"Invalid user_id: {user_id}")
            return

        records = ws.get_all_records()
        
        if not any(str(r.get("id", "")).strip() == user_id for r in records):
            ws.append_row([
                user_id,
                user.username or "",
                "",  # file_url
                "",  # subscription_type
                "",  # subscription_end
                "",  # post_id
                "",  # post_text
                "",  # post_photo
                ""   # post_buttons
            ])
            logger.info(f"Зарегистрирован новый пользователь: {user_id}")
    except Exception as e:
        logger.error(f"Ошибка регистрации пользователя: {e}")

# Обработчики команд
@dp.message(Command("start"))
async def cmd_start(message: Message):
    try:
        await register_user(message.from_user)
        records = ws.get_all_records() if ws else []
        posts = [p for p in records if str(p.get("post_id", "")).strip()]
        
        if not posts:
            await message.answer("📭 Пока нет опубликованных постов")
            return
            
        for post in posts:
            text = post.get("post_text", "Без текста")
            photo_id = post.get("post_photo", "").strip()
            buttons_data = post.get("post_buttons", "").strip()
            
            logger.info(f"Данные кнопок из таблицы: '{buttons_data}'")
            keyboard = create_buttons_keyboard(buttons_data)
            logger.info(f"Созданная клавиатура: {keyboard}")
            
            try:
                if photo_id:
                    await message.answer_photo(
                        photo=photo_id,
                        caption=text,
                        reply_markup=keyboard if keyboard else (delete_kb(post["post_id"]) if message.from_user.id == ADMIN_ID else None)
                    )
                else:
                    await message.answer(
                        text=text,
                        reply_markup=keyboard if keyboard else (delete_kb(post["post_id"]) if message.from_user.id == ADMIN_ID else None)
                    )
            except Exception as e:
                logger.error(f"Ошибка отправки поста {post.get('post_id')}: {e}")
                await message.answer(f"📄 {text[:300]}" + ("..." if len(text) > 300 else ""))
                
    except Exception as e:
        logger.error(f"Ошибка в /start: {e}", exc_info=True)
        await message.answer("⚠️ Ошибка загрузки постов")

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("🚫 Доступ запрещен")
        return
    await message.answer("👨‍💻 Админ-панель:", reply_markup=admin_kb())

# Обработчики кнопок
@dp.callback_query(F.data.startswith("buy_file:"))
async def buy_file_callback(callback: types.CallbackQuery):
    try:
        # Разбираем данные кнопки: buy_file:file_id:price
        parts = callback.data.split(':')
        if len(parts) < 3:
            await callback.answer("❌ Ошибка формата кнопки")
            return
            
        file_id = parts[1]
        price = parts[2]
        user_id = str(callback.from_user.id)
        
        # Проверяем, есть ли уже доступ к файлу
        if user_id in paid_files and file_id in paid_files[user_id]:
            expiry = paid_files[user_id][file_id]
            if isinstance(expiry, datetime) and datetime.now() < expiry:
                # Отправляем файл
                await callback.message.answer_document(file_id, caption="✅ Вот ваш файл!")
                await callback.answer()
                return
            elif expiry == "forever":
                # Бессрочный доступ
                await callback.message.answer_document(file_id, caption="✅ Вот ваш файл!")
                await callback.answer()
                return
        
        # Предлагаем оплатить
        payment_url = generate_file_payment_link(callback.from_user.id, file_id, price, "Файл")
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"💳 Оплатить {price}₽", url=payment_url)]
        ])
        
        await callback.message.answer(
            f"📦 Для получения файла необходимо оплатить {price}₽\n"
            f"После оплаты файл будет доступен для скачивания",
            reply_markup=keyboard
        )
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка обработки покупки файла: {e}")
        await callback.answer("❌ Ошибка при обработке запроса")

@dp.callback_query(F.data == "add_post")
async def add_post_callback(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("🚫 Нет доступа")
        return
    
    await state.set_state(PostStates.waiting_text)
    await callback.message.answer("📝 Введите текст поста:")
    await callback.answer()

@dp.callback_query(F.data == "list_posts")
async def list_posts_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("🚫 Нет доступа")
        return
        
    posts = ws.get_all_records() if ws else []
    posts = [p for p in posts if str(p.get("post_id", "")).strip()]
    
    if not posts:
        await callback.message.answer("📭 Нет постов для отображения")
        return
        
    for post in posts:
        text = post.get("post_text", "Без текста")
        photo_id = post.get("post_photo", "").strip()
        post_id = post.get("post_id", "N/A")
        buttons_data = post.get("post_buttons", "").strip()
        
        keyboard = create_buttons_keyboard(buttons_data)
        
        try:
            if photo_id:
                await callback.message.answer_photo(
                    photo_id,
                    caption=f"{text}\n\nID: {post_id}\nКнопки: {buttons_data if buttons_data else 'нет'}",
                    reply_markup=keyboard if keyboard else delete_kb(post_id))
            else:
                await callback.message.answer(
                    f"{text}\n\nID: {post_id}\nКнопки: {buttons_data if buttons_data else 'нет'}",
                    reply_markup=keyboard if keyboard else delete_kb(post_id))
        except Exception as e:
            logger.error(f"Ошибка отправки поста {post_id}: {e}")
            await callback.message.answer(
                f"📄 {text[:300]}...\n\nID: {post_id}\nКнопки: {buttons_data if buttons_data else 'нет'}",
                reply_markup=delete_kb(post_id))
    await callback.answer()

@dp.callback_query(F.data.startswith("delete_"))
async def delete_post_callback(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("🚫 Нет доступа")
        return
        
    post_id = callback.data.split("_")[1]
    try:
        if ws:
            records = ws.get_all_values()
            for idx, row in enumerate(records[1:], start=2):
                if str(row[5]) == str(post_id):
                    ws.delete_rows(idx)
                    await callback.message.delete()
                    await callback.answer("✅ Пост удален")
                    return
        await callback.answer("❌ Пост не найден")
    except Exception as e:
        logger.error(f"Ошибка удаления: {e}")
        await callback.answer("⚠️ Ошибка удаления")

# Обработчики состояний
@dp.message(PostStates.waiting_text)
async def process_post_text(message: Message, state: FSMContext):
    await state.update_data(text=message.text)
    await state.set_state(PostStates.waiting_photo)
    await message.answer("📷 Отправьте фото или напишите 'пропустить':")

@dp.message(PostStates.waiting_photo)
async def process_post_photo(message: Message, state: FSMContext):
    try:
        if message.photo:
            await state.update_data(photo_id=message.photo[-1].file_id)
        elif message.text and message.text.lower() == "пропустить":
            await state.update_data(photo_id="")
        else:
            await message.answer("❌ Отправьте фото или напишите 'пропустить'")
            return

        # Предлагаем добавить кнопки
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да", callback_data="add_buttons_yes")],
            [InlineKeyboardButton(text="❌ Нет", callback_data="add_buttons_no")]
        ])
        
        await state.set_state(PostStates.waiting_buttons_choice)
        await message.answer("📌 Хотите добавить кнопки к посту?", reply_markup=keyboard)
            
    except Exception as e:
        logger.error(f"Ошибка обработки фото: {e}")
        await message.answer("❌ Ошибка обработки")
        await state.clear()

@dp.callback_query(PostStates.waiting_buttons_choice, F.data.in_(["add_buttons_yes", "add_buttons_no"]))
async def process_buttons_choice(callback: types.CallbackQuery, state: FSMContext):
    try:
        if callback.data == "add_buttons_no":
            # Сохраняем пост без кнопок
            await state.update_data(buttons="нет")
            await process_final_post(callback.message, state)
        else:
            # Предлагаем выбрать тип кнопки
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📁 Продаваемый файл", callback_data="button_type_file")],
                [InlineKeyboardButton(text="🔐 Приглашение в канал", callback_data="button_type_channel")],
                [InlineKeyboardButton(text="🔗 Обычная ссылка", callback_data="button_type_url")],
                [InlineKeyboardButton(text="✅ Готово", callback_data="button_type_done")]
            ])
            await state.set_state(PostStates.waiting_button_type)
            await state.update_data(buttons_data=[])
            await callback.message.answer("🎛 Выберите тип кнопки:", reply_markup=keyboard)
        
        await callback.answer()
    except Exception as e:
        logger.error(f"Ошибка выбора кнопок: {e}")
        await callback.message.answer("❌ Ошибка")

@dp.callback_query(PostStates.waiting_button_type, F.data.startswith("button_type_"))
async def process_button_type(callback: types.CallbackQuery, state: FSMContext):
    try:
        btn_type = callback.data.split("_")[2]
        await state.update_data(current_button_type=btn_type)
        
        if btn_type in ["file", "channel", "url"]:
            await state.set_state(PostStates.waiting_button_text)
            await callback.message.answer("📝 Введите текст для кнопки:")
        elif btn_type == "done":
            await process_final_post(callback.message, state)
        
        await callback.answer()
    except Exception as e:
        logger.error(f"Ошибка выбора типа: {e}")
        await callback.message.answer("❌ Ошибка")

@dp.message(PostStates.waiting_button_text)
async def process_button_text(message: Message, state: FSMContext):
    try:
        await state.update_data(current_button_text=message.text)
        data = await state.get_data()
        btn_type = data.get("current_button_type")
        
        if btn_type in ["file", "channel"]:
            await state.set_state(PostStates.waiting_button_price)
            await message.answer("💰 Введите цену в рублях:")
        elif btn_type == "url":
            await state.set_state(PostStates.waiting_button_url)
            await message.answer("🔗 Введите URL:")
            
    except Exception as e:
        logger.error(f"Ошибка текста кнопки: {e}")
        await message.answer("❌ Ошибка")

@dp.message(PostStates.waiting_button_price)
async def process_button_price(message: Message, state: FSMContext):
    try:
        price = message.text.strip()
        if not price.isdigit():
            await message.answer("❌ Введите корректную цену (число):")
            return
            
        await state.update_data(current_button_price=price)
        data = await state.get_data()
        btn_type = data.get("current_button_type")
        
        if btn_type == "file":
            await state.set_state(PostStates.waiting_button_file)
            await message.answer("📎 Отправьте файл для продажи:")
        elif btn_type == "channel":
            await state.set_state(PostStates.waiting_button_days)
            await message.answer("📅 Введите количество дней доступа (или 'навсегда'):")
            
    except Exception as e:
        logger.error(f"Ошибка цены: {e}")
        await message.answer("❌ Ошибка")

@dp.message(PostStates.waiting_button_file)
async def process_button_file(message: Message, state: FSMContext):
    try:
        if not (message.document or message.photo):
            await message.answer("❌ Отправьте файл или фото:")
            return
            
        file_id = message.document.file_id if message.document else message.photo[-1].file_id
        await state.update_data(current_button_file=file_id)
        
        # Добавляем кнопку в список
        data = await state.get_data()
        buttons_data = data.get("buttons_data", [])
        btn_type = data.get("current_button_type")
        text = data.get("current_button_text")
        price = data.get("current_button_price")
        file_id = data.get("current_button_file")
        
        # Используем новый формат: file|текст|цена|file_id
        buttons_data.append(f"file|{text}|{price}|{file_id}")
        await state.update_data(buttons_data=buttons_data)
        
        # Возвращаемся к выбору типа
        await offer_more_buttons(message, state)
            
    except Exception as e:
        logger.error(f"Ошибка файла: {e}")
        await message.answer("❌ Ошибка")

@dp.message(PostStates.waiting_button_days)
async def process_button_days(message: Message, state: FSMContext):
    try:
        days = message.text.strip()
        if days.lower() != "навсегда" and not days.isdigit():
            await message.answer("❌ Введите число дней или 'навсегда':")
            return
            
        # Добавляем кнопку в список
        data = await state.get_data()
        buttons_data = data.get("buttons_data", [])
        btn_type = data.get("current_button_type")
        text = data.get("current_button_text")
        price = data.get("current_button_price")
        
        buttons_data.append(f"{btn_type}:{text}:{price}:{days}")
        await state.update_data(buttons_data=buttons_data)
        
        # Возвращаемся к выбору типа
        await offer_more_buttons(message, state)
            
    except Exception as e:
        logger.error(f"Ошибка дней: {e}")
        await message.answer("❌ Ошибка")

@dp.message(PostStates.waiting_button_url)
async def process_button_url(message: Message, state: FSMContext):
    try:
        url = message.text.strip()
        if not (url.startswith('http://') or url.startswith('https://')):
            await message.answer("❌ URL должен начинаться с http:// или https://")
            return
        
        # Добавляем кнопку в список
        data = await state.get_data()
        buttons_data = data.get("buttons_data", [])
        text = data.get("current_button_text")
        
        # Правильный формат: url|текст|url_адрес
        buttons_data.append(f"url|{text}|{url}")
        await state.update_data(buttons_data=buttons_data)
        
        # Возвращаемся к выбору типа
        await offer_more_buttons(message, state)
            
    except Exception as e:
        logger.error(f"Ошибка URL: {e}")
        await message.answer("❌ Ошибка")

async def offer_more_buttons(message: Message, state: FSMContext):
    """Предлагает добавить еще кнопки"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📁 Продаваемый файл", callback_data="button_type_file")],
        [InlineKeyboardButton(text="🔐 Приглашение в канал", callback_data="button_type_channel")],
        [InlineKeyboardButton(text="🔗 Обычная ссылка", callback_data="button_type_url")],
        [InlineKeyboardButton(text="✅ Готово", callback_data="button_type_done")]
    ])
    await state.set_state(PostStates.waiting_button_type)
    await message.answer("🎛 Добавить еще кнопку или завершить?", reply_markup=keyboard)

@dp.callback_query(PostStates.waiting_button_type, F.data == "button_type_done")
async def process_buttons_done(callback: types.CallbackQuery, state: FSMContext):
    """Обработка завершения добавления кнопок"""
    await process_final_post(callback.message, state)
    await callback.answer()

async def process_final_post(message: Message, state: FSMContext):
    """Финальное сохранение поста"""
    try:
        data = await state.get_data()
        text = data.get("text", "")
        photo_id = data.get("photo_id", "")
        buttons_data = data.get("buttons_data", [])
        
        if ws:
            records = ws.get_all_records()
            
            post_ids = []
            for p in records:
                try:
                    post_id_str = str(p.get("post_id", "")).strip()
                    if post_id_str:
                        post_ids.append(int(post_id_str))
                except (ValueError, AttributeError):
                    continue
            post_id = max(post_ids + [0]) + 1
            
            user_ids = {str(r["id"]) for r in records if str(r.get("id", "")).strip()}
            
            # Сохраняем в таблицу (объединяем через |)
            buttons_str = "|".join(buttons_data) if buttons_data else "нет"
            ws.append_row(["", "", "", "", "", post_id, text, photo_id, buttons_str])
            
            # Создаем клавиатуру для рассылки
            keyboard = create_buttons_keyboard(buttons_str)
            
            # Рассылаем пост
            success = 0
            for user_id in user_ids:
                try:
                    if photo_id:
                        await bot.send_photo(
                            user_id, 
                            photo=photo_id, 
                            caption=text,
                            reply_markup=keyboard
                        )
                    else:
                        await bot.send_message(
                            user_id, 
                            text=text,
                            reply_markup=keyboard
                        )
                    success += 1
                except Exception as e:
                    logger.error(f"Не удалось отправить пост пользователю {user_id}: {e}")

            await message.answer(
                f"✅ Пост добавлен (ID: {post_id})\n"
                f"Кнопки: {len(buttons_data)} шт.\n"
                f"Отправлено: {success}/{len(user_ids)}"
            )
        else:
            await message.answer("⚠️ База данных недоступна")
            
    except Exception as e:
        logger.error(f"Ошибка добавления поста: {e}", exc_info=True)
        await message.answer("❌ Ошибка при добавлении поста")
    finally:
        await state.clear()

# === Prodamus webhook для файлов ===
@app.post("/webhook/prodamus/files")
async def prodamus_files_webhook(request: Request):
    check_expired_files()  # проверка на каждом апдейте
    try:
        # Читаем JSON или form-data
        try:
            data = await request.json()
        except:
            form = await request.form()
            data = dict(form)

        # Декодируем customer_extra
        raw_order = str(data.get("order_id", ""))
        customer_extra = unquote(str(data.get("customer_extra", "")))

        # Определяем user_id и file_id из order_id
        if raw_order.startswith("file_") and len(raw_order.split("_")) >= 3:
            parts = raw_order.split("_")
            user_id = parts[1]
            file_id = "_".join(parts[2:])  # На случай если file_id содержит _
        elif "пользователя" in customer_extra:
            user_id = customer_extra.split()[-1]
            # Для этого случая нужен дополнительный способ определить file_id
            # Можно добавить file_id в customer_extra или использовать другой подход
            file_id = "unknown"  # Заглушка
        else:
            await bot.send_message(ADMIN_ID, f"[ALERT] Не удалось определить user_id/file_id: {data}")
            return {"status": "error", "message": "Не удалось определить user_id/file_id"}

        # Сохраняем доступ к файлу (бессрочный)
        if user_id not in paid_files:
            paid_files[user_id] = {}
        paid_files[user_id][file_id] = "forever"  # Бессрочный доступ
        save_paid_files()

        # Отправляем файл пользователю
        try:
            await bot.send_message(user_id, "✅ Оплата прошла успешно! Вот ваш файл:")
            await bot.send_document(user_id, file_id)
            await bot.send_message(ADMIN_ID, f"Пользователь {user_id} оплатил файл {file_id}")
        except Exception as e:
            logger.error(f"Ошибка отправки файла пользователю {user_id}: {e}")
            await bot.send_message(ADMIN_ID, f"Ошибка отправки файла пользователю {user_id}: {e}")

        return {"status": "success"}

    except Exception as e:
        logger.error(f"Ошибка вебхука файлов: {e}")
        await bot.send_message(ADMIN_ID, f"[ALERT] Ошибка вебхука файлов: {e}")
        return {"status": "error", "message": str(e)}

# Webhook
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}{WEBHOOK_PATH}"

@app.on_event("startup")
async def startup():
    if os.getenv("RENDER"):
        await bot.set_webhook(WEBHOOK_URL)
        logger.info(f"Webhook установлен: {WEBHOOK_URL}")
    # Загружаем данные об оплатах и запускаем мониторинг
    load_paid_files()
    threading.Thread(target=file_access_watcher, daemon=True).start()

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    data = await request.json()
    update = types.Update(**data)
    await dp.feed_update(bot, update)
    return {"ok": True}

@app.get("/")
async def health_check():
    return {"status": "ok", "sheets": bool(ws), "paid_files_count": len(paid_files)}
