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
print("store OK")
