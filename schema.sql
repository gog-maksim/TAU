-- ============================================
-- Схема БД «Автостоянка» для PostgreSQL
-- ============================================

-- 1. Владельцы
CREATE TABLE IF NOT EXISTS owners (
    id          SERIAL PRIMARY KEY,
    full_name   TEXT NOT NULL,
    phone       TEXT DEFAULT ''
);

-- 2. Тарифы
CREATE TABLE IF NOT EXISTS tariffs (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    hourly_rate INTEGER NOT NULL DEFAULT 100,
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TEXT DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI')
);

-- 3. Скидки
CREATE TABLE IF NOT EXISTS discounts (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    percent     INTEGER NOT NULL DEFAULT 0,
    is_active   BOOLEAN DEFAULT TRUE
);

-- 4. Парковочные места
CREATE TABLE IF NOT EXISTS parking_spots (
    id          SERIAL PRIMARY KEY,
    number      INTEGER NOT NULL UNIQUE
);

-- 5. Автомобили
CREATE TABLE IF NOT EXISTS vehicles (
    id          SERIAL PRIMARY KEY,
    plate_number TEXT NOT NULL UNIQUE,
    make_model  TEXT NOT NULL,
    owner_id    INTEGER NOT NULL REFERENCES owners(id) ON DELETE CASCADE
);

-- 6. Парковочные сессии
CREATE TABLE IF NOT EXISTS parking_sessions (
    id              SERIAL PRIMARY KEY,
    vehicle_id      INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    parking_spot_id INTEGER NOT NULL REFERENCES parking_spots(id) ON DELETE CASCADE,
    tariff_id       INTEGER NOT NULL REFERENCES tariffs(id),
    discount_id     INTEGER REFERENCES discounts(id),
    arrival_time    TEXT NOT NULL,
    departure_time  TEXT,
    total_cost      INTEGER DEFAULT 0
);

-- 7. Платежи
CREATE TABLE IF NOT EXISTS payments (
    id              SERIAL PRIMARY KEY,
    session_id      INTEGER NOT NULL REFERENCES parking_sessions(id) ON DELETE CASCADE,
    amount          INTEGER NOT NULL DEFAULT 0,
    payment_time    TEXT DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI')
);

-- 8. Чёрный список
CREATE TABLE IF NOT EXISTS blacklist (
    id              SERIAL PRIMARY KEY,
    vehicle_id      INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
    reason          TEXT DEFAULT '',
    date_added      TEXT DEFAULT to_char(now(), 'YYYY-MM-DD HH24:MI'),
    is_active       BOOLEAN DEFAULT TRUE
);

-- ============================================
-- Заполняем справочники по умолчанию
-- ============================================

CREATE UNIQUE INDEX IF NOT EXISTS uq_discounts_name_active ON discounts(name) WHERE is_active = TRUE;
-- тарифы версионируются: уникальность имени только среди активных
CREATE UNIQUE INDEX IF NOT EXISTS uq_tariffs_name_active ON tariffs(name) WHERE is_active = TRUE;

INSERT INTO tariffs (name, hourly_rate) VALUES ('Стандартный', 100)
ON CONFLICT (name) WHERE is_active DO NOTHING;

INSERT INTO discounts (name, percent) VALUES
    ('Нет', 0),
    ('Пенсионер', 10),
    ('Постоянный клиент', 20),
    ('Ветеран', 50)
ON CONFLICT (name) WHERE is_active DO NOTHING;

-- Создаём 100 парковочных мест (1..100)
INSERT INTO parking_spots (number)
SELECT generate_series(1, 100)
ON CONFLICT (number) DO NOTHING;

-- ============================================
-- Функция расчёта стоимости сессии
-- ============================================
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
$$ LANGUAGE plpgsql;

-- ============================================
-- Триггер автоматического расчёта total_cost
-- ============================================
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
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_total_cost ON parking_sessions;
CREATE TRIGGER trg_total_cost
    BEFORE INSERT OR UPDATE ON parking_sessions
    FOR EACH ROW EXECUTE FUNCTION update_total_cost();

-- ============================================
-- Индексы
-- ============================================
CREATE INDEX IF NOT EXISTS idx_sessions_vehicle ON parking_sessions(vehicle_id);
CREATE INDEX IF NOT EXISTS idx_sessions_spot ON parking_sessions(parking_spot_id);
CREATE INDEX IF NOT EXISTS idx_sessions_arrival ON parking_sessions(arrival_time);
CREATE INDEX IF NOT EXISTS idx_payments_session ON payments(session_id);
CREATE INDEX IF NOT EXISTS idx_blacklist_vehicle ON blacklist(vehicle_id);

-- ============================================
-- Представление: занятые места
-- ============================================
CREATE OR REPLACE VIEW occupied_spots AS
SELECT DISTINCT parking_spot_id
FROM parking_sessions
WHERE departure_time IS NULL;

-- ============================================
-- Представление: история сессий с деталями
-- ============================================
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
LEFT JOIN discounts d ON d.id = ps.discount_id;
