import asyncio
import html
import json
import logging
import os
import re

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton as Btn,
                           InlineKeyboardMarkup, InputMediaPhoto, KeyboardButton,
                           Message, ReplyKeyboardMarkup)

from store import (add_items, add_user, all_users, get_order, move, new_order,
                   stock, take_item, user_orders)

TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
CARD_INFO = os.environ.get("CARD_INFO", "Реквизиты пока не заданы")
CRYPTO_TOKEN = os.environ.get("CRYPTO_PAY_TOKEN")  # пусто = кнопки CryptoBot нет
# тест-режим CryptoBot: https://testnet-pay.crypt.bot/api
CRYPTO_API = os.environ.get("CRYPTO_API_URL", "https://pay.crypt.bot/api")

CATALOG, CABINET, SUPPORT = "🛒 Каталог", "👤 Мой кабинет", "🛠 Поддержка"
STATUS = {"wait_crypto": "⏳ ждёт оплаты", "review": "🔎 на проверке",
          "paid": "✅ оплачен", "rejected": "❌ отклонён"}

with open("catalog.json", encoding="utf-8") as f:
    PRODUCTS = {p["id"]: p for p in json.load(f)}

bot = Bot(TOKEN)
dp = Dispatcher()
ADM = F.from_user.id == ADMIN_ID
MENU = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=CATALOG), KeyboardButton(text=CABINET)],
              [KeyboardButton(text=SUPPORT)]], resize_keyboard=True)
last = {}  # id пользователя -> сообщения бота, которые сотрутся при следующем экране


class St(StatesGroup):
    proof = State()
    support = State()


class Adm(StatesGroup):
    add = State()
    bc = State()


def kb(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows])


def title(p):
    return f"{p.get('emoji', '📁')} {p['name']}"


def sellable(pid):
    p = PRODUCTS.get(pid)
    return p if p and p.get("price") else None


async def clean(uid):
    for mid in last.pop(uid, []):
        try:
            await bot.delete_message(uid, mid)
        except Exception:
            pass  # уже удалено или старше 48 часов


async def show(uid, text, markup=None, photos=None):
    """Один экран: прошлые сообщения бота стираются, новые запоминаются."""
    await clean(uid)
    photos = [photos] if isinstance(photos, str) else list(photos or [])
    ids, msg = [], None
    try:
        if len(photos) > 1:  # у альбома не бывает кнопок, поэтому текст идёт отдельно
            ids = [x.message_id for x in await bot.send_media_group(
                uid, [InputMediaPhoto(media=x) for x in photos])]
        elif photos:
            msg = await bot.send_photo(uid, photos[0], caption=text, reply_markup=markup)
    except Exception:
        logging.exception("фото не отправилось (часто это file_id от другого бота)")
    if msg is None:
        msg = await bot.send_message(uid, text, reply_markup=markup)
    last[uid] = ids + [msg.message_id]


async def rm(m: Message):
    try:
        await m.delete()
    except Exception:
        pass


async def tell(uid, text, code=False):
    try:
        await bot.send_message(uid, text, parse_mode="HTML" if code else None)
        return True
    except Exception:
        logging.exception("не доставлено пользователю %s", uid)
        return False


async def deliver(o):
    """Выдаёт оплаченный заказ o: берёт товар со склада. True = сообщение дошло."""
    item = take_item(o[2], o[0])
    if item:
        return await tell(o[1], f"✅ Заказ #{o[0]} оплачен!\n\n📦 Ваш товар:\n"
                                f"<code>{html.escape(item)}</code>", code=True)
    await bot.send_message(ADMIN_ID, f"⚠️ Заказ #{o[0]} оплачен, а товара «{PRODUCTS[o[2]]['name']}» "
                                     f"на складе нет! Свяжитесь с клиентом, ID: {o[1]}")
    return await tell(o[1], f"✅ Заказ #{o[0]} оплачен, но товар закончился. "
                            "Администратор скоро свяжется с вами.")


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

async def catalog_screen(uid):
    rows = [[Btn(text=title(p), callback_data=f"p:{i}")] for i, p in PRODUCTS.items()]
    await show(uid, "🛒 Каталог товаров\n\nВыберите товар:", kb(*rows))


@dp.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    add_user(m.from_user.id)
    await rm(m)
    await show(m.from_user.id, "👋 Добро пожаловать!\n\nВыберите раздел кнопками внизу ⬇", MENU)


@dp.message(F.text == CATALOG)
async def catalog(m: Message, state: FSMContext):
    await state.clear()
    await rm(m)
    await catalog_screen(m.from_user.id)


@dp.callback_query(F.data == "back")
async def back(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await catalog_screen(c.from_user.id)
    await c.answer()


@dp.message(F.text == CABINET)
async def cabinet(m: Message, state: FSMContext):
    await state.clear()
    await rm(m)
    hist = "\n".join(f"#{i} {PRODUCTS.get(p, {}).get('name', p)} — {STATUS.get(s, s)}"
                     for i, p, s in user_orders(m.from_user.id)) or "пока пусто"
    await show(m.from_user.id, f"👤 Мой кабинет\n\n🙍 Имя: {m.from_user.full_name}\n"
                               f"🆔 ID: {m.from_user.id}\n\n🧾 Последние заказы:\n{hist}")


@dp.message(F.text == SUPPORT)
async def support(m: Message, state: FSMContext):
    await state.set_state(St.support)
    await rm(m)
    await show(m.from_user.id, "🛠 Поддержка\n\nОпишите вопрос одним сообщением, "
                               "я передам его администратору.")


@dp.message(St.support, F.text)
async def support_send(m: Message, state: FSMContext):
    await bot.send_message(ADMIN_ID, f"🛠 Обращение\nID: {m.from_user.id}\n"
                                     f"{m.from_user.full_name}\n\n{m.text}")
    await state.clear()
    await show(m.from_user.id, "✅ Отправлено. Ответ придёт сюда, в бот.")


# ---------- товар и оплата ----------

@dp.callback_query(F.data.startswith("p:"))
async def product(c: CallbackQuery, state: FSMContext):
    await state.clear()
    p = PRODUCTS.get(c.data[2:])
    if not p:
        return await c.answer("Товар не найден", show_alert=True)
    n = stock(p["id"])
    lines = [f"📁 Выбран товар - {p['name']}", ""]
    if p.get("description"):
        lines += [f"ℹ️ {p['description']}", ""]
    lines.append(f"📦 Товара в наличии - {n}")
    lines.append(f"💰 Цена - {p['price']} ₽" if p.get("price") else "💰 Цена - уточняется")
    rows = []
    if not p.get("price"):
        lines += ["", "⏳ Покупка пока недоступна"]
    elif n == 0:
        lines += ["", "❌ Нет в наличии"]
    else:
        lines += ["", "Для оплаты воспользуйтесь кнопками ниже ⬇"]
        if CRYPTO_TOKEN:
            rows.append([Btn(text="₿ CryptoBot", callback_data=f"cr:{p['id']}")])
        rows.append([Btn(text="💳 Перевод на карту", callback_data=f"card:{p['id']}")])
    rows.append([Btn(text="↩️ Назад", callback_data="back")])
    await show(c.from_user.id, "\n".join(lines), kb(*rows), p.get("photo"))
    await c.answer()


@dp.callback_query(F.data.startswith("card:"))
async def pay_card(c: CallbackQuery, state: FSMContext):
    p = sellable(c.data[5:])
    if not p or not stock(p["id"]):
        return await c.answer("Товар недоступен", show_alert=True)
    oid = new_order(c.from_user.id, p["id"], "card", "wait_proof")
    await state.set_state(St.proof)
    await state.update_data(oid=oid)
    await show(c.from_user.id,
               f"🧾 Заказ #{oid}\n📁 {p['name']}\n💰 К оплате: {p['price']} ₽\n\n{CARD_INFO}\n\n"
               "📸 После перевода отправьте сюда скриншот оплаты.",
               kb([Btn(text="↩️ Назад", callback_data=f"p:{p['id']}")]))
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
    await show(m.from_user.id, "✅ Скриншот отправлен, ждите подтверждения.")


@dp.message(St.proof)
async def proof_bad(m: Message):
    await m.answer("📸 Нужен скриншот оплаты (фото). Чтобы выйти, нажмите кнопку меню.")


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
        sent = await deliver(o)
        mark = "✅ Подтверждён"
    else:
        sent = await tell(o[1], f"❌ Оплата по заказу #{oid} не подтверждена. Напишите в поддержку.")
        mark = "❌ Отклонён"
    if not sent:
        mark += " (пользователю НЕ доставлено!)"
    await c.message.edit_caption(caption=f"{c.message.caption}\n\n{mark}")
    await c.answer()


@dp.callback_query(F.data.startswith("cr:"))
async def pay_crypto(c: CallbackQuery):
    p = sellable(c.data[3:])
    if not p or not CRYPTO_TOKEN or not stock(p["id"]):
        return await c.answer("Оплата недоступна", show_alert=True)
    try:
        inv = await crypto("createInvoice", currency_type="fiat", fiat="RUB",
                           amount=str(p["price"]), description=p["name"])
    except Exception:
        logging.exception("createInvoice")
        return await c.answer("Не удалось создать счёт, попробуйте позже", show_alert=True)
    oid = new_order(c.from_user.id, p["id"], "crypto", "wait_crypto", inv["invoice_id"])
    url = inv.get("bot_invoice_url") or inv.get("pay_url")
    await show(c.from_user.id,
               f"🧾 Заказ #{oid}\n📁 {p['name']}\n💰 К оплате: {p['price']} ₽\n\n"
               f"🔗 Счёт: {url}\n\nПосле оплаты нажмите «Проверить».",
               kb([Btn(text="🔄 Проверить оплату", callback_data=f"chk:{oid}")],
                  [Btn(text="↩️ Назад", callback_data=f"p:{p['id']}")]))
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
        await clean(c.from_user.id)  # убираем экран со счётом, товар отправляется отдельным сообщением
        await deliver(o)
        await c.answer()
    else:
        await c.answer("Оплата пока не найдена", show_alert=True)


# ---------- админ ----------

def admin_kb():
    return kb([Btn(text="📦 Остатки", callback_data="adm:stock")],
              [Btn(text="➕ Пополнить", callback_data="adm:add")],
              [Btn(text="📢 Рассылка", callback_data="adm:bc")])


ADM_BACK = [Btn(text="↩️ Назад", callback_data="adm:menu")]


@dp.message(Command("admin"), ADM)
async def admin(m: Message, state: FSMContext):
    await state.clear()
    await rm(m)
    await show(m.from_user.id, "🛠 Админ-панель", admin_kb())


@dp.callback_query(ADM, F.data == "adm:menu")
async def adm_menu(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await show(c.from_user.id, "🛠 Админ-панель", admin_kb())
    await c.answer()


@dp.callback_query(ADM, F.data == "adm:stock")
async def adm_stock(c: CallbackQuery):
    lines = [f"{title(p)}: {stock(i)} шт." for i, p in PRODUCTS.items()]
    await show(c.from_user.id, "📦 Остатки\n\n" + "\n".join(lines), kb(ADM_BACK))
    await c.answer()


@dp.callback_query(ADM, F.data == "adm:add")
async def adm_add(c: CallbackQuery):
    rows = [[Btn(text=title(p), callback_data=f"adm:p:{i}")] for i, p in PRODUCTS.items()]
    await show(c.from_user.id, "➕ Для какого товара пополнить?", kb(*rows, ADM_BACK))
    await c.answer()


@dp.callback_query(ADM, F.data.startswith("adm:p:"))
async def adm_pick(c: CallbackQuery, state: FSMContext):
    pid = c.data[6:]
    if pid not in PRODUCTS:
        return await c.answer("Товар не найден", show_alert=True)
    await state.set_state(Adm.add)
    await state.update_data(pid=pid)
    await show(c.from_user.id, f"➕ {title(PRODUCTS[pid])}\n\nПришлите товары одним сообщением: "
                               "каждый с новой строки (ключ, логин:пароль и т.п.).\nОтмена: /admin")
    await c.answer()


@dp.message(Adm.add, F.text)
async def adm_save(m: Message, state: FSMContext):
    pid = (await state.get_data())["pid"]
    n = add_items(pid, m.text.splitlines())
    await state.clear()
    await show(m.from_user.id, f"✅ Добавлено: {n}\n📦 Теперь в наличии: {stock(pid)}", admin_kb())


@dp.callback_query(ADM, F.data == "adm:bc")
async def adm_bc(c: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.bc)
    await show(c.from_user.id, "📢 Рассылка\n\nПришлите сообщение (текст или фото с подписью), "
                               "оно уйдёт всем, кто запускал бота.\nОтмена: /admin")
    await c.answer()


@dp.message(Adm.bc)
async def adm_bc_preview(m: Message, state: FSMContext):
    await state.update_data(mid=m.message_id)
    await show(m.from_user.id, f"📢 Разослать это сообщение? Получателей: {len(all_users())}",
               kb([Btn(text="✅ Отправить", callback_data="bc:yes"),
                   Btn(text="❌ Отмена", callback_data="adm:menu")]))


@dp.callback_query(ADM, F.data == "bc:yes")
async def bc_send(c: CallbackQuery, state: FSMContext):
    mid = (await state.get_data()).get("mid")
    if not mid:
        return await c.answer("Сначала пришлите сообщение", show_alert=True)
    await state.clear()
    await c.answer("Отправляю…")
    ok = bad = 0
    # shortcut: без обработки флуд-лимита Telegram, при тысячах подписчиков добавить TelegramRetryAfter
    for uid in all_users():
        try:
            await bot.copy_message(uid, ADMIN_ID, mid)
            ok += 1
        except Exception:
            bad += 1
        await asyncio.sleep(0.05)
    await show(ADMIN_ID, f"📢 Рассылка завершена\n✅ Доставлено: {ok}\n⚠️ Не доставлено: {bad}", admin_kb())


@dp.message(ADM, F.reply_to_message, F.text)
async def admin_reply(m: Message):
    """Админ отвечает реплаем на обращение или чек: ответ уходит пользователю с ID из сообщения."""
    src = m.reply_to_message.text or m.reply_to_message.caption or ""
    found = re.search(r"ID: (\d+)", src)
    if found and not await tell(int(found[1]), f"💬 Ответ поддержки:\n\n{m.text}"):
        await m.answer("Не доставлено (пользователь заблокировал бота?)")


@dp.message(ADM, F.photo)
async def photo_id(m: Message):
    """Админ шлёт боту скрин, в ответ получает file_id для поля photo в catalog.json."""
    await m.answer(m.photo[-1].file_id)


async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
