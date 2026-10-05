"""
Конфигурация подключения к PostgreSQL.
Шаг 1: единая точка подключения. Поддерживает ENV, иначе дефолты для локалки.
  PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD
"""
import os
import psycopg2
from psycopg2.extras import RealDictCursor

DB_CONFIG = {
    "host": os.getenv("PGHOST", "localhost"),
    "port": int(os.getenv("PGPORT", "5432")),
    "database": os.getenv("PGDATABASE", "parking_db"),
    "user": os.getenv("PGUSER", "postgres"),
    "password": os.getenv("PGPASSWORD", "postgres"),
    "connect_timeout": 5,
}


def get_connection():
    """Возвращает соединение с БД."""
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    return conn


def test_connection() -> tuple[bool, str]:
    """Проверка связи для п.1. Возвращает (ok, message)."""
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT version()")
        ver = cur.fetchone()[0]
        cur.close()
        conn.close()
        return True, ver
    except Exception as e:
        return False, str(e)


def get_cursor(conn=None, dict_cursor=False):
    """Возвращает курсор. Если dict_cursor=True — результаты как словари."""
    if conn is None:
        conn = get_connection()
    cursor_factory = RealDictCursor if dict_cursor else None
    return conn.cursor(cursor_factory=cursor_factory), conn
