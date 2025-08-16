import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Подключение к Google Sheets
scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/drive"]

creds = ServiceAccountCredentials.from_json_keyfile_name("GSPREAD_CREDENTIALS.json", scope)
client = gspread.authorize(creds)

# Открываем таблицу (по названию — то, что ты ввёл в Google Sheets)
sheet = client.open("TelegramBotAdmin").sheet1  

# Чтение 1-й строки
row_1 = sheet.row_values(1)
print("Данные из 1-й строки:", row_1)

# Добавление новой строки
sheet.append_row(["Привет", "Тест", "От бота"])
print("✅ Записал новую строку!")

