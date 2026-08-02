FROM python:3.10-slim

WORKDIR /app

# Копируем зависимости и устанавливаем
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем все файлы проекта (включая f.png)
COPY . .

# Запускаем бота
CMD ["python", "bot.py"]
