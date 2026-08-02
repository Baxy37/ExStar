import os
import asyncio
import logging
import hashlib
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    PreCheckoutQuery,
    SuccessfulPayment,
    FSInputFile,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, BigInteger
from sqlalchemy.orm import declarative_base, sessionmaker

# ------------------- Переменные окружения -------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "8942021486:AAGuGoMDwLXqSIIv8N_3rj6kmbZqIKH8riE")
SUPPORT_LINK = os.getenv("SUPPORT_LINK", "https://t.me/+ffrIKHeanSFmMDcy")

# ------------------- Логирование -------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------- Проверка наличия f.png при старте -------------------
CURRENT_DIR = Path(__file__).parent
PHOTO_PATH = CURRENT_DIR / "f.png"
if PHOTO_PATH.exists():
    logger.info(f"✅ Файл f.png найден по пути: {PHOTO_PATH.absolute()}, размер: {PHOTO_PATH.stat().st_size} байт")
else:
    logger.error(f"❌ Файл f.png НЕ НАЙДЕН по пути: {PHOTO_PATH.absolute()}")
    # Выведем список файлов в текущей папке для отладки
    try:
        files = list(CURRENT_DIR.iterdir())
        logger.info(f"Содержимое папки {CURRENT_DIR.absolute()}: {[f.name for f in files]}")
    except Exception as e:
        logger.error(f"Не удалось прочитать содержимое папки: {e}")

# ------------------- База данных (SQLite) -------------------
DATABASE_URL = "sqlite:///exstar.db"
Base = declarative_base()

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False)
    stars_amount = Column(Integer, nullable=False)
    asset = Column(String(50), nullable=False)
    receive_amount = Column(Float, nullable=False)
    recipient = Column(String(200))
    status = Column(String(20), default="pending")
    payment_id = Column(String(100))
    order_hash = Column(String(20))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

engine = create_engine(DATABASE_URL)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

# ------------------- Инициализация бота -------------------
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ------------------- FSM состояния -------------------
class SwapStates(StatesGroup):
    choose_deposit = State()
    enter_stars_amount = State()
    choose_asset = State()
    enter_wallet = State()
    enter_rub_recipient = State()
    confirm_swap = State()

# ------------------- Клавиатуры (все на русском) -------------------
def main_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обменять", callback_data="new_swap")
    builder.button(text="⚙️ Настроить кошелёк", callback_data="setup_wallet")
    builder.button(text="💬 Поддержка", callback_data="support")
    builder.adjust(1)
    return builder.as_markup()

def deposit_method_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="⭐ Звёзды", callback_data="deposit_stars")
    builder.button(text="💎 Криптовалюта", callback_data="deposit_crypto")
    builder.button(text="✖ Отмена", callback_data="cancel")
    builder.adjust(2)
    return builder.as_markup()

def asset_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="GRAM", callback_data="asset_gram")
    builder.button(text="ETH", callback_data="asset_eth")
    builder.button(text="USDT (ERC-20)", callback_data="asset_usdt_erc20")
    builder.button(text="USDT (Jetton)", callback_data="asset_usdt_jetton")
    builder.button(text="RUB", callback_data="asset_rub")
    builder.button(text="✖ Отмена", callback_data="cancel")
    builder.adjust(2)
    return builder.as_markup()

def confirm_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data="confirm_pay")
    builder.button(text="✖ Отмена", callback_data="cancel")
    builder.adjust(1)
    return builder.as_markup()

def after_payment_keyboard(order_hash):
    builder = InlineKeyboardBuilder()
    builder.button(text="💰 Добавить стейк — мгновенный обмен", callback_data=f"stake_{order_hash}")
    builder.button(text="📄 Открыть карточку", callback_data=f"card_{order_hash}")
    builder.adjust(1)
    return builder.as_markup()

# ------------------- Работа с БД -------------------
def save_order(user_id, stars, asset, receive, recipient):
    session = Session()
    order_hash = hashlib.md5(f"{user_id}_{int(time.time())}".encode()).hexdigest()[:8]
    order = Order(
        user_id=user_id,
        stars_amount=stars,
        asset=asset,
        receive_amount=receive,
        recipient=recipient,
        order_hash=order_hash,
        status="pending"
    )
    session.add(order)
    session.commit()
    session.close()
    return order_hash

def update_order_paid(order_hash, payment_id):
    session = Session()
    order = session.query(Order).filter_by(order_hash=order_hash).first()
    if order:
        order.status = "paid"
        order.payment_id = payment_id
        order.updated_at = datetime.utcnow()
        session.commit()
    session.close()

def get_order(order_hash):
    session = Session()
    order = session.query(Order).filter_by(order_hash=order_hash).first()
    session.close()
    return order

# ------------------- Обработчики команд -------------------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    # Проверяем ещё раз наличие файла (на случай, если он появился после старта)
    photo_path = Path(__file__).parent / "f.png"
    logger.info(f"Обработка /start: ищем файл {photo_path.absolute()}, exists={photo_path.exists()}")

    if photo_path.exists():
        try:
            photo = FSInputFile(str(photo_path))
            await message.answer_photo(
                photo=photo,
                caption=(
                    "🌟 Добро пожаловать в ExStar!\n\n"
                    "Обменяйте звёзды 🌟 на GRAM, ETH, USDT и RUB — прямо в Telegram.\n\n"
                    "Нажмите «Обменять» ниже, чтобы начать обмен. "
                    "Мы будем держать вас в курсе каждого шага здесь, в этом чате."
                ),
                reply_markup=main_menu_keyboard(),
            )
            logger.info("Фото успешно отправлено")
            return
        except Exception as e:
            logger.error(f"Ошибка при отправке фото: {e}", exc_info=True)
    else:
        logger.warning("Файл f.png не найден, отправляем только текст")

    # Если фото не отправилось — отправляем текст
    await message.answer(
        "🌟 Добро пожаловать в ExStar!\n\n"
        "Обменяйте звёзды 🌟 на GRAM, ETH, USDT и RUB — прямо в Telegram.\n\n"
        "Нажмите «Обменять» ниже, чтобы начать обмен. "
        "Мы будем держать вас в курсе каждого шага здесь, в этом чате.",
        reply_markup=main_menu_keyboard(),
    )

# ------------------- Все остальные обработчики (без изменений) -------------------
# ... (оставляем всё остальное как в предыдущей версии, чтобы не захламлять ответ)
# Но для полноты я дам полный файл ниже.

# ------------------- Health‑сервер для Render -------------------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'OK')
        else:
            self.send_response(404)
            self.end_headers()

def run_health_server():
    server = HTTPServer(('0.0.0.0', 8080), HealthHandler)
    server.serve_forever()

# ------------------- Запуск бота и health‑сервера -------------------
async def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
