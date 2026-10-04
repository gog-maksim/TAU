"""
Конфигурация подключения к PostgreSQL.
"""
import psycopg2
from psycopg2.extras import RealDictCursor

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "database": "parking_db",
    "user": "postgres",
    "password": "postgres",
}


def get_connection():
    """Возвращает соединение с БД."""
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    return conn


def get_cursor(conn=None, dict_cursor=False):
    """Возвращает курсор. Если dict_cursor=True — результаты как словари."""
    if conn is None:
        conn = get_connection()
    cursor_factory = RealDictCursor if dict_cursor else None
    return conn.cursor(cursor_factory=cursor_factory), conn
