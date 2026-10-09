import asyncio
import json
import logging
import os
import re
import sqlite3

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton as Btn,
                           InlineKeyboardMarkup, InputMediaPhoto, KeyboardButton,
                           Message, ReplyKeyboardMarkup)

TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
CARD_INFO = os.environ.get("CARD_INFO", "Реквизиты пока не заданы")
CRYPTO_TOKEN = os.environ.get("CRYPTO_PAY_TOKEN")  # пусто = кнопки CryptoBot нет
# тест-режим CryptoBot: https://testnet-pay.crypt.bot/api
CRYPTO_API = os.environ.get("CRYPTO_API_URL", "https://pay.crypt.bot/api")

CATALOG, CABINET, SUPPORT = "🛒 Каталог", "👤 Мой кабинет", "🛠 Поддержка"
STATUS = {"wait_crypto": "ждёт оплаты", "review": "на проверке",
          "paid": "оплачен", "rejected": "отклонён"}

with open("catalog.json", encoding="utf-8") as f:
    PRODUCTS = {p["id"]: p for p in json.load(f)}

# shortcut: база лежит файлом рядом с ботом; без постоянного диска на хостинге
# история заказов пропадёт при редеплое, тогда подключить volume
db = sqlite3.connect("shop.db")
db.execute("create table if not exists orders (id integer primary key, "
           "user_id integer, product text, method text, status text, invoice integer)")

bot = Bot(TOKEN)
dp = Dispatcher()
MENU = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=CATALOG), KeyboardButton(text=CABINET)],
              [KeyboardButton(text=SUPPORT)]], resize_keyboard=True)


class St(StatesGroup):
    proof = State()
    support = State()


def kb(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows])


def sellable(pid):
    p = PRODUCTS.get(pid)
    return p if p and p.get("price") else None


def new_order(uid, pid, method, status, invoice=None):
    cur = db.execute("insert into orders (user_id, product, method, status, invoice) "
                     "values (?,?,?,?,?)", (uid, pid, method, status, invoice))
    db.commit()
    return cur.lastrowid


def get_order(oid):
    return db.execute("select id, user_id, product, method, status, invoice "
                      "from orders where id=?", (oid,)).fetchone()


def move(oid, old, new):
    """Меняет статус только если он сейчас old. True = мы первые, выдача один раз."""
    cur = db.execute("update orders set status=? where id=? and status=?", (new, oid, old))
    db.commit()
    return cur.rowcount == 1


def delivery(pid, oid):
    text = PRODUCTS[pid].get("delivery") or "Администратор скоро отправит вам товар."
    return f"Заказ #{oid} оплачен ✅\n\n{text}"


async def tell(uid, text):
    try:
        await bot.send_message(uid, text)
        return True
    except Exception:
        logging.exception("не доставлено пользователю %s", uid)
        return False


async def crypto(method, **params):
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.post(f"{CRYPTO_API}/{method}", json=params,
                          headers={"Crypto-Pay-API-Token": CRYPTO_TOKEN}) as r:
            data = await r.json()
    if not data.get("ok"):
        raise RuntimeError(data)
    return data["result"]


# ---------- меню ----------

@dp.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Добро пожаловать! Выберите раздел кнопками внизу 👇", reply_markup=MENU)


@dp.message(F.text == CATALOG)
async def catalog(m: Message, state: FSMContext):
    await state.clear()
    rows = [[Btn(text=p["name"], callback_data=f"p:{i}")] for i, p in PRODUCTS.items()]
    await m.answer("Выберите товар:", reply_markup=kb(*rows))


@dp.message(F.text == CABINET)
async def cabinet(m: Message, state: FSMContext):
    await state.clear()
    rows = db.execute("select id, product, status from orders where user_id=? "
                      "and status!='wait_proof' order by id desc limit 5",
                      (m.from_user.id,)).fetchall()
    hist = "\n".join(f"#{i} {PRODUCTS.get(p, {}).get('name', p)} — {STATUS.get(s, s)}"
                     for i, p, s in rows) or "пока пусто"
    await m.answer(f"👤 {m.from_user.full_name}\nID: {m.from_user.id}\n\nПоследние заказы:\n{hist}")


@dp.message(F.text == SUPPORT)
async def support(m: Message, state: FSMContext):
    await state.set_state(St.support)
    await m.answer("Опишите вопрос одним сообщением, я передам его администратору.")


@dp.message(St.support, F.text)
async def support_send(m: Message, state: FSMContext):
    await bot.send_message(ADMIN_ID, f"🛠 Обращение\nID: {m.from_user.id}\n"
                                     f"{m.from_user.full_name}\n\n{m.text}")
    await state.clear()
    await m.answer("Отправлено. Ответ придёт сюда, в бот.")


@dp.message(F.from_user.id == ADMIN_ID, F.reply_to_message, F.text)
async def admin_reply(m: Message):
    """Админ отвечает реплаем на обращение или чек: ответ уходит пользователю с ID из сообщения."""
    src = m.reply_to_message.text or m.reply_to_message.caption or ""
    found = re.search(r"ID: (\d+)", src)
    if found and not await tell(int(found[1]), f"Ответ поддержки:\n\n{m.text}"):
        await m.answer("Не доставлено (пользователь заблокировал бота?)")


# ---------- каталог и покупка ----------

@dp.callback_query(F.data.startswith("p:"))
async def product(c: CallbackQuery):
    p = PRODUCTS.get(c.data[2:])
    if not p:
        return await c.answer("Товар не найден", show_alert=True)
    price = f"{p['price']} ₽" if p.get("price") else "уточняется, покупка пока недоступна"
    text = f"{p['name']}\n\n{p['description']}\n\nЦена: {price}"
    markup = kb([Btn(text="🛒 Купить", callback_data=f"b:{p['id']}")]) if p.get("price") else kb()
    photos = p.get("photo") or []  # строка или список: ссылки или file_id
    if isinstance(photos, str):
        photos = [photos]
    if len(photos) > 1:  # у альбома не бывает кнопок, поэтому текст отдельным сообщением
        await c.message.answer_media_group([InputMediaPhoto(media=x) for x in photos])
        await c.message.answer(text, reply_markup=markup)
    elif photos:
        await c.message.answer_photo(photos[0], caption=text, reply_markup=markup)
    else:
        await c.message.answer(text, reply_markup=markup)
    await c.answer()


@dp.callback_query(F.data.startswith("b:"))
async def buy(c: CallbackQuery):
    if not sellable(c.data[2:]):
        return await c.answer("Товар недоступен", show_alert=True)
    pid = c.data[2:]
    rows = [[Btn(text="💳 Перевод на карту", callback_data=f"card:{pid}")]]
    if CRYPTO_TOKEN:
        rows.append([Btn(text="₿ CryptoBot", callback_data=f"cr:{pid}")])
    await c.message.answer("Способ оплаты:", reply_markup=kb(*rows))
    await c.answer()


@dp.callback_query(F.data.startswith("card:"))
async def pay_card(c: CallbackQuery, state: FSMContext):
    p = sellable(c.data[5:])
    if not p:
        return await c.answer("Товар недоступен", show_alert=True)
    oid = new_order(c.from_user.id, p["id"], "card", "wait_proof")
    await state.set_state(St.proof)
    await state.update_data(oid=oid)
    await c.message.answer(f"Заказ #{oid}: {p['name']}\nК оплате: {p['price']} ₽\n\n{CARD_INFO}\n\n"
                           "После перевода отправьте сюда скриншот оплаты.")
    await c.answer()


@dp.message(St.proof, F.photo)
async def proof(m: Message, state: FSMContext):
    oid = (await state.get_data())["oid"]
    o = get_order(oid)
    if not move(oid, "wait_proof", "review"):
        return await m.answer("Этот заказ уже отправлен на проверку.")
    p = PRODUCTS[o[2]]
    await bot.send_photo(
        ADMIN_ID, m.photo[-1].file_id,
        caption=f"💳 Заказ #{oid}\n{p['name']} — {p['price']} ₽\nID: {m.from_user.id}\n{m.from_user.full_name}",
        reply_markup=kb([Btn(text="✅ Подтвердить", callback_data=f"ok:{oid}"),
                         Btn(text="❌ Отклонить", callback_data=f"no:{oid}")]))
    await state.clear()
    await m.answer("Скриншот отправлен, ждите подтверждения.")


@dp.message(St.proof)
async def proof_bad(m: Message):
    await m.answer("Нужен скриншот оплаты (фото). Чтобы выйти, нажмите кнопку меню.")


@dp.callback_query(F.data.regexp(r"^(ok|no):\d+$"))
async def decide(c: CallbackQuery):
    if c.from_user.id != ADMIN_ID:
        return await c.answer("Нет доступа", show_alert=True)
    act, oid = c.data.split(":")
    oid = int(oid)
    o = get_order(oid)
    if not o or not move(oid, "review", "paid" if act == "ok" else "rejected"):
        return await c.answer("Заказ уже обработан", show_alert=True)
    if act == "ok":
        sent = await tell(o[1], delivery(o[2], oid))
        mark = "✅ Подтверждён"
    else:
        sent = await tell(o[1], f"Оплата по заказу #{oid} не подтверждена. Напишите в поддержку.")
        mark = "❌ Отклонён"
    if not sent:
        mark += " (пользователю НЕ доставлено!)"
    await c.message.edit_caption(caption=f"{c.message.caption}\n\n{mark}")
    await c.answer()


@dp.callback_query(F.data.startswith("cr:"))
async def pay_crypto(c: CallbackQuery):
    p = sellable(c.data[3:])
    if not p or not CRYPTO_TOKEN:
        return await c.answer("Оплата недоступна", show_alert=True)
    try:
        inv = await crypto("createInvoice", currency_type="fiat", fiat="RUB",
                           amount=str(p["price"]), description=p["name"])
    except Exception:
        logging.exception("createInvoice")
        return await c.answer("Не удалось создать счёт, попробуйте позже", show_alert=True)
    oid = new_order(c.from_user.id, p["id"], "crypto", "wait_crypto", inv["invoice_id"])
    url = inv.get("bot_invoice_url") or inv.get("pay_url")
    await c.message.answer(f"Заказ #{oid}: {p['name']}\nК оплате: {p['price']} ₽\n\nСчёт: {url}\n\n"
                           "После оплаты нажмите «Проверить».",
                           reply_markup=kb([Btn(text="🔄 Проверить оплату", callback_data=f"chk:{oid}")]))
    await c.answer()


@dp.callback_query(F.data.startswith("chk:"))
async def check_crypto(c: CallbackQuery):
    o = get_order(int(c.data[4:]))
    if not o or o[1] != c.from_user.id or o[3] != "crypto":
        return await c.answer("Заказ не найден", show_alert=True)
    if o[4] == "paid":
        return await c.answer("Заказ уже оплачен")
    try:
        res = await crypto("getInvoices", invoice_ids=str(o[5]))
    except Exception:
        logging.exception("getInvoices")
        return await c.answer("Не удалось проверить, попробуйте ещё раз", show_alert=True)
    items = res["items"] if isinstance(res, dict) else res
    if items and items[0]["status"] == "paid" and move(o[0], "wait_crypto", "paid"):
        await c.message.answer(delivery(o[2], o[0]))
        await c.answer()
    else:
        await c.answer("Оплата пока не найдена", show_alert=True)


@dp.message(F.from_user.id == ADMIN_ID, F.photo)
async def photo_id(m: Message):
    """Админ шлёт боту скрин, в ответ получает file_id для поля photo в catalog.json."""
    await m.answer(m.photo[-1].file_id)


async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
