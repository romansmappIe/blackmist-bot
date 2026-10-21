"""Хранилище магазина (SQLite): пользователи, каталог, корзина, заказы."""
import sqlite3
import threading
from datetime import datetime

CATEGORIES = [
    ("liquids", "💧 Жидкости"),
    ("disposable", "💨 Одноразки"),
    ("pods", "🔋 Под-системы"),
    ("parts", "🔧 Расходники"),
]

# Стартовые товары. id 1–9 должны совпадать со списком P в webapp/index.html
SEED = [
    ("liquids", "Клубника-Киви 30 мл", "Солевой, 20 мг", 450, 25),
    ("liquids", "Мята 30 мл", "Солевой, 20 мг", 450, 18),
    ("liquids", "Табак 30 мл", "Солевой, 20 мг", 430, 12),
    ("disposable", "Одноразка «Манго»", "800 затяжек", 700, 30),
    ("disposable", "Одноразка «Арбуз»", "1500 затяжек", 950, 20),
    ("pods", "Под-система Slim", "450 мАч, чёрная", 1800, 8),
    ("pods", "Под-система Pro", "800 мАч, серая", 2600, 5),
    ("parts", "Картридж 1.0 Ом", "2 шт. для Slim", 350, 40),
    ("parts", "Испаритель 0.8 Ом", "5 шт. для Pro", 600, 22),
]

MAX_PER_ITEM = 50


class Store:
    def __init__(self, path="shop.db"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self._init()

    def _init(self):
        with self.lock, self.db:
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, adult INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS products(
                id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL, title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '', price INTEGER NOT NULL, stock INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS cart(
                user_id INTEGER NOT NULL, product_id INTEGER NOT NULL, qty INTEGER NOT NULL,
                PRIMARY KEY(user_id, product_id));
            CREATE TABLE IF NOT EXISTS orders(
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, username TEXT,
                name TEXT, contact TEXT, address TEXT, total INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'new', created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS order_items(
                order_id INTEGER NOT NULL, product_id INTEGER NOT NULL, title TEXT NOT NULL,
                price INTEGER NOT NULL, qty INTEGER NOT NULL);
            """)
            if not self.db.execute("SELECT 1 FROM products LIMIT 1").fetchone():
                self.db.executemany(
                    "INSERT INTO products(category,title,description,price,stock) VALUES(?,?,?,?,?)", SEED)

    # ---- пользователи ----
    def is_adult(self, uid):
        with self.lock:
            r = self.db.execute("SELECT adult FROM users WHERE id=?", (uid,)).fetchone()
            return bool(r and r["adult"])

    def set_adult(self, uid, value):
        with self.lock, self.db:
            self.db.execute("INSERT INTO users(id,adult) VALUES(?,?) "
                            "ON CONFLICT(id) DO UPDATE SET adult=excluded.adult", (uid, int(value)))

    # ---- каталог ----
    def products(self, category):
        with self.lock:
            return self.db.execute(
                "SELECT * FROM products WHERE category=? AND stock>0 ORDER BY id", (category,)).fetchall()

    def product(self, pid):
        with self.lock:
            return self.db.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()

    def add_product(self, category, title, description, price, stock):
        with self.lock, self.db:
            cur = self.db.execute(
                "INSERT INTO products(category,title,description,price,stock) VALUES(?,?,?,?,?)",
                (category, title, description, price, stock))
            return cur.lastrowid

    def set_stock(self, pid, stock):
        with self.lock, self.db:
            self.db.execute("UPDATE products SET stock=? WHERE id=?", (stock, pid))

    def set_price(self, pid, price):
        with self.lock, self.db:
            self.db.execute("UPDATE products SET price=? WHERE id=?", (price, pid))

    # ---- корзина ----
    def cart_add(self, uid, pid):
        with self.lock, self.db:
            p = self.product(pid)
            if not p:
                return False, "Товар не найден"
            r = self.db.execute("SELECT qty FROM cart WHERE user_id=? AND product_id=?", (uid, pid)).fetchone()
            qty = (r["qty"] if r else 0) + 1
            if qty > p["stock"]:
                return False, f"Больше нет в наличии (осталось {p['stock']})"
            if qty > MAX_PER_ITEM:
                return False, f"Не больше {MAX_PER_ITEM} шт. одного товара"
            self.db.execute("INSERT INTO cart(user_id,product_id,qty) VALUES(?,?,?) "
                            "ON CONFLICT(user_id,product_id) DO UPDATE SET qty=excluded.qty", (uid, pid, qty))
            return True, "Добавлено в корзину"

    def cart_items(self, uid):
        with self.lock:
            return self.db.execute(
                "SELECT p.id, p.title, p.price, c.qty FROM cart c JOIN products p ON p.id=c.product_id "
                "WHERE c.user_id=? ORDER BY p.id", (uid,)).fetchall()

    def cart_total(self, uid):
        return sum(i["price"] * i["qty"] for i in self.cart_items(uid))

    def cart_clear(self, uid):
        with self.lock, self.db:
            self.db.execute("DELETE FROM cart WHERE user_id=?", (uid,))

    # ---- заказы ----
    def create_order(self, uid, username, name, contact, address):
        """Проверяет остатки, списывает их, сохраняет заказ, очищает корзину. -> (ok, id | текст ошибки)"""
        with self.lock, self.db:
            items = self.cart_items(uid)
            if not items:
                return False, "корзина пуста"
            for i in items:
                p = self.product(i["id"])
                if not p or p["stock"] < i["qty"]:
                    return False, f"«{i['title']}» закончился или остатка не хватает"
            total = sum(i["price"] * i["qty"] for i in items)
            cur = self.db.execute(
                "INSERT INTO orders(user_id,username,name,contact,address,total,created) VALUES(?,?,?,?,?,?,?)",
                (uid, username, name, contact, address, total, datetime.now().strftime("%d.%m %H:%M")))
            oid = cur.lastrowid
            for i in items:
                self.db.execute("INSERT INTO order_items VALUES(?,?,?,?,?)",
                                (oid, i["id"], i["title"], i["price"], i["qty"]))
                self.db.execute("UPDATE products SET stock=stock-? WHERE id=?", (i["qty"], i["id"]))
            self.db.execute("DELETE FROM cart WHERE user_id=?", (uid,))
            return True, oid

    def order(self, oid):
        with self.lock:
            o = self.db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
            items = self.db.execute("SELECT * FROM order_items WHERE order_id=?", (oid,)).fetchall()
            return o, items

    def recent_orders(self, limit=15):
        with self.lock:
            return self.db.execute("SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
