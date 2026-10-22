"""Telegram-бот магазина Black Mist (aiogram 3.x). Запуск: python bot.py
Переменные окружения: BOT_TOKEN, ADMIN_IDS (через запятую), CURRENCY (по умолчанию ₽),
WEBAPP_URL (https-адрес мини-приложения), SHOP_NAME (по умолчанию Black Mist)."""
import asyncio
import html
import json
import logging
import os

# Добавил aiohttp для работы сервера Render
from aiohttp import web

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (CallbackQuery, KeyboardButton, Message,
                           ReplyKeyboardMarkup, WebAppInfo)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from store import CATEGORIES, Store

TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
CUR = os.getenv("CURRENCY", "₽")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")  # https-адрес, где лежит webapp/index.html
SHOP_NAME = os.getenv("SHOP_NAME", "Black Mist")

store = Store(os.getenv("DB_PATH", "shop.db"))
router = Router()


class Checkout(StatesGroup):
    name = State()
    contact = State()
    address = State()


def money(v): return f"{v} {CUR}"


def shop_kb():
    if not WEBAPP_URL:
        return None
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[[
        KeyboardButton(text="🖤 Открыть магазин", web_app=WebAppInfo(url=WEBAPP_URL))]])


# ---------- защита: пока не подтверждён возраст, ничего не работает ----------
class AdultOnly(BaseMiddleware):
    ALLOWED = {"age_ok", "age_no"}

    async def __call__(self, handler, event, data):
        if isinstance(event, CallbackQuery) and event.data not in self.ALLOWED:
            if not store.is_adult(event.from_user.id):
                await event.answer("Сначала подтвердите возраст: /start", show_alert=True)
                return
        return await handler(event, data)


router.callback_query.middleware(AdultOnly())


# ---------- клавиатуры ----------
def main_menu():
    b = InlineKeyboardBuilder()
    b.button(text="🛍 Каталог", callback_data="catalog")
    b.button(text="🧺 Корзина", callback_data="cart")
    b.adjust(2)
    return b.as_markup()


def catalog_kb():
    b = InlineKeyboardBuilder()
    for key, name in CATEGORIES:
        b.button(text=name, callback_data=f"cat:{key}")
    b.button(text="⬅️ Меню", callback_data="menu")
    b.adjust(2, 2, 1)
    return b.as_markup()


# ---------- старт и возраст ----------
@router.message(CommandStart())
async def start(m: Message):
    if store.is_adult(m.from_user.id):
        if WEBAPP_URL:
            await m.answer(f"🖤 <b>{SHOP_NAME}</b>\nМагазин открывается кнопкой внизу 👇", reply_markup=shop_kb())
        await m.answer("Главное меню:", reply_markup=main_menu())
        return
    b = InlineKeyboardBuilder()
    b.button(text="✅ Мне есть 18 лет", callback_data="age_ok")
    b.button(text="❌ Нет", callback_data="age_no")
    b.adjust(1)
    await m.answer(f"🖤 <b>{SHOP_NAME}</b>\n\n🔞 Товары предназначены только для лиц старше 18 лет.\nПодтвердите возраст:",
                   reply_markup=b.as_markup())


@router.callback_query(F.data == "age_ok")
async def age_ok(c: CallbackQuery):
    store.set_adult(c.from_user.id, True)
    await c.message.edit_text("Спасибо! Главное меню:", reply_markup=main_menu())
    if WEBAPP_URL:
        await c.message.answer("Магазин с картинками и корзиной — кнопка внизу 👇", reply_markup=shop_kb())
    await c.answer()


@router.callback_query(F.data == "age_no")
async def age_no(c: CallbackQuery):
    store.set_adult(c.from_user.id, False)
    await c.message.edit_text("К сожалению, доступ закрыт.")
    await c.answer()


@router.callback_query(F.data == "menu")
async def menu(c: CallbackQuery):
    await c.message.edit_text("Главное меню:", reply_markup=main_menu())
    await c.answer()


# ---------- каталог ----------
@router.callback_query(F.data == "catalog")
async def catalog(c: CallbackQuery):
    await c.message.edit_text("Выберите категорию:", reply_markup=catalog_kb())
    await c.answer()


@router.callback_query(F.data.startswith("cat:"))
async def category(c: CallbackQuery):
    key = c.data.split(":", 1)[1]
    name = dict(CATEGORIES).get(key, "Категория")
    items = store.products(key)
    b = InlineKeyboardBuilder()
    for p in items:
        b.button(text=f"{p['title']} — {money(p['price'])}", callback_data=f"prod:{p['id']}")
    b.button(text="⬅️ Назад", callback_data="catalog")
    b.adjust(1)
    text = f"<b>{name}</b>" if items else f"<b>{name}</b>\nПока нет в наличии."
    await c.message.edit_text(text, reply_markup=b.as_markup())
    await c.answer()


@router.callback_query(F.data.startswith("prod:"))
async def product(c: CallbackQuery):
    p = store.product(int(c.data.split(":", 1)[1]))
    if not p:
        await c.answer("Товар не найден", show_alert=True)
        return
    b = InlineKeyboardBuilder()
    b.button(text="➕ В корзину", callback_data=f"add:{p['id']}")
    b.button(text="🧺 Корзина", callback_data="cart")
    b.button(text="⬅️ Назад", callback_data=f"cat:{p['category']}")
    b.adjust(2, 1)
    text = (f"<b>{html.escape(p['title'])}</b>\n{html.escape(p['description'])}\n\n"
            f"Цена: <b>{money(p['price'])}</b>\nВ наличии: {p['stock']} шт.")
    await c.message.edit_text(text, reply_markup=b.as_markup())
    await c.answer()


@router.callback_query(F.data.startswith("add:"))
async def add(c: CallbackQuery):
    ok, msg = store.cart_add(c.from_user.id, int(c.data.split(":", 1)[1]))
    await c.answer(("✅ " if ok else "⚠️ ") + msg, show_alert=not ok)


# ---------- корзина ----------
def cart_text(user_id):
    items = store.cart_items(user_id)
    if not items:
        return "Корзина пуста.", False
    lines = [f"• {html.escape(i['title'])}\n   {i['qty']} × {money(i['price'])} = {money(i['qty'] * i['price'])}"
             for i in items]
    return "<b>Ваша корзина</b>\n\n" + "\n".join(lines) + f"\n\n<b>Итого: {money(store.cart_total(user_id))}</b>", True


@router.callback_query(F.data == "cart")
async def cart(c: CallbackQuery):
    text, has = cart_text(c.from_user.id)
    b = InlineKeyboardBuilder()
    if has:
        b.button(text="✅ Оформить заказ", callback_data="checkout")
        b.button(text="🗑 Очистить", callback_data="clear")
    b.button(text="🛍 Каталог", callback_data="catalog")
    b.adjust(1)
    await c.message.edit_text(text, reply_markup=b.as_markup())
    await c.answer()


@router.callback_query(F.data == "clear")
async def clear(c: CallbackQuery):
    store.cart_clear(c.from_user.id)
    await c.message.edit_text("Корзина очищена.", reply_markup=main_menu())
    await c.answer()


# ---------- оформление заказа ----------
@router.callback_query(F.data == "checkout")
async def checkout(c: CallbackQuery, state: FSMContext):
    if not store.cart_items(c.from_user.id):
        await c.answer("Корзина пуста", show_alert=True)
        return
    await state.set_state(Checkout.name)
    await c.message.answer("Как к вам обращаться? (имя)")
    await c.answer()


@router.message(Checkout.name, F.text)
async def co_name(m: Message, state: FSMContext):
    await state.update_data(name=m.text.strip()[:100])
    await state.set_state(Checkout.contact)
    await m.answer("Телефон или @username для связи:")


@router.message(Checkout.contact, F.text)
async def co_contact(m: Message, state: FSMContext):
    await state.update_data(contact=m.text.strip()[:100])
    await state.set_state(Checkout.address)
    await m.answer("Адрес доставки или «самовывоз»:")


@router.message(Checkout.address, F.text)
async def co_address(m: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    ok, res = store.create_order(m.from_user.id, m.from_user.username,
                                 data["name"], data["contact"], m.text.strip()[:300])
    if not ok:
        await m.answer(f"Не удалось оформить заказ: {res}\nПроверьте корзину.", reply_markup=main_menu())
        return
    await m.answer(f"🎉 Заказ №{res} принят! Мы свяжемся с вами.", reply_markup=main_menu())
    await notify_admins(bot, res)


async def notify_admins(bot: Bot, oid: int):
    o, items = store.order(oid)
    lines = "\n".join(f"• {html.escape(i['title'])} — {i['qty']} × {money(i['price'])}" for i in items)
    uname = f"@{o['username']}" if o["username"] else f"id {o['user_id']}"
    text = (f"🆕 <b>Заказ №{o['id']}</b>\n{lines}\n\n<b>Итого: {money(o['total'])}</b>\n"
            f"Имя: {html.escape(o['name'])}\nКонтакт: {html.escape(o['contact'])}\n"
            f"Адрес: {html.escape(o['address'])}\nTelegram: {uname}")
    for admin in ADMIN_IDS:
        try:
            await bot.send_message(admin, text)
        except Exception:
            logging.exception("Не удалось уведомить админа %s", admin)


# ---------- админ-команды ----------
def is_admin(m: Message): return m.from_user.id in ADMIN_IDS


@router.message(Command("orders"), is_admin)
async def cmd_orders(m: Message):
    rows = store.recent_orders()
    if not rows:
        await m.answer("Заказов пока нет.")
        return
    await m.answer("\n".join(f"№{o['id']} · {o['created']} · {o['name']} · {money(o['total'])} · {o['status']}"
                             for o in rows))


@router.message(Command("add"), is_admin)
async def cmd_add(m: Message):
    # /add liquids | Название | Описание | 450 | 20
    parts = [x.strip() for x in m.text.removeprefix("/add").split("|")]
    keys = dict(CATEGORIES)
    if len(parts) != 5 or parts[0] not in keys or not parts[3].isdigit() or not parts[4].isdigit():
        await m.answer("Формат: /add категория | название | описание | цена | остаток\n"
                       f"Категории: {', '.join(keys)}")
        return
    pid = store.add_product(parts[0], parts[1], parts[2], int(parts[3]), int(parts[4]))
    await m.answer[(f"0Товар добавлен,]), id {pid}")


@router.message(Command int("stock"), is_admin)
async def cmd_stock(m: Message):
   (a a = m.text.split()[1:]
    if len(a[) != 2 or1 not all(x.is]))
digit() for x    in a):
 await        await m. manswer("Формат: /stock id количество")
        return
.    store.set_stock(int(aanswer("Остаток обновлён.")


@router.message(Command("price"), is_admin)
async def cmd_price(m: Message):
    a = m.text.split()[1:]
    if len(a) != 2 or not all(x.isdigit() for x in a):
        await m.answer("Формат: /price id цена")
        return
    store.set_price(int(a[0]), int(a[1]))
    await m.answer("Цена обновлена.")


# ---------- мини-приложение ----------
@router.message(Command("shop"))
async def shop(m: Message):
    if not store.is_adult(m.from_user.id):
        await m.answer("Сначала подтвердите возраст: /start")
        return
    if not WEBAPP_URL:
        await m.answer("Мини-приложение не настроено (переменная WEBAPP_URL).")
        return
    await m.answer("Откройте магазин кнопкой ниже 👇", reply_markup=shop_kb())


@router.message(F.web_app_data)
async def webapp_order(m: Message, bot: Bot):
    uid = m.from_user.id
    if not store.is_adult(uid):
        await m.answer("Сначала подтвердите возраст: /start")
        return
    try:
        d = json.loads(m.web_app_data.data)
        items = [(int(i["id"]), min(max(int(i["qty"]), 0), 50)) for i in d["items"]]
        name, contact, address = (str(d[k]).strip()[:300] for k in ("name", "contact", "address"))
    except Exception:
        await m.answer("Не удалось прочитать заказ. Попробуйте ещё раз.")
        return
    store.cart_clear(uid)
    for pid, qty in items:
        for _ in range(qty):
            ok, msg = store.cart_add(uid, pid)
            if not ok:
                store.cart_clear(uid)
                await m.answer(f"Не удалось оформить заказ: {msg}")
                return
    ok, res = store.create_order(uid, m.from_user.username, name, contact, address)
    if not ok:
        await m.answer(f"Не удалось оформить заказ: {res}")
        return
    await m.answer(f"🎉 Заказ №{res} принят! Мы свяжемся с вами.")
    await notify_admins(bot, res)


async def main():
    if not TOKEN:
        raise SystemExit("Задайте BOT_TOKEN (токен от @BotFather)")
    logging.basicConfig(level=logging.INFO)

    # --- ЗАГЛУШКА ДЛЯ RENDER (чтобы сервер не выдавал ошибку портов) ---
    async def health(request):
        return web.Response(text="OK")
    
    app = web.Application()
    app.router.add_get('/', health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    # -------------------------------------------------------------------

    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
