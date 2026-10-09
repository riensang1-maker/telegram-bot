"""База заказов, товаров (ключей) и пользователей. Без aiogram, поэтому проверяется отдельно."""
import os
import sqlite3

# shortcut: база лежит файлом рядом с ботом; на хостинге без постоянного диска
# она сбросится при редеплое, тогда подключить volume и указать путь в DB_PATH
db = sqlite3.connect(os.environ.get("DB_PATH", "shop.db"))
db.executescript("""
create table if not exists orders (id integer primary key, user_id integer,
    product text, method text, status text, invoice integer);
create table if not exists items (id integer primary key, product text,
    data text, order_id integer);
create table if not exists users (id integer primary key);
""")


def new_order(uid, pid, method, status, invoice=None):
    cur = db.execute("insert into orders (user_id, product, method, status, invoice) "
                     "values (?,?,?,?,?)", (uid, pid, method, status, invoice))
    db.commit()
    return cur.lastrowid


def get_order(oid):
    return db.execute("select id, user_id, product, method, status, invoice "
                      "from orders where id=?", (oid,)).fetchone()


def move(oid, old, new):
    """Меняет статус, только если он сейчас old. True = мы первые, выдача пройдёт один раз."""
    cur = db.execute("update orders set status=? where id=? and status=?", (new, oid, old))
    db.commit()
    return cur.rowcount == 1


def user_orders(uid, limit=5):
    return db.execute("select id, product, status from orders where user_id=? "
                      "and status!='wait_proof' order by id desc limit ?", (uid, limit)).fetchall()


def stock(pid):
    return db.execute("select count(*) from items where product=? and order_id is null",
                      (pid,)).fetchone()[0]


def add_items(pid, lines):
    rows = [(pid, x.strip()) for x in lines if x.strip()]
    db.executemany("insert into items (product, data) values (?,?)", rows)
    db.commit()
    return len(rows)


def take_item(pid, oid):
    """Отдаёт заказу один свободный товар. Повторный вызов для того же заказа вернёт тот же."""
    db.execute("update items set order_id=? where order_id is null and id="
               "(select id from items where product=? and order_id is null order by id limit 1) "
               "and not exists (select 1 from items where order_id=?)", (oid, pid, oid))
    db.commit()
    row = db.execute("select data from items where order_id=?", (oid,)).fetchone()
    return row[0] if row else None


def add_user(uid):
    db.execute("insert or ignore into users (id) values (?)", (uid,))
    db.commit()


def all_users():
    return [r[0] for r in db.execute("select id from users")]
