"""
п.2-5: доступ к PostgreSQL. п.5 добавляет запись въезда.
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


# ---------- п.5: регистрация въезда ----------

def create_parking_entry(plate, brand, owner_name, phone, place_number,
                         tariff_id, discount_id=None, arrival_time=None):
    """Создает owner/vehicle/session. Возвращает session_id.

    Проверки уровня БД (дополнительно к проверкам формы):
    - место свободно (нет активной сессии)
    - авто не на стоянке (нет активной сессии по номеру)
    """
    from datetime import datetime
    arrival_time = arrival_time or datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        # место существует?
        cur.execute("SELECT id FROM parking_spots WHERE number = %s", (place_number,))
        spot = cur.fetchone()
        if not spot:
            raise ValueError(f"Место №{place_number} не существует")
        spot_id = spot["id"]
        # место свободно?
        cur.execute("SELECT id FROM parking_sessions WHERE parking_spot_id = %s AND departure_time IS NULL",
                    (spot_id,))
        if cur.fetchone():
            raise ValueError(f"Место №{place_number} уже занято (БД)")
        # авто уже на стоянке?
        cur.execute("""
            SELECT ps.id FROM parking_sessions ps
            JOIN vehicles v ON v.id = ps.vehicle_id
            WHERE upper(v.plate_number) = upper(%s) AND ps.departure_time IS NULL
        """, (plate,))
        if cur.fetchone():
            raise ValueError(f"Автомобиль {plate} уже на стоянке (БД)")
        # owner: ищем по ФИО+телефон, иначе создаем
        cur.execute("SELECT id FROM owners WHERE full_name = %s AND COALESCE(phone,'') = COALESCE(%s,'')",
                    (owner_name, phone or ""))
        owner = cur.fetchone()
        if owner:
            owner_id = owner["id"]
        else:
            cur.execute("INSERT INTO owners (full_name, phone) VALUES (%s, %s) RETURNING id",
                        (owner_name, phone or ""))
            owner_id = cur.fetchone()["id"]
        # vehicle: по номеру
        cur.execute("SELECT id, owner_id FROM vehicles WHERE upper(plate_number) = upper(%s)", (plate,))
        veh = cur.fetchone()
        if veh:
            vehicle_id = veh["id"]
            # обновляем данные авто/владельца к последним
            cur.execute("UPDATE vehicles SET make_model = %s, owner_id = %s WHERE id = %s",
                        (brand, owner_id, vehicle_id))
        else:
            cur.execute("INSERT INTO vehicles (plate_number, make_model, owner_id) VALUES (%s, %s, %s) RETURNING id",
                        (plate, brand, owner_id))
            vehicle_id = cur.fetchone()["id"]
        # session
        cur.execute("""
            INSERT INTO parking_sessions (vehicle_id, parking_spot_id, tariff_id, discount_id, arrival_time)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
        """, (vehicle_id, spot_id, tariff_id, discount_id, arrival_time))
        session_id = cur.fetchone()["id"]
        conn.commit()
        return session_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
