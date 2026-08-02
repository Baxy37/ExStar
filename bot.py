import asyncio
import logging
import hashlib
import time
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
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, BigInteger
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# ------------------- Настройка логирования -------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------- Токен бота (ваш) -------------------
BOT_TOKEN = "8942021486:AAGuGoMDwLXqSIIv8N_3rj6kmbZqIKH8riE"

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
    status = Column(String(20), default="pending")  # pending, paid
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

# ------------------- Клавиатуры -------------------
def main_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 New swap", callback_data="new_swap")
    builder.button(text="⚙️ Настроить кошелек", callback_data="setup_wallet")
    builder.button(text="💬 Поддержка", callback_data="support")
    builder.adjust(1)
    return builder.as_markup()

def deposit_method_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="⭐ Stars", callback_data="deposit_stars")
    builder.button(text="💎 Crypto", callback_data="deposit_crypto")
    builder.button(text="✖ Cancel", callback_data="cancel")
    builder.adjust(2)
    return builder.as_markup()

def asset_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="GRAM", callback_data="asset_gram")
    builder.button(text="ETH", callback_data="asset_eth")
    builder.button(text="USDT (ERC-20)", callback_data="asset_usdt_erc20")
    builder.button(text="USDT (Jetton)", callback_data="asset_usdt_jetton")
    builder.button(text="RUB", callback_data="asset_rub")
    builder.button(text="✖ Cancel", callback_data="cancel")
    builder.adjust(2)
    return builder.as_markup()

def confirm_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data="confirm_pay")
    builder.button(text="✖ Cancel", callback_data="cancel")
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
    await message.answer(
        "🌟 Добро пожаловать в ExStar!\n\n"
        "Обменивайте GRAM, ETH, USDT, RUB и Telegram Stars — прямо в Telegram.\n\n"
        "Нажмите «🔄 New swap» ниже, чтобы начать обмен. Мы будем держать вас в курсе каждого шага здесь, в этом чате.",
        reply_markup=main_menu_keyboard(),
    )

@dp.callback_query(F.data == "new_swap")
async def start_swap(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text(
        "📥 Choose how to deposit:",
        reply_markup=deposit_method_keyboard(),
    )
    await state.set_state(SwapStates.choose_deposit)

@dp.callback_query(F.data == "setup_wallet")
async def setup_wallet(callback: types.CallbackQuery):
    await callback.answer("Функция настройки кошелька пока в разработке.", show_alert=True)

@dp.callback_query(F.data == "support")
async def support(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.answer("💬 Присоединяйтесь к чату поддержки: https://t.me/+ffrIKHeanSFmMDcy")

@dp.callback_query(F.data == "cancel", StateFilter("*"))
async def cancel_handler(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("Операция отменена.")
    await callback.message.edit_text(
        "🌟 Главное меню",
        reply_markup=main_menu_keyboard(),
    )

@dp.callback_query(F.data == "deposit_stars", StateFilter(SwapStates.choose_deposit))
async def deposit_stars(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text(
        "⭐ Введите количество Stars, которое хотите обменять:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✖ Cancel", callback_data="cancel")
        ]]),
    )
    await state.set_state(SwapStates.enter_stars_amount)

@dp.callback_query(F.data == "deposit_crypto", StateFilter(SwapStates.choose_deposit))
async def deposit_crypto(callback: types.CallbackQuery):
    await callback.answer("Пока поддерживается только обмен Stars → Криптовалюта / Рубли.", show_alert=True)

@dp.message(F.text, StateFilter(SwapStates.enter_stars_amount))
async def process_stars_amount(message: types.Message, state: FSMContext):
    try:
        amount = int(message.text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Пожалуйста, введите положительное целое число (количество Stars).")
        return
    await state.update_data(stars_amount=amount)
    await message.answer(
        "📊 Выберите актив, который хотите получить:",
        reply_markup=asset_keyboard(),
    )
    await state.set_state(SwapStates.choose_asset)

@dp.callback_query(F.data.startswith("asset_"), StateFilter(SwapStates.choose_asset))
async def process_asset(callback: types.CallbackQuery, state: FSMContext):
    asset_map = {
        "asset_gram": "GRAM",
        "asset_eth": "ETH",
        "asset_usdt_erc20": "USDT (ERC-20)",
        "asset_usdt_jetton": "USDT (Jetton)",
        "asset_rub": "RUB",
    }
    asset = asset_map.get(callback.data)
    if not asset:
        await callback.answer("Неизвестный актив.")
        return
    await state.update_data(asset=asset)
    await callback.answer()
    if asset == "RUB":
        await callback.message.edit_text(
            "💳 Введите номер карты или номер телефона для перевода, а также название банка.\n"
            "Пример: 1234 5678 9012 3456, Сбербанк\n"
            "или: +7 900 123-45-67, Тинькофф",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✖ Cancel", callback_data="cancel")
            ]]),
        )
        await state.set_state(SwapStates.enter_rub_recipient)
    else:
        await callback.message.edit_text(
            f"📤 Введите адрес кошелька для получения {asset}:\n"
            "(отправьте текстовое сообщение с адресом)",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✖ Cancel", callback_data="cancel")
            ]]),
        )
        await state.set_state(SwapStates.enter_wallet)

@dp.message(F.text, StateFilter(SwapStates.enter_rub_recipient))
async def process_rub_recipient(message: types.Message, state: FSMContext):
    recipient_data = message.text.strip()
    if len(recipient_data) < 5:
        await message.answer("❌ Слишком короткие данные. Пожалуйста, введите номер карты/телефона и банк.")
        return
    await state.update_data(recipient=recipient_data)
    data = await state.get_data()
    stars = data.get("stars_amount")
    asset = data.get("asset")
    rate = 0.03  # 1 Star = 0.03 RUB
    receive = round(stars * rate, 2)
    order_hash = save_order(
        user_id=message.from_user.id,
        stars=stars,
        asset=asset,
        receive=receive,
        recipient=recipient_data
    )
    await message.answer(
        f"📝 Проверьте детали обмена:\n\n"
        f"Вы отправляете: {stars} Stars\n"
        f"Вы получаете: {receive} RUB\n"
        f"На реквизиты: {recipient_data}\n\n"
        f"Подтверждаете?",
        reply_markup=confirm_keyboard(),
    )
    await state.update_data(order_hash=order_hash)
    await state.set_state(SwapStates.confirm_swap)

@dp.message(F.text, StateFilter(SwapStates.enter_wallet))
async def process_wallet(message: types.Message, state: FSMContext):
    wallet = message.text.strip()
    if len(wallet) < 20:
        await message.answer("❌ Адрес слишком короткий. Пожалуйста, введите корректный адрес.")
        return
    await state.update_data(wallet=wallet)
    data = await state.get_data()
    stars = data.get("stars_amount")
    asset = data.get("asset")
    if asset == "GRAM":
        rate = 0.004
    elif asset == "ETH":
        rate = 0.0001
    elif asset in ("USDT (ERC-20)", "USDT (Jetton)"):
        rate = 0.01
    else:
        rate = 0.001
    receive = round(stars * rate, 4)
    recipient = wallet
    order_hash = save_order(
        user_id=message.from_user.id,
        stars=stars,
        asset=asset,
        receive=receive,
        recipient=recipient
    )
    await message.answer(
        f"📝 Проверьте детали обмена:\n\n"
        f"Вы отправляете: {stars} Stars\n"
        f"Вы получаете: {receive} {asset}\n"
        f"На кошелёк: {wallet[:6]}...{wallet[-6:]}\n\n"
        f"Подтверждаете?",
        reply_markup=confirm_keyboard(),
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
        await callback.answer("Ошибка: заказ не найден.")
        return
    prices = [LabeledPrice(label=f"{stars} Stars", amount=stars)]
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title="⭐ Обмен Stars на " + asset,
        description=f"ExStar: {stars} Stars → {receive} {asset}",
        payload=f"swap_{callback.from_user.id}_{order_hash}",
        provider_token="",
        currency="XTR",
        prices=prices,
        start_parameter="swap_start",
        need_name=False,
        need_phone_number=False,
        need_email=False,
        need_shipping_address=False,
        is_flexible=False,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⭐ Pay", pay=True)
        ]]),
    )
    await callback.answer("Счёт создан. Оплатите, нажав «Pay».")

@dp.pre_checkout_query()
async def pre_checkout_handler(pre_checkout: PreCheckoutQuery):
    await pre_checkout.answer(ok=True)

@dp.message(F.successful_payment)
async def successful_payment_handler(message: types.Message, state: FSMContext):
    payment = message.successful_payment
    payload_parts = payment.invoice_payload.split("_")
    if len(payload_parts) >= 3:
        order_hash = payload_parts[2]
    else:
        await message.answer("❌ Не удалось определить заказ. Обратитесь в поддержку.")
        return
    update_order_paid(order_hash, payment.telegram_payment_charge_id)
    order = get_order(order_hash)
    if not order:
        await message.answer("❌ Заказ не найден.")
        return
    await message.answer(
        f"✅ Вы успешно совершили платёж в адрес ExStar на сумму ★{order.stars_amount}\n\n"
        f"📋 Заявка на проверке\n\n"
        f"Получили ваши {order.stars_amount} ★. По правилам Telegram (21-дневное окно возврата) крупные платежи ждут окончания окна перед выплатой в сети.\n\n"
        f"Заказ #{order.order_hash}\n"
        f"Оплачено {order.stars_amount} ★\n"
        f"Статус: ждёт окно возврата\n\n"
        f"Не хотите ждать? Добавьте стейк — он разблокирует мгновенный обмен на эквивалент стейка в Stars.",
        reply_markup=after_payment_keyboard(order.order_hash),
    )
    await state.clear()

@dp.callback_query(F.data.startswith("stake_"))
async def stake_handler(callback: types.CallbackQuery):
    order_hash = callback.data.split("_")[1]
    await callback.answer(f"Функция стейкинга в разработке. Заказ #{order_hash}", show_alert=True)

@dp.callback_query(F.data.startswith("card_"))
async def card_handler(callback: types.CallbackQuery):
    order_hash = callback.data.split("_")[1]
    await callback.answer(f"Карточка заказа #{order_hash} будет доступна позже.", show_alert=True)

# ------------------- Запуск бота -------------------
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
