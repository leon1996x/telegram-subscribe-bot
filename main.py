from fastapi import FastAPI
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# === Подключаемся к Google Sheets ===
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("GSPREAD_CREDENTIALS.json", scope)
client = gspread.authorize(creds)

# Подключаем таблицу
spreadsheet = client.open("BotData")  # название таблицы
sheet = spreadsheet.sheet1  # первый лист

# === FastAPI ===
app = FastAPI()

@app.get("/")
def home():
    return {"status": "ok", "message": "FastAPI работает!"}

@app.get("/test")
def test():
    """Добавляет тестовую строку в Google Sheets"""
    new_row = ["Привет", "Тест", "От бота"]
    sheet.append_row(new_row)
    return {"status": "ok", "added_row": new_row}

@app.get("/rows")
def rows():
    """Возвращает все строки"""
    data = sheet.get_all_values()
    return {"status": "ok", "rows": data}
