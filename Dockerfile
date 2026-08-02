FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем все файлы (включая f.png)
COPY . .

CMD ["python", "bot.py"]
