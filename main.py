
async def cmd_admin(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer("🔑 Админ-панель:", reply_markup=admin_keyboard())
    else:
        await message.answer("⛔ У вас нет доступа!")

# --- ОБРАБОТКА КНОПОК ---
@dp.message(F.text.in_(["📋 Посмотреть данные", "➕ Добавить запись", "❌ Удалить запись", "🚪 Выйти"]))
async def handle_admin_buttons(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет доступа!")
        return

    if message.text == "📋 Посмотреть данные":
        rows = worksheet.get_all_values()
        text = "\n".join([", ".join(row) for row in rows]) if rows else "📂 Таблица пуста"
        await message.answer(f"Данные из таблицы:\n\n{text}")

    elif message.text == "➕ Добавить запись":
        worksheet.append_row(["Новая запись"])
        await message.answer("✅ Запись добавлена!")

    elif message.text == "❌ Удалить запись":
        rows = worksheet.get_all_values()
        if len(rows) > 1:
            worksheet.delete_rows(len(rows))
            await message.answer("❌ Последняя запись удалена")
        else:
            await message.answer("⚠️ Удалять нечего")

    elif message.text == "🚪 Выйти":
        await message.answer("Вы вышли из админки.", reply_markup=ReplyKeyboardRemove())

# --- Фоновый запуск бота ---
@app.on_event("startup")
async def on_startup():
    loop = asyncio.get_event_loop()
    loop.create_task(dp.start_polling(bot))

