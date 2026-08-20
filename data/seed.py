import sqlite3
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent / "retail.db"


def seed_database() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()

    connection = sqlite3.connect(DB_PATH)
    try:
        connection.executescript(
            """
            CREATE TABLE customers (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                city TEXT NOT NULL,
                segment TEXT NOT NULL
            );

            CREATE TABLE products (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                price REAL NOT NULL
            );

            CREATE TABLE orders (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL,
                order_date TEXT NOT NULL,
                status TEXT NOT NULL,
                FOREIGN KEY (customer_id) REFERENCES customers(id)
            );

            CREATE TABLE order_items (
                id INTEGER PRIMARY KEY,
                order_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                unit_price REAL NOT NULL,
                FOREIGN KEY (order_id) REFERENCES orders(id),
                FOREIGN KEY (product_id) REFERENCES products(id)
            );
            """
        )

        connection.executemany(
            "INSERT INTO customers (id, name, city, segment) VALUES (?, ?, ?, ?)",
            [
                (1, "Amina Hassan", "Riyadh", "Consumer"),
                (2, "Omar Saleh", "Jeddah", "Corporate"),
                (3, "Noura Ali", "Dammam", "Consumer"),
                (4, "Fahad Khalid", "Riyadh", "Small Business"),
                (5, "Sara Ahmed", "Jeddah", "Corporate"),
            ],
        )

        connection.executemany(
            "INSERT INTO products (id, name, category, price) VALUES (?, ?, ?, ?)",
            [
                (1, "Laptop Pro 14", "Electronics", 5200.0),
                (2, "Wireless Mouse", "Accessories", 120.0),
                (3, "Office Chair", "Furniture", 850.0),
                (4, "Standing Desk", "Furniture", 1800.0),
                (5, "Noise Canceling Headphones", "Electronics", 940.0),
                (6, "USB-C Hub", "Accessories", 210.0),
            ],
        )

        connection.executemany(
            "INSERT INTO orders (id, customer_id, order_date, status) VALUES (?, ?, ?, ?)",
            [
                (1, 1, "2026-01-08", "completed"),
                (2, 2, "2026-01-15", "completed"),
                (3, 3, "2026-02-03", "completed"),
                (4, 4, "2026-02-18", "pending"),
                (5, 5, "2026-03-05", "completed"),
                (6, 1, "2026-03-17", "completed"),
                (7, 2, "2026-04-02", "cancelled"),
                (8, 3, "2026-04-20", "completed"),
                (9, 4, "2026-05-11", "completed"),
                (10, 5, "2026-06-06", "completed"),
            ],
        )

        connection.executemany(
            """
            INSERT INTO order_items (id, order_id, product_id, quantity, unit_price)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (1, 1, 1, 1, 5200.0),
                (2, 1, 2, 2, 120.0),
                (3, 2, 3, 4, 850.0),
                (4, 2, 6, 5, 210.0),
                (5, 3, 5, 2, 940.0),
                (6, 4, 4, 1, 1800.0),
                (7, 5, 1, 1, 5200.0),
                (8, 5, 6, 3, 210.0),
                (9, 6, 2, 6, 120.0),
                (10, 6, 5, 1, 940.0),
                (11, 7, 3, 1, 850.0),
                (12, 8, 4, 2, 1800.0),
                (13, 8, 6, 2, 210.0),
                (14, 9, 3, 2, 850.0),
                (15, 9, 2, 4, 120.0),
                (16, 10, 5, 3, 940.0),
                (17, 10, 6, 6, 210.0),
            ],
        )

        connection.commit()
    finally:
        connection.close()

    print(f"Seeded database at {DB_PATH}")


if __name__ == "__main__":
    seed_database()
