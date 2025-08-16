# Используем стабильный образ Python
FROM python:3.12-slim

# Устанавливаем зависимости для сборки
RUN apt-get update && apt-get install -y gcc g++ make

# Создаём рабочую папку
WORKDIR /app

# Сначала копируем requirements.txt
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь проект
COPY . .

# Запуск (как в твоём Procfile)
CMD ["python", "main.py"]
