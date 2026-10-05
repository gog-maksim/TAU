"""
Шаг 2: read-only доступ к PostgreSQL.
Только чтение — запись/миграция будут в п.4-7.
"""
from psycopg2.extras import RealDictCursor
from db_config import get_connection


def _fetch_all(query, params=None):
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(query, params or ())
        rows = cur.fetchall()
        cur.close()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_tariffs(active_only=True):
    q = "SELECT id, name, hourly_rate FROM tariffs"
    if active_only:
        q += " WHERE is_active = TRUE"
    return _fetch_all(q + " ORDER BY id")


def get_discounts(active_only=True):
    q = "SELECT id, name, percent FROM discounts"
    if active_only:
        q += " WHERE is_active = TRUE"
    return _fetch_all(q + " ORDER BY id")


def get_occupied_numbers():
    """Номера занятых мест (departure_time IS NULL). JOIN для получения number, а не id."""
    return _fetch_all("""
        SELECT s.number
        FROM parking_sessions ps
        JOIN parking_spots s ON s.id = ps.parking_spot_id
        WHERE ps.departure_time IS NULL
        ORDER BY s.number
    """)


def get_active_sessions():
    """Текущие автомобили на стоянке — то, что понадобится в п.4."""
    return _fetch_all("""
        SELECT
            v.plate_number AS plate,
            v.make_model   AS brand,
            o.full_name    AS owner,
            o.phone        AS phone,
            s.number       AS place,
            ps.arrival_time AS time,
            COALESCE(d.percent, 0) AS discount_percent,
            COALESCE(d.name, 'Нет') AS discount_name,
            t.hourly_rate  AS hourly_rate,
            ps.total_cost  AS total_cost
        FROM parking_sessions ps
        JOIN vehicles v ON v.id = ps.vehicle_id
        JOIN owners o   ON o.id = v.owner_id
        JOIN parking_spots s ON s.id = ps.parking_spot_id
        JOIN tariffs t  ON t.id = ps.tariff_id
        LEFT JOIN discounts d ON d.id = ps.discount_id
        WHERE ps.departure_time IS NULL
        ORDER BY s.number
    """)


def get_history(limit=100):
    return _fetch_all("""
        SELECT
            ps.id AS session_id,
            v.plate_number AS plate,
            s.number AS place,
            ps.arrival_time,
            ps.departure_time,
            ps.total_cost
        FROM parking_sessions ps
        JOIN vehicles v ON v.id = ps.vehicle_id
        JOIN parking_spots s ON s.id = ps.parking_spot_id
        ORDER BY ps.id DESC
        LIMIT %s
    """, (limit,))


def get_snapshot():
    """Сводка для проверки п.2 одним вызовом."""
    tariffs = get_tariffs()
    discounts = get_discounts()
    occupied = get_occupied_numbers()
    active = get_active_sessions()
    return {
        "tariffs": tariffs,
        "discounts": discounts,
        "total_spots": 100,
        "occupied": [r["number"] for r in occupied],
        "active_count": len(active),
        "active": active,
    }
