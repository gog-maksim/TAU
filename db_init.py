"""
Автоматическая инициализация БД при первом запуске.
"""
import psycopg2
from db_config import DB_CONFIG


def init_database():
    """Создаёт таблицы если их нет. Безопасно вызывать многократно."""
    conn = None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = True
        cur = conn.cursor()

        # 1. Владельцы
        cur.execute("""
            CREATE TABLE IF NOT EXISTS owners (
                id          SERIAL PRIMARY KEY,
                full_name   TEXT NOT NULL,
                phone       TEXT DEFAULT ''
            )
        """)

        # 2. Тарифы
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tariffs (
                id          SERIAL PRIMARY KEY,
                name        TEXT NOT NULL,
                hourly_rate INTEGER NOT NULL DEFAULT 100,
                is_active   BOOLEAN DEFAULT TRUE,
                created_at  TEXT DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI')
            )
        """)

        # 3. Скидки
        cur.execute("""
            CREATE TABLE IF NOT EXISTS discounts (
                id          SERIAL PRIMARY KEY,
                name        TEXT NOT NULL,
                percent     INTEGER NOT NULL DEFAULT 0,
                is_active   BOOLEAN DEFAULT TRUE
            )
        """)

        # 4. Парковочные места
        cur.execute("""
            CREATE TABLE IF NOT EXISTS parking_spots (
                id          SERIAL PRIMARY KEY,
                number      INTEGER NOT NULL UNIQUE
            )
        """)

        # 5. Автомобили
        cur.execute("""
            CREATE TABLE IF NOT EXISTS vehicles (
                id          SERIAL PRIMARY KEY,
                plate_number TEXT NOT NULL UNIQUE,
                make_model  TEXT NOT NULL,
                owner_id    INTEGER NOT NULL REFERENCES owners(id) ON DELETE CASCADE
            )
        """)

        # 6. Парковочные сессии
        cur.execute("""
            CREATE TABLE IF NOT EXISTS parking_sessions (
                id              SERIAL PRIMARY KEY,
                vehicle_id      INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
                parking_spot_id INTEGER NOT NULL REFERENCES parking_spots(id) ON DELETE CASCADE,
                tariff_id       INTEGER NOT NULL REFERENCES tariffs(id),
                discount_id     INTEGER REFERENCES discounts(id),
                arrival_time    TEXT NOT NULL,
                departure_time  TEXT,
                total_cost      INTEGER DEFAULT 0
            )
        """)

        # 7. Платежи
        cur.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id              SERIAL PRIMARY KEY,
                session_id      INTEGER NOT NULL REFERENCES parking_sessions(id) ON DELETE CASCADE,
                amount          INTEGER NOT NULL DEFAULT 0,
                payment_time    TEXT DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI')
            )
        """)

        # 8. Чёрный список
        cur.execute("""
            CREATE TABLE IF NOT EXISTS blacklist (
                id              SERIAL PRIMARY KEY,
                vehicle_id      INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
                reason          TEXT DEFAULT '',
                date_added      TEXT DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI'),
                is_active       BOOLEAN DEFAULT TRUE
            )
        """)

        # Заполняем справочники
        cur.execute("""
            INSERT INTO tariffs (name, hourly_rate) VALUES ('Стандартный', 100)
            ON CONFLICT DO NOTHING
        """)

        cur.execute("""
            INSERT INTO discounts (name, percent) VALUES
                ('Нет', 0),
                ('Пенсионер', 10),
                ('Постоянный клиент', 20),
                ('Ветеран', 50)
            ON CONFLICT DO NOTHING
        """)

        # Создаём 100 парковочных мест
        cur.execute("""
            INSERT INTO parking_spots (number)
            SELECT generate_series(1, 100)
            ON CONFLICT (number) DO NOTHING
        """)

        # Индексы
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_vehicle ON parking_sessions(vehicle_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_spot ON parking_sessions(parking_spot_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_arrival ON parking_sessions(arrival_time)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_payments_session ON payments(session_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_blacklist_vehicle ON blacklist(vehicle_id)")

        # Функция расчёта стоимости
        cur.execute("""
            CREATE OR REPLACE FUNCTION calc_session_cost(
                p_arrival TEXT,
                p_departure TEXT,
                p_hourly_rate INTEGER,
                p_discount INTEGER DEFAULT 0
            ) RETURNS INTEGER AS $$
            DECLARE
                v_arrival TIMESTAMP;
                v_departure TIMESTAMP;
                v_hours INTEGER;
                v_cost INTEGER;
            BEGIN
                v_arrival := to_timestamp(p_arrival, 'YYYY-MM-DD HH24:MI');
                v_departure := COALESCE(to_timestamp(p_departure, 'YYYY-MM-DD HH24:MI'), now());
                v_hours := GREATEST(1, CEIL(EXTRACT(EPOCH FROM (v_departure - v_arrival)) / 3600));
                v_cost := v_hours * p_hourly_rate * (1 - p_discount / 100.0);
                RETURN v_cost::INTEGER;
            END;
            $$ LANGUAGE plpgsql
        """)

        # Триггер автоматического расчёта
        cur.execute("""
            CREATE OR REPLACE FUNCTION update_total_cost() RETURNS TRIGGER AS $$
            DECLARE
                v_hourly_rate INTEGER;
                v_discount INTEGER := 0;
            BEGIN
                SELECT hourly_rate INTO v_hourly_rate FROM tariffs WHERE id = NEW.tariff_id;
                SELECT COALESCE(percent, 0) INTO v_discount FROM discounts WHERE id = NEW.discount_id;

                NEW.total_cost := calc_session_cost(
                    NEW.arrival_time,
                    NEW.departure_time,
                    COALESCE(v_hourly_rate, 100),
                    v_discount
                );
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """)

        cur.execute("DROP TRIGGER IF EXISTS trg_total_cost ON parking_sessions")
        cur.execute("""
            CREATE TRIGGER trg_total_cost
                BEFORE INSERT OR UPDATE ON parking_sessions
                FOR EACH ROW EXECUTE FUNCTION update_total_cost()
        """)

        # Представления
        cur.execute("""
            CREATE OR REPLACE VIEW occupied_spots AS
            SELECT DISTINCT parking_spot_id
            FROM parking_sessions
            WHERE departure_time IS NULL
        """)

        cur.execute("""
            CREATE OR REPLACE VIEW session_history AS
            SELECT
                ps.id           AS session_id,
                v.plate_number,
                v.make_model,
                o.full_name     AS owner_name,
                o.phone         AS owner_phone,
                ps.parking_spot_id,
                ps.arrival_time,
                ps.departure_time,
                t.hourly_rate,
                d.percent       AS discount_percent,
                ps.total_cost,
                CASE
                    WHEN ps.departure_time IS NULL THEN 'На стоянке'
                    ELSE 'Завершена'
                END AS status
            FROM parking_sessions ps
            JOIN vehicles v  ON v.id = ps.vehicle_id
            JOIN owners o    ON o.id = v.owner_id
            JOIN tariffs t   ON t.id = ps.tariff_id
            LEFT JOIN discounts d ON d.id = ps.discount_id
        """)

        cur.close()
        print("База данных успешно инициализирована!")
        return True

    except Exception as e:
        print(f"Ошибка инициализации БД: {e}")
        return False
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    init_database()
