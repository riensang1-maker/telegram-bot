# запуск: python check_catalog.py, проверяет catalog.json перед деплоем
import json

ids = set()
with open("catalog.json", encoding="utf-8") as f:
    for p in json.load(f):
        assert {"id", "name", "description", "price"} <= p.keys(), p
        assert p["id"] not in ids and len(p["id"]) <= 40, p["id"]
        ids.add(p["id"])
        assert isinstance(p.get("ref_bonus", 0), int) and p.get("ref_bonus", 0) >= 0, p  # бонус за реферала, ₽
        assert p["price"] is None or (isinstance(p["price"], (int, float)) and p["price"] > 0), p
print("catalog OK:", len(ids), "товара")
