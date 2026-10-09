"""База заказов, товаров и пользователей."""
import os
import sqlite3

db = sqlite3.connect(os.environ.get("DB_PATH", "shop.db"), check_same_thread=False)
db.executescript("""
create table if not exists orders (
    id integer primary key, user_id integer, product text,
    method text, status text, invoice integer,
    currency text not null default 'RUB', amount real
);
create table if not exists items (id integer primary key, product text, data text, order_id integer);
create table if not exists users (id integer primary key);
create table if not exists photos (product text primary key, file_id text);
create table if not exists prices (product text, currency text, amount real,
    primary key(product, currency));
create table if not exists hidden_products (product text primary key);
create table if not exists reviews (order_id integer primary key, rating integer, text text);
""")

# Миграция старой базы без потери заказов.
cols = {r[1] for r in db.execute("pragma table_info(orders)")}
if "currency" not in cols:
    db.execute("alter table orders add column currency text not null default 'RUB'")
if "amount" not in cols:
    db.execute("alter table orders add column amount real")
db.commit()

def get_price(pid, currency, default=None):
    row = db.execute("select amount from prices where product=? and currency=?", (pid, currency)).fetchone()
    return row[0] if row else default

def set_price(pid, currency, amount):
    db.execute("insert or replace into prices(product,currency,amount) values(?,?,?)", (pid, currency, amount))
    db.commit()

def is_hidden(pid):
    return db.execute("select 1 from hidden_products where product=?", (pid,)).fetchone() is not None

def hide_product(pid):
    db.execute("insert or ignore into hidden_products(product) values(?)", (pid,))
    db.commit()

def unhide_product(pid):
    db.execute("delete from hidden_products where product=?", (pid,))
    db.commit()

def new_order(uid, pid, method, status, invoice=None, currency="RUB", amount=None):
    cur = db.execute("insert into orders (user_id, product, method, status, invoice, currency, amount) "
                     "values (?,?,?,?,?,?,?)", (uid, pid, method, status, invoice, currency, amount))
    db.commit()
    return cur.lastrowid

def get_order(oid):
    return db.execute("select id, user_id, product, method, status, invoice, currency, amount "
                      "from orders where id=?", (oid,)).fetchone()

def move(oid, old, new):
    cur = db.execute("update orders set status=? where id=? and status=?", (new, oid, old))
    db.commit()
    return cur.rowcount == 1

def user_orders(uid, limit=5):
    return db.execute("select id, product, status from orders where user_id=? "
                      "and status!='wait_proof' order by id desc limit ?", (uid, limit)).fetchall()

def stock(pid):
    return db.execute("select count(*) from items where product=? and order_id is null", (pid,)).fetchone()[0]

def add_items(pid, lines):
    rows = [(pid, x.strip()) for x in lines if x.strip()]
    db.executemany("insert into items (product, data) values (?,?)", rows)
    db.commit()
    return len(rows)

def take_item(pid, oid):
    db.execute("update items set order_id=? where order_id is null and id="
               "(select id from items where product=? and order_id is null order by id limit 1) "
               "and not exists (select 1 from items where order_id=?)", (oid, pid, oid))
    db.commit()
    row = db.execute("select data from items where order_id=?", (oid,)).fetchone()
    return row[0] if row else None

def add_review(oid, rating):
    """True = оценка сохранена; повторная оценка того же заказа игнорируется."""
    cur = db.execute("insert or ignore into reviews (order_id, rating) values (?,?)", (oid, rating))
    db.commit()
    return cur.rowcount == 1

def set_review_text(oid, text):
    db.execute("update reviews set text=? where order_id=?", (text, oid))
    db.commit()

def add_user(uid):
    db.execute("insert or ignore into users (id) values (?)", (uid,))
    db.commit()

def all_users():
    return [r[0] for r in db.execute("select id from users")]

def set_photo(pid, file_id):
    db.execute("insert or replace into photos (product, file_id) values (?,?)", (pid, file_id))
    db.commit()

def get_photo(pid):
    row = db.execute("select file_id from photos where product=?", (pid,)).fetchone()
    return row[0] if row else None


def list_stock_items(pid):
    """Свободные ключи/аккаунты, ещё не привязанные к заказу."""
    return db.execute(
        "select id, data from items where product=? and order_id is null order by id",
        (pid,)
    ).fetchall()

def delete_stock_item(item_id, pid):
    """Удаляет только свободную запись склада, не трогая уже выданные заказы."""
    cur = db.execute(
        "delete from items where id=? and product=? and order_id is null",
        (item_id, pid)
    )
    db.commit()
    return cur.rowcount == 1
