import os
import asyncio
import logging
import hashlib
import time
import threading
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

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
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import Column, Integer, String, Float, DateTime, BigInteger, select

# ------------------- Переменные окружения -------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "8942021486:AAGuGoMDwLXqSIIv8N_3rj6kmbZqIKH8riE")
SUPPORT_LINK = os.getenv("SUPPORT_LINK", "https://t.me/+ffrIKHeanSFmMDcy")

# ------------------- Логирование -------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------- Путь к файлу фото -------------------
PHOTO_FILE = Path(__file__).parent / "f.png"
if PHOTO_FILE.exists():
    logger.info(f"✅ Файл f.png найден, размер: {PHOTO_FILE.stat().st_size} байт")
else:
    logger.warning(f"❌ Файл f.png НЕ НАЙДЕН по пути {PHOTO_FILE.absolute()}")

# ------------------- Асинхронная БД (aiosqlite) -------------------
DATABASE_URL = "sqlite+aiosqlite:///exstar.db"
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

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# ------------------- Инициализация бота -------------------
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Кеш приветствий (чтобы не дублировать)
welcomed_users = set()

# ------------------- FSM -------------------
class SwapStates(StatesGroup):
    choose_deposit = State()
    enter_stars_amount = State()
    choose_asset = State()
    enter_wallet = State()
    enter_rub_recipient = State()
    confirm_swap = State()

# ------------------- Клавиатуры -------------------
def main_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Обменять", callback_data="new_swap")
    builder.button(text="⚙️ Настроить кошелёк", callback_data="setup_wallet")
    builder.button(text="🤝 Спонсоры", callback_data="sponsors")
    builder.button(text="💬 Поддержка", callback_data="support")
    builder.adjust(1)
    return builder.as_markup()

def sponsors_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="⭐ Патрик Stars", url="https://t.me/patrickstarsrobot?start=6378686913")
    builder.button(text="🦆 DuckyStars", url="https://t.me/duckystars_bot?start=r_fnvbu1u122wx")
    builder.button(text="🏪 Portals Market", url="https://t.me/portals_market_bot/market?startapp=gift_019f8a58-1ea3-7a61-b47c-58ee6450df47_p0yi8t")
    builder.button(text="🔙 Назад", callback_data="back_to_menu")
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
    builder.button(text="💰 Добавить стейк", callback_data=f"stake_{order_hash}")
    builder.button(text="📄 Открыть карточку", callback_data=f"card_{order_hash}")
    builder.adjust(1)
    return builder.as_markup()

# ------------------- Работа с БД (асинхронно) -------------------
async def save_order(user_id, stars, asset, receive, recipient):
    async with AsyncSessionLocal() as session:
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
        await session.commit()
        return order_hash

async def update_order_paid(order_hash, payment_id):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Order).where(Order.order_hash == order_hash))
        order = result.scalar_one_or_none()
        if order:
            order.status = "paid"
            order.payment_id = payment_id
            order.updated_at = datetime.utcnow()
            await session.commit()

async def get_order(order_hash):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Order).where(Order.order_hash == order_hash))
        return result.scalar_one_or_none()

# ------------------- Обработчики -------------------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    if user_id in welcomed_users:
        await message.answer("🌟 Добро пожаловать обратно!", reply_markup=main_menu_keyboard())
        return
    welcomed_users.add(user_id)

    caption = (
        "🌟 Добро пожаловать в ExStar!\n\n"
        "Обменяйте звёзды 🌟 на GRAM, ETH, USDT и RUB — прямо в Telegram.\n\n"
        "Нажмите «Обменять» ниже, чтобы начать обмен. "
        "Мы будем держать вас в курсе каждого шага здесь, в этом чате."
    )

    if PHOTO_FILE.exists():
        try:
            photo = FSInputFile(str(PHOTO_FILE))
            await message.answer_photo(photo=photo, caption=caption, reply_markup=main_menu_keyboard())
            logger.info(f"Приветствие с фото отправлено пользователю {user_id}")
            return
        except Exception as e:
            logger.error(f"Ошибка при отправке фото: {e}")
    await message.answer(caption, reply_markup=main_menu_keyboard())

# ------------------- Обработчики callback (с удалением старых сообщений) -------------------
@dp.callback_query(F.data == "new_swap")
async def start_swap(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.delete()
    await callback.message.answer("📥 Выберите способ пополнения:", reply_markup=deposit_method_keyboard())
    await state.set_state(SwapStates.choose_deposit)

@dp.callback_query(F.data == "setup_wallet")
async def setup_wallet(callback: types.CallbackQuery):
    await callback.answer("⚙️ Настройка кошелька в разработке.", show_alert=True)

@dp.callback_query(F.data == "sponsors")
async def show_sponsors(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.delete()
    await callback.message.answer(
        "🤝 Наши спонсоры:\n\n"
        "⭐ Патрик Stars — получай звёзды бесплатно\n"
        "🦆 DuckyStars — зарабатывай Stars за задания\n"
        "🏪 Portals Market — торгуй подарками\n\n"
        "Поддержи их — и они поддержат нас!",
        reply_markup=sponsors_keyboard()
    )

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.delete()
    await callback.message.answer("🌟 Главное меню", reply_markup=main_menu_keyboard())

@dp.callback_query(F.data == "support")
async def support(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.answer(f"💬 Чат поддержки: {SUPPORT_LINK}")

@dp.callback_query(F.data == "cancel", StateFilter("*"))
async def cancel_handler(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("Отменено.")
    await callback.message.delete()
    await callback.message.answer("🌟 Главное меню", reply_markup=main_menu_keyboard())

@dp.callback_query(F.data == "deposit_stars", StateFilter(SwapStates.choose_deposit))
async def deposit_stars(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.delete()
    await callback.message.answer(
        "⭐ Введите количество Звёзд:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖ Отмена", callback_data="cancel")]])
    )
    await state.set_state(SwapStates.enter_stars_amount)

@dp.callback_query(F.data == "deposit_crypto", StateFilter(SwapStates.choose_deposit))
async def deposit_crypto(callback: types.CallbackQuery):
    await callback.answer("Пока только Звёзды → Крипто/Рубли.", show_alert=True)

@dp.message(F.text, StateFilter(SwapStates.enter_stars_amount))
async def process_stars_amount(message: types.Message, state: FSMContext):
    try:
        amount = int(message.text)
        if amount <= 0: raise ValueError
    except ValueError:
        await message.answer("❌ Введите положительное целое число.")
        return
    await state.update_data(stars_amount=amount)
    await message.answer("📊 Выберите актив:", reply_markup=asset_keyboard())
    await state.set_state(SwapStates.choose_asset)

@dp.callback_query(F.data.startswith("asset_"), StateFilter(SwapStates.choose_asset))
async def process_asset(callback: types.CallbackQuery, state: FSMContext):
    asset_map = {
        "asset_gram": "GRAM", "asset_eth": "ETH",
        "asset_usdt_erc20": "USDT (ERC-20)", "asset_usdt_jetton": "USDT (Jetton)",
        "asset_rub": "RUB"
    }
    asset = asset_map.get(callback.data)
    if not asset:
        await callback.answer("Неизвестно.")
        return
    await state.update_data(asset=asset)
    await callback.answer()
    await callback.message.delete()
    if asset == "RUB":
        await callback.message.answer(
            "💳 Введите номер карты/телефона и банк.\nПример: 1234 5678 9012 3456, Сбербанк",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖ Отмена", callback_data="cancel")]])
        )
        await state.set_state(SwapStates.enter_rub_recipient)
    else:
        await callback.message.answer(
            f"📤 Введите адрес кошелька для {asset}:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖ Отмена", callback_data="cancel")]])
        )
        await state.set_state(SwapStates.enter_wallet)

@dp.message(F.text, StateFilter(SwapStates.enter_rub_recipient))
async def process_rub_recipient(message: types.Message, state: FSMContext):
    data = await state.get_data()
    stars = data.get("stars_amount")
    receive = round(stars * 0.03, 2)
    order_hash = await save_order(message.from_user.id, stars, "RUB", receive, message.text)
    await message.answer(
        f"📝 Вы отправляете: {stars} Звёзд\nВы получаете: {receive} RUB\nНа реквизиты: {message.text}\n\nПодтверждаете?",
        reply_markup=confirm_keyboard()
    )
    await state.update_data(order_hash=order_hash)
    await state.set_state(SwapStates.confirm_swap)

@dp.message(F.text, StateFilter(SwapStates.enter_wallet))
async def process_wallet(message: types.Message, state: FSMContext):
    if len(message.text) < 20:
        await message.answer("❌ Слишком короткий адрес.")
        return
    data = await state.get_data()
    stars = data.get("stars_amount")
    asset = data.get("asset")
    rates = {"GRAM": 0.004, "ETH": 0.0001, "USDT (ERC-20)": 0.01, "USDT (Jetton)": 0.01}
    rate = rates.get(asset, 0.001)
    receive = round(stars * rate, 4)
    order_hash = await save_order(message.from_user.id, stars, asset, receive, message.text)
    await message.answer(
        f"📝 Вы отправляете: {stars} Звёзд\nВы получаете: {receive} {asset}\nНа кошелёк: {message.text[:6]}...{message.text[-6:]}\n\nПодтверждаете?",
        reply_markup=confirm_keyboard()
    )
    await state.update_data(order_hash=order_hash)
    await state.set_state(SwapStates.confirm_swap)

@dp.callback_query(F.data == "confirm_pay", StateFilter(SwapStates.confirm_swap))
async def confirm_swap(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    stars = data.get("stars_amount")
    asset = data.get("asset")
    receive = data.get("receive") or 0
    order_hash = data.get("order_hash")
    if not order_hash:
        await callback.answer("Ошибка заказа.")
        return
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=f"⭐ Обмен Звёзд на {asset}",
        description=f"{stars} Звёзд → {receive} {asset}",
        payload=f"swap_{callback.from_user.id}_{order_hash}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=f"{stars} Звёзд", amount=stars)],
        start_parameter="swap_start",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⭐ Оплатить", pay=True)]])
    )
    await callback.answer("Счёт создан.")

@dp.pre_checkout_query()
async def pre_checkout_handler(pre_checkout: PreCheckoutQuery):
    await pre_checkout.answer(ok=True)

@dp.message(F.successful_payment)
async def successful_payment_handler(message: types.Message, state: FSMContext):
    payment = message.successful_payment
    parts = payment.invoice_payload.split("_")
    if len(parts) < 3:
        await message.answer("❌ Ошибка заказа. Обратитесь в поддержку.")
        return
    order_hash = parts[2]
    await update_order_paid(order_hash, payment.telegram_payment_charge_id)
    order = await get_order(order_hash)
    if not order:
        await message.answer("❌ Заказ не найден.")
        return
    await message.answer(
        f"✅ Платёж на ★{order.stars_amount} получен!\n\n"
        f"📋 Заявка на проверке (21-дневное окно возврата).\n"
        f"Заказ #{order.order_hash}\nСтатус: ждёт окно возврата",
        reply_markup=after_payment_keyboard(order.order_hash)
    )
    await state.clear()

@dp.callback_query(F.data.startswith("stake_"))
async def stake_handler(callback: types.CallbackQuery):
    await callback.answer("Функция стейкинга в разработке.", show_alert=True)

@dp.callback_query(F.data.startswith("card_"))
async def card_handler(callback: types.CallbackQuery):
    await callback.answer("Карточка заказа будет позже.", show_alert=True)

# ------------------- Health‑сервер -------------------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'OK')
        else:
            self.send_response(404)
            self.end_headers()

def run_health():
    server = HTTPServer(('0.0.0.0', 8080), HealthHandler)
    server.serve_forever()

async def main():
    await init_db()
    threading.Thread(target=run_health, daemon=True).start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
