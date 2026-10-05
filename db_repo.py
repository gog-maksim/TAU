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
    q = "SELECT id, name, percent, is_active FROM discounts"
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


# ---------- п.6: оплата и расчёт ----------

def get_active_session_by_plate(plate):
    """Активная сессия по госномеру. None если авто не в БД (старые JSON-записи)."""
    rows = _fetch_all("""
        SELECT
            ps.id AS session_id,
            ps.arrival_time,
            t.hourly_rate,
            COALESCE(d.percent, 0) AS discount_percent,
            ps.total_cost AS stored_cost
        FROM parking_sessions ps
        JOIN vehicles v ON v.id = ps.vehicle_id
        JOIN tariffs t ON t.id = ps.tariff_id
        LEFT JOIN discounts d ON d.id = ps.discount_id
        WHERE upper(v.plate_number) = upper(%s) AND ps.departure_time IS NULL
    """, (plate,))
    return rows[0] if rows else None


def calc_current_cost(session_id):
    """Свежая стоимость через calc_session_cost(arrival, now(), rate, discount)."""
    rows = _fetch_all("""
        SELECT calc_session_cost(ps.arrival_time, NULL, t.hourly_rate, COALESCE(d.percent, 0)) AS cost
        FROM parking_sessions ps
        JOIN tariffs t ON t.id = ps.tariff_id
        LEFT JOIN discounts d ON d.id = ps.discount_id
        WHERE ps.id = %s
    """, (session_id,))
    return int(rows[0]["cost"]) if rows else 0


def add_payment(session_id, amount):
    """Записать платеж. amount > 0."""
    if amount <= 0:
        return None
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO payments (session_id, amount) VALUES (%s, %s) RETURNING id",
                    (session_id, int(amount)))
        pid = cur.fetchone()[0]
        conn.commit()
        return pid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_payments_total(session_id):
    rows = _fetch_all("SELECT COALESCE(SUM(amount),0) AS total FROM payments WHERE session_id = %s",
                      (session_id,))
    return int(rows[0]["total"]) if rows else 0


# ---------- п.7: выезд без удаления ----------

def close_session(session_id, departure_time=None):
    """Проставляет departure_time (автоматически now()), запись остается для истории.
    Возвращает (departure_time, total_cost) после закрытия."""
    from datetime import datetime
    departure_time = departure_time or datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            UPDATE parking_sessions
            SET departure_time = %s
            WHERE id = %s AND departure_time IS NULL
            RETURNING departure_time, total_cost
        """, (departure_time, session_id))
        row = cur.fetchone()
        conn.commit()
        if not row:
            raise ValueError(f"Сессия {session_id} уже закрыта или не существует")
        return dict(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------- п.8: чёрный список ----------

def get_blacklist(active_only=False):
    q = """
        SELECT b.id, v.plate_number AS plate, v.make_model AS brand,
               b.reason, b.date_added, b.is_active
        FROM blacklist b
        JOIN vehicles v ON v.id = b.vehicle_id
    """
    if active_only:
        q += " WHERE b.is_active = TRUE"
    return _fetch_all(q + " ORDER BY b.id DESC")


def is_blacklisted(plate):
    """Активная запись ЧС по номеру или None."""
    rows = _fetch_all("""
        SELECT b.id, b.reason, b.date_added
        FROM blacklist b
        JOIN vehicles v ON v.id = b.vehicle_id
        WHERE upper(v.plate_number) = upper(%s) AND b.is_active = TRUE
        LIMIT 1
    """, (plate,))
    return rows[0] if rows else None


def add_blacklist_entry(plate, reason=""):
    """Добавляет авто в ЧС. Создаёт заглушки owner/vehicle если номера ещё нет в БД."""
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT id FROM vehicles WHERE upper(plate_number) = upper(%s)", (plate,))
        veh = cur.fetchone()
        if veh:
            vehicle_id = veh["id"]
        else:
            cur.execute("SELECT id FROM owners WHERE full_name = '(ЧС)' LIMIT 1")
            stub = cur.fetchone()
            owner_id = stub["id"] if stub else None
            if owner_id is None:
                cur.execute("INSERT INTO owners (full_name, phone) VALUES ('(ЧС)', '') RETURNING id")
                owner_id = cur.fetchone()["id"]
            cur.execute("INSERT INTO vehicles (plate_number, make_model, owner_id) VALUES (%s, '(ЧС)', %s) RETURNING id",
                        (plate, owner_id))
            vehicle_id = cur.fetchone()["id"]
        cur.execute("SELECT id FROM blacklist WHERE vehicle_id = %s AND is_active = TRUE", (vehicle_id,))
        if cur.fetchone():
            raise ValueError(f"Автомобиль {plate} уже в чёрном списке")
        cur.execute("INSERT INTO blacklist (vehicle_id, reason) VALUES (%s, %s) RETURNING id",
                    (vehicle_id, reason or ""))
        bid = cur.fetchone()["id"]
        conn.commit()
        return bid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_blacklist_active(entry_id, active):
    """Мягкое удаление/восстановление: только флаг is_active, запись сохраняется."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE blacklist SET is_active = %s WHERE id = %s", (bool(active), entry_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------- п.8: скидки ----------

def add_discount(name, percent):
    name = (name or "").strip()
    if not name:
        raise ValueError("Название скидки пустое")
    percent = int(percent)
    if not 0 <= percent <= 100:
        raise ValueError("Скидка должна быть 0–100%")
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("INSERT INTO discounts (name, percent) VALUES (%s, %s) RETURNING id",
                    (name, percent))
        did = cur.fetchone()["id"]
        conn.commit()
        return did
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_discount(discount_id, name, percent):
    """Версионирование как у тарифов: старую деактивируем, создаём новую.
    Прошлые стоянки ссылаются на старую запись — их расчёты не меняются."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Название скидки пустое")
    percent = int(percent)
    if not 0 <= percent <= 100:
        raise ValueError("Скидка должна быть 0–100%")
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("UPDATE discounts SET is_active = FALSE WHERE id = %s", (discount_id,))
        cur.execute("INSERT INTO discounts (name, percent) VALUES (%s, %s) RETURNING id",
                    (name, percent))
        nid = cur.fetchone()["id"]
        conn.commit()
        return nid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_discount_active(discount_id, active):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE discounts SET is_active = %s WHERE id = %s", (bool(active), discount_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------- п.8: тариф (версионирование, старые не трогаем) ----------

def replace_active_tariff(hourly_rate):
    """Создаёт новый активный тариф, старый деактивирует (история сессий не меняется)."""
    hourly_rate = int(hourly_rate)
    if hourly_rate <= 0:
        raise ValueError("Тариф должен быть > 0")
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT id, name FROM tariffs WHERE is_active = TRUE ORDER BY id DESC LIMIT 1")
        cur_t = cur.fetchone()
        name = cur_t["name"] if cur_t else "Стандартный"
        cur.execute("UPDATE tariffs SET is_active = FALSE WHERE is_active = TRUE")
        cur.execute("INSERT INTO tariffs (name, hourly_rate) VALUES (%s, %s) RETURNING id",
                    (name, hourly_rate))
        nid = cur.fetchone()["id"]
        conn.commit()
        return nid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------- п.9: история завершённых стоянок ----------

def get_full_history(limit=500):
    """Завершённые сессии с оплатой: долг = total - sum(payments)."""
    return _fetch_all("""
        SELECT
            ps.id AS session_id,
            v.plate_number AS plate,
            v.make_model AS brand,
            o.full_name AS owner,
            s.number AS place,
            ps.arrival_time AS arrival,
            ps.departure_time AS departure,
            t.hourly_rate AS rate,
            COALESCE(d.percent, 0) AS discount,
            ps.total_cost AS total,
            COALESCE((SELECT SUM(amount) FROM payments p WHERE p.session_id = ps.id), 0) AS paid,
            ps.total_cost - COALESCE((SELECT SUM(amount) FROM payments p WHERE p.session_id = ps.id), 0) AS debt
        FROM parking_sessions ps
        JOIN vehicles v ON v.id = ps.vehicle_id
        JOIN owners o ON o.id = v.owner_id
        JOIN parking_spots s ON s.id = ps.parking_spot_id
        JOIN tariffs t ON t.id = ps.tariff_id
        LEFT JOIN discounts d ON d.id = ps.discount_id
        WHERE ps.departure_time IS NOT NULL
        ORDER BY ps.id DESC
        LIMIT %s
    """, (limit,))


# ---------- доп. логика: автоподстановка + атомарный выезд ----------

def get_vehicle_by_plate(plate):
    """Карточка авто для автоподстановки: марка/владелец/телефон + последняя скидка."""
    rows = _fetch_all("""
        SELECT v.plate_number AS plate, v.make_model AS brand,
               o.full_name AS owner, o.phone AS phone,
               (SELECT ps.discount_id FROM parking_sessions ps
                WHERE ps.vehicle_id = v.id ORDER BY ps.id DESC LIMIT 1) AS last_discount_id
        FROM vehicles v
        JOIN owners o ON o.id = v.owner_id
        WHERE upper(v.plate_number) = upper(%s)
        LIMIT 1
    """, (plate,))
    return rows[0] if rows else None


def checkout_session(session_id, amount, departure_time=None):
    """Выезд одной транзакцией: платёж (если > 0) + закрытие сессии.
    Возвращает dict(departure_time, total_cost, paid_total)."""
    from datetime import datetime
    departure_time = departure_time or datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        if amount and int(amount) > 0:
            cur.execute("INSERT INTO payments (session_id, amount) VALUES (%s, %s)",
                        (session_id, int(amount)))
        cur.execute("""
            UPDATE parking_sessions
            SET departure_time = %s
            WHERE id = %s AND departure_time IS NULL
            RETURNING departure_time, total_cost
        """, (departure_time, session_id))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Сессия {session_id} уже закрыта или не существует")
        cur.execute("SELECT COALESCE(SUM(amount),0) AS paid FROM payments WHERE session_id = %s",
                    (session_id,))
        paid = cur.fetchone()["paid"]
        conn.commit()
        return {"departure_time": row["departure_time"], "total_cost": row["total_cost"],
                "paid_total": int(paid)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
