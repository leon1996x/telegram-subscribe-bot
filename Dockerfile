# Используем Python 3.12
FROM python:3.12-slim

# Директория внутри контейнера
WORKDIR /app

# Сначала ставим зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Потом копируем весь проект
COPY . .

# Запуск бота
CMD ["python", "main.py"]
