# запуск: python check_store.py, проверяет логику склада и заказов на пустой базе в памяти
import os

os.environ["DB_PATH"] = ":memory:"
import store as s

assert s.add_items("g", ["a", "  ", "b\n", "c"]) == 3 and s.stock("g") == 3
o1 = s.new_order(1, "g", "card", "review")
assert s.move(o1, "review", "paid") and not s.move(o1, "review", "paid")  # выдача один раз
assert s.take_item("g", o1) == "a" and s.stock("g") == 2
assert s.take_item("g", o1) == "a" and s.stock("g") == 2  # повтор не съедает второй товар
o2 = s.new_order(2, "g", "card", "review")
assert s.take_item("g", o2) == "b"
o3 = s.new_order(3, "g", "card", "review")
assert s.take_item("g", o3) == "c" and s.stock("g") == 0
o4 = s.new_order(4, "g", "card", "review")
assert s.take_item("g", o4) is None  # закончились
s.add_user(7); s.add_user(7)
assert s.all_users() == [7]
s.set_photo("g", "f1"); s.set_photo("g", "f2")
assert s.get_photo("g") == "f2" and s.get_photo("x") is None
assert s.add_review(o1, 5) and not s.add_review(o1, 1)  # один отзыв на заказ
s.set_review_text(o1, "ок")
assert s.db.execute("select rating, text from reviews where order_id=?", (o1,)).fetchone() == (5, "ок")
assert s.sold_today() == 1  # оплачен только o1
s.add_user(10); s.add_user(11, 10); s.add_user(11, 7); s.add_user(12, 12)
assert s.ref_stats(10) == (1, 0, 0)  # 11 привязан к 10; повторная привязка не работает
assert s.db.execute("select ref_by from users where id=12").fetchone() == (None,)  # самореферал игнорируется
o5 = s.new_order(11, "g", "card", "review"); s.move(o5, "review", "paid")
assert s.credit_ref(o5, 10) == 10 and s.credit_ref(o5, 10) is None  # бонус один раз на заказ
assert s.credit_ref(o1, 10) is None  # у o1 пригласившего нет
assert s.balance(10) == 10 and s.ref_stats(10) == (1, 1, 10)
assert not s.pay_balance(10, 15) and s.pay_balance(10, 10) and s.balance(10) == 0
print("store OK")
