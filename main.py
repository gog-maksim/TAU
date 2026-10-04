import sys
import math
import re
from datetime import datetime
from PyQt6 import uic
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTableWidgetItem, QMessageBox,
    QHeaderView, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSpinBox, QDialogButtonBox, QFrame, QGridLayout
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QPalette, QColor

from db_config import get_connection
from db_init import init_database


HOURLY_RATE = 100
TOTAL_PLACES = 100


LIGHT_QSS = """
QDialog { background: #f4f6f9; }
QLabel { color: #1a2b4c; font-size: 13px; }
QGroupBox { font-weight: bold; color: #1a2b4c; }
QLineEdit, QSpinBox { background: white; color: #1a2b4c; border: 1px solid #cbd5e1; border-radius: 6px; padding: 6px; }
QSpinBox::up-button, QSpinBox::down-button { background: #eef2f7; border: 1px solid #cbd5e1; }
"""


class CheckoutDialog(QDialog):
    """Диалог оформления выезда по п.7.3 ТЗ"""
    def __init__(self, parent, car: dict, hourly_rate: int):
        super().__init__(parent)
        self.car = car
        self.hourly_rate = hourly_rate
        self.additional_payment = 0
        self.result_action = None  # "paid" | "debt" | None
        self.setWindowTitle("Оформление выезда — расчет сессии")
        self.setMinimumWidth(480)
        self.setStyleSheet(LIGHT_QSS)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Заголовок
        title = QLabel(f"<b>{car['plate']}</b> — {car['brand']}  •  Место №{car['place']}")
        title.setStyleSheet("font-size:15px; padding:6px; background:#eef2f7; border-radius:6px; color:#1a2b4c;")
        layout.addWidget(title)

        info = QLabel(
            f"Владелец: <b>{car['owner']}</b><br>"
            f"Телефон: {car['phone'] or '—'}<br>"
            f"Время въезда: <b>{car['time']}</b><br>"
            f"Скидка: <b>{car['discount']}%</b>  •  Тариф: <b>{hourly_rate} ₽/час</b>"
        )
        info.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(info)

        # Расчет
        duration_h, cost = self._calc()
        self.label_duration = QLabel(f"Текущая длительность: <b>{self._fmt_duration(duration_h)}</b>")
        self.label_duration.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.label_duration)

        self.label_cost = QLabel(f"Расчетная стоимость: <b>{cost} ₽</b>  <span style='color:#6b7a90'>( {duration_h} ч × {hourly_rate} ₽ × скидка )</span>")
        self.label_cost.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.label_cost)

        # Платежи
        box = QFrame()
        box.setStyleSheet("QFrame{background:white; border:1px solid #d0d7e3; border-radius:6px;} QLabel{color:#1a2b4c}")
        box_l = QVBoxLayout(box)
        box_l.setContentsMargins(12,12,12,12)

        box_l.addWidget(QLabel("<b>Платежи</b>"))
        self.label_total = QLabel(f"К оплате всего: <b>{cost} ₽</b>")
        self.label_total.setTextFormat(Qt.TextFormat.RichText)
        box_l.addWidget(self.label_total)

        row_pay = QHBoxLayout()
        row_pay.addWidget(QLabel("Внести платеж сейчас (₽):"))
        self.spin_pay = QSpinBox()
        self.spin_pay.setRange(0, 100000)
        self.spin_pay.setSingleStep(100)
        self.spin_pay.setValue(0)
        self.spin_pay.valueChanged.connect(self._on_pay_changed)
        row_pay.addWidget(self.spin_pay)
        box_l.addLayout(row_pay)

        self.label_remaining = QLabel(f"К доплате: <b>{cost} ₽</b>")
        self.label_remaining.setTextFormat(Qt.TextFormat.RichText)
        box_l.addWidget(self.label_remaining)

        self.label_status = QLabel("Статус после оформления: <b style='color:#e67e22'>Задолженность</b>")
        self.label_status.setTextFormat(Qt.TextFormat.RichText)
        box_l.addWidget(self.label_status)

        hint = QLabel("При частичной оплате остаток оформляется как задолженность. При полной — «Оплачено».")
        hint.setStyleSheet("color:#6b7a90; font-size:11px; font-style:italic;")
        hint.setWordWrap(True)
        box_l.addWidget(hint)

        layout.addWidget(box)

        # Кнопки
        btns = QDialogButtonBox()
        self.btn_pay_debt = QPushButton("Оформить как задолженность")
        self.btn_pay_debt.setStyleSheet("background:#e67e22; color:white; font-weight:bold; padding:8px; border-radius:6px;")
        self.btn_paid = QPushButton("Подтвердить выезд")
        self.btn_paid.setStyleSheet("background:#27ae60; color:white; font-weight:bold; padding:8px; border-radius:6px;")
        self.btn_cancel = QPushButton("Отмена")
        btns.addButton(self.btn_pay_debt, QDialogButtonBox.ButtonRole.ActionRole)
        btns.addButton(self.btn_paid, QDialogButtonBox.ButtonRole.AcceptRole)
        btns.addButton(self.btn_cancel, QDialogButtonBox.ButtonRole.RejectRole)
        self.btn_pay_debt.clicked.connect(self._on_debt)
        self.btn_paid.clicked.connect(self._on_paid)
        self.btn_cancel.clicked.connect(self.reject)
        layout.addWidget(btns)

        self.cost = cost
        self.duration_h = duration_h
        self._on_pay_changed(0)

    def _calc(self):
        try:
            t_entry = datetime.strptime(self.car["time"], "%Y-%m-%d %H:%M")
        except:
            return 1, self.hourly_rate
        delta = (datetime.now() - t_entry).total_seconds() / 3600
        hours = max(1, math.ceil(delta))
        disc = int(str(self.car["discount"]).replace("%","")) if isinstance(self.car["discount"], str) else int(self.car["discount"])
        cost = int(hours * self.hourly_rate * (1 - disc/100))
        return hours, cost

    def _fmt_duration(self, h):
        if h < 24:
            return f"{h} ч"
        d = h // 24
        rh = h % 24
        return f"{d} д {rh} ч ({h} ч)"

    def _on_pay_changed(self, val):
        remaining = max(0, self.cost - val)
        self.label_remaining.setText(f"К доплате: <b>{remaining} ₽</b>")
        if val >= self.cost:
            self.label_status.setText("Статус после оформления: <b style='color:#27ae60'>Оплачено</b>")
        elif val > 0:
            self.label_status.setText(f"Статус после оформления: <b style='color:#e67e22'>Частично оплачено, долг {remaining} ₽</b>")
        else:
            self.label_status.setText("Статус после оформления: <b style='color:#e74c3c'>Задолженность</b>")

    def _on_debt(self):
        self.additional_payment = self.spin_pay.value()
        self.result_action = "debt"
        self.accept()

    def _on_paid(self):
        self.additional_payment = self.spin_pay.value()
        if self.additional_payment < self.cost:
            self.result_action = "debt"
        else:
            self.result_action = "paid"
        self.accept()


def apply_light_palette(app: QApplication):
    """Принудительно светлая палитра — перебивает системную темную тему"""
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor("#f4f6f9"))
    pal.setColor(QPalette.ColorRole.WindowText, QColor("#1a2b4c"))
    pal.setColor(QPalette.ColorRole.Base, QColor("white"))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor("#f8fafc"))
    pal.setColor(QPalette.ColorRole.Text, QColor("#1a2b4c"))
    pal.setColor(QPalette.ColorRole.Button, QColor("white"))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor("#1a2b4c"))
    pal.setColor(QPalette.ColorRole.Highlight, QColor("#dbeafe"))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#1a2b4c"))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor("white"))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor("#1a2b4c"))
    app.setPalette(pal)


class ParkingApp(QMainWindow):
    """Основной класс — работа с PostgreSQL"""
    def __init__(self):
        super().__init__()
        uic.loadUi("parking.ui", self)

        self.HOURLY_RATE = HOURLY_RATE

        # Настройка таблицы
        self.tableCars.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tableCars.setSelectionBehavior(self.tableCars.SelectionBehavior.SelectRows)
        self.tableCars.setEditTriggers(self.tableCars.EditTrigger.NoEditTriggers)
        self.tableCars.verticalHeader().setVisible(False)
        self.tableCars.setAlternatingRowColors(True)

        # Заполнить combo_place 1..100
        self.combo_place.clear()
        for i in range(1, TOTAL_PLACES + 1):
            self.combo_place.addItem(str(i))

        # Сигналы
        self.btn_add.clicked.connect(self.add_car)
        self.btn_remove.clicked.connect(self.remove_car)
        self.combo_place.currentTextChanged.connect(self._highlight_grid_selection)
        self.tableCars.itemSelectionChanged.connect(self._on_table_select)

        # Сетка 10x10
        self.place_buttons = {}
        self._build_grid()

        # Таймер авто-пересчета каждую минуту
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_calculations)
        self.timer.start(60_000)

        self.load_data_from_db()
        self.refresh_calculations()
        self.update_monitoring()

    # ---------- Grid ----------
    def _build_grid(self):
        layout = self.gridParking
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.place_buttons.clear()
        for place in range(1, TOTAL_PLACES + 1):
            btn = QPushButton(str(place))
            btn.setFixedSize(28, 28)
            btn.setStyleSheet(self._style_for_place(place, occupied=False, selected=False))
            btn.clicked.connect(lambda _, p=place: self._on_grid_clicked(p))
            r = (place - 1) // 10
            c = (place - 1) % 10
            layout.addWidget(btn, r, c)
            self.place_buttons[place] = btn

    def _style_for_place(self, place, occupied, selected):
        if occupied:
            return "QPushButton{background:#e74c3c; color:white; border-radius:6px; font-weight:bold; font-size:11px; border:1px solid #c0392b}"
        if selected:
            return "QPushButton{background:#3498db; color:white; border-radius:6px; font-weight:bold; font-size:11px; border:2px solid #1a5a8a}"
        return "QPushButton{background:white; color:#2c3e50; border-radius:6px; font-size:11px; border:1px solid #d0d7e3} QPushButton:hover{background:#eaf2ff}"

    def _on_grid_clicked(self, place):
        occupied = self._occupied_places()
        if place in occupied:
            for row in range(self.tableCars.rowCount()):
                if self.tableCars.item(row, 4).text() == str(place):
                    self.tableCars.selectRow(row)
                    break
            return
        idx = self.combo_place.findText(str(place))
        if idx >= 0:
            self.combo_place.setCurrentIndex(idx)

    def _highlight_grid_selection(self, text):
        try:
            sel = int(text)
        except:
            sel = -1
        occupied = self._occupied_places()
        for p, btn in self.place_buttons.items():
            occ = p in occupied
            is_sel = (p == sel and not occ)
            btn.setStyleSheet(self._style_for_place(p, occupied=occ, selected=is_sel))

    def _occupied_places(self):
        s = set()
        for row in range(self.tableCars.rowCount()):
            try:
                s.add(int(self.tableCars.item(row, 4).text()))
            except:
                pass
        return s

    # ---------- Helpers ----------
    def _occupied_plates(self):
        return {self.tableCars.item(r, 0).text().strip().lower() for r in range(self.tableCars.rowCount()) if self.tableCars.item(r,0)}

    def _calc_duration_cost(self, time_str, discount_percent):
        try:
            t_entry = datetime.strptime(time_str, "%Y-%m-%d %H:%M")
        except:
            return 1, int(self.HOURLY_RATE * (1 - discount_percent/100)), "1 ч"
        delta_h = (datetime.now() - t_entry).total_seconds() / 3600
        hours = max(1, math.ceil(delta_h))
        cost = int(hours * self.HOURLY_RATE * (1 - discount_percent/100))
        if hours < 24:
            dur = f"{hours} ч"
        else:
            d = hours // 24
            rh = hours % 24
            dur = f"{d}д {rh}ч"
        return hours, cost, dur

    # ---------- Add ----------
    def add_car(self):
        plate = self.input_plate.text().strip().upper()
        brand = self.input_brand.text().strip()
        owner = self.input_owner.text().strip()
        phone = self.input_phone.text().strip()
        place_str = self.combo_place.currentText().strip()
        discount_text = self.combo_discount.currentText()

        if not plate or not brand or not owner or not place_str:
            QMessageBox.warning(self, "Ошибка", "Заполните обязательные поля: гос.номер, марка, ФИО, место.")
            return

        if len(plate) < 5:
            QMessageBox.warning(self, "Ошибка", "Гос.номер выглядит слишком коротким.")
            return

        try:
            place = int(place_str)
            if not 1 <= place <= TOTAL_PLACES:
                raise ValueError
        except:
            QMessageBox.warning(self, "Ошибка", "Место должно быть числом 1–100.")
            return

        if place in self._occupied_places():
            QMessageBox.warning(self, "Место занято", f"Место №{place} уже занято. Выберите свободное.")
            return

        if plate.lower() in self._occupied_plates():
            QMessageBox.warning(self, "Дубликат", f"Автомобиль с номером {plate} уже на стоянке.")
            return

        if phone and len(re.sub(r"\D", "", phone)) < 7:
            QMessageBox.warning(self, "Ошибка", "Номер телефона указан некорректно.")
            return

        m = re.search(r"(\d+)%", discount_text)
        discount = int(m.group(1)) if m else 0

        time_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        try:
            self._save_car_to_db(plate, brand, owner, phone, place, time_str, discount)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка БД", f"Не удалось сохранить данные:\n{e}")
            return

        self.insert_row_to_table(plate, brand, owner, phone, place, time_str, discount)

        self.refresh_calculations()
        self.update_monitoring()

        self.input_plate.clear()
        self.input_brand.clear()
        self.input_owner.clear()
        self.input_phone.clear()
        self._select_next_free_place()

        self.statusbar.showMessage(f"Автомобиль {plate} зарегистрирован на место №{place}", 4000)

    def _save_car_to_db(self, plate, brand, owner, phone, place, time_str, discount):
        """Сохраняет автомобиль и сессию в PostgreSQL"""
        conn = get_connection()
        cur = conn.cursor()
        try:
            # 1. Владелец
            cur.execute(
                "INSERT INTO owners (full_name, phone) VALUES (%s, %s) RETURNING id",
                (owner, phone)
            )
            owner_id = cur.fetchone()[0]

            # 2. Автомобиль
            cur.execute(
                "INSERT INTO vehicles (plate_number, make_model, owner_id) VALUES (%s, %s, %s) RETURNING id",
                (plate, brand, owner_id)
            )
            vehicle_id = cur.fetchone()[0]

            # 3. Находим tariff_id и discount_id
            cur.execute("SELECT id FROM tariffs WHERE is_active = TRUE LIMIT 1")
            tariff_id = cur.fetchone()[0]

            cur.execute("SELECT id FROM discounts WHERE percent = %s LIMIT 1", (discount,))
            row = cur.fetchone()
            discount_id = row[0] if row else None

            # 4. Находим parking_spot_id
            cur.execute("SELECT id FROM parking_spots WHERE number = %s", (place,))
            spot_id = cur.fetchone()[0]

            # 5. Создаём сессию
            cur.execute(
                """INSERT INTO parking_sessions
                   (vehicle_id, parking_spot_id, tariff_id, discount_id, arrival_time)
                   VALUES (%s, %s, %s, %s, %s)""",
                (vehicle_id, spot_id, tariff_id, discount_id, time_str)
            )
        finally:
            cur.close()
            conn.close()

    def _select_next_free_place(self):
        occ = self._occupied_places()
        for i in range(1, TOTAL_PLACES+1):
            if i not in occ:
                idx = self.combo_place.findText(str(i))
                if idx >= 0:
                    self.combo_place.setCurrentIndex(idx)
                break

    def insert_row_to_table(self, plate, brand, owner, phone, place, time_str, discount):
        row = self.tableCars.rowCount()
        self.tableCars.insertRow(row)
        dark = QColor("#1a2b4c")
        def _it(text):
            it = QTableWidgetItem(text)
            it.setForeground(dark)
            return it
        self.tableCars.setItem(row, 0, _it(plate))
        self.tableCars.setItem(row, 1, _it(brand))
        self.tableCars.setItem(row, 2, _it(owner))
        self.tableCars.setItem(row, 3, _it(phone))
        self.tableCars.setItem(row, 4, _it(str(place)))
        self.tableCars.setItem(row, 5, _it(time_str))

        self.tableCars.setItem(row, 6, _it("—"))
        self.tableCars.setItem(row, 7, _it("—"))
        self.tableCars.setItem(row, 8, _it(f"{discount}%"))

        status_item = QTableWidgetItem("На стоянке")
        status_item.setForeground(QColor("#1d4ed8"))
        self.tableCars.setItem(row, 9, status_item)

        for col in (4,6,7,8,9):
            it = self.tableCars.item(row, col)
            if it:
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        for col in (0,1,2,3,5):
            it = self.tableCars.item(row, col)
            if it:
                it.setForeground(dark)

    # ---------- Refresh & monitoring ----------
    def refresh_calculations(self):
        for row in range(self.tableCars.rowCount()):
            time_str = self.tableCars.item(row, 5).text()
            disc_str = self.tableCars.item(row, 8).text().replace("%","")
            try:
                disc = int(disc_str)
            except:
                disc = 0
            _, cost, dur = self._calc_duration_cost(time_str, disc)
            dur_item = self.tableCars.item(row, 6)
            if dur_item:
                dur_item.setText(dur)
                dur_item.setForeground(QColor("#1a2b4c"))
            cost_item = self.tableCars.item(row, 7)
            if cost_item:
                cost_item.setText(f"{cost} ₽")
                if "д" in dur:
                    cost_item.setForeground(QColor("#7c3aed"))
                else:
                    cost_item.setForeground(QColor("#1a2b4c"))

    def update_monitoring(self):
        occupied = len(self._occupied_places())
        free = TOTAL_PLACES - occupied
        self.label_occupancy.setText(f"Занято: {occupied} / {TOTAL_PLACES}  •  Свободно: {free}")
        self.progressOccupancy.setValue(occupied)
        if occupied >= 90:
            self.progressOccupancy.setStyleSheet("QProgressBar{border:1px solid #d0d7e3; border-radius:5px; background:#eef2f7} QProgressBar::chunk{background:#e74c3c}")
        elif occupied >= 70:
            self.progressOccupancy.setStyleSheet("QProgressBar{border:1px solid #d0d7e3; border-radius:5px; background:#eef2f7} QProgressBar::chunk{background:#f39c12}")
        else:
            self.progressOccupancy.setStyleSheet("QProgressBar{border:1px solid #d0d7e3; border-radius:5px; background:#eef2f7} QProgressBar::chunk{background:#27ae60}")

        occ_set = self._occupied_places()
        try:
            sel = int(self.combo_place.currentText())
        except:
            sel = -1
        for p, btn in self.place_buttons.items():
            occ = p in occ_set
            is_sel = (p == sel and not occ)
            btn.setStyleSheet(self._style_for_place(p, occupied=occ, selected=is_sel))
            btn.setToolTip(f"Место №{p} — {'занято' if occ else 'свободно'}")

    def _on_table_select(self):
        row = self.tableCars.currentRow()
        if row < 0:
            return
        try:
            place = self.tableCars.item(row, 4).text()
            self.combo_place.blockSignals(True)
            idx = self.combo_place.findText(place)
            occ = self._occupied_places()
            sel = int(place) if place.isdigit() else -1
            for p, btn in self.place_buttons.items():
                occ_p = p in occ
                if p == sel:
                    btn.setStyleSheet("QPushButton{background:#8e44ad; color:white; border-radius:6px; font-weight:bold; border:2px solid #6c3483}")
                else:
                    is_combo_sel = False
                    try:
                        is_combo_sel = (p == int(self.combo_place.currentText()) and p not in occ)
                    except:
                        pass
                    btn.setStyleSheet(self._style_for_place(p, occupied=occ_p, selected=is_combo_sel))
        finally:
            self.combo_place.blockSignals(False)

    # ---------- Remove / checkout ----------
    def remove_car(self):
        row = self.tableCars.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Выезд", "Сначала выберите автомобиль в таблице справа.")
            return

        plate = self.tableCars.item(row, 0).text()
        brand = self.tableCars.item(row, 1).text()
        owner = self.tableCars.item(row, 2).text()
        phone = self.tableCars.item(row, 3).text()
        place = self.tableCars.item(row, 4).text()
        time_str = self.tableCars.item(row, 5).text()
        discount = self.tableCars.item(row, 8).text()

        car = {"plate": plate, "brand": brand, "owner": owner, "phone": phone, "place": place, "time": time_str, "discount": discount}

        dlg = CheckoutDialog(self, car, self.HOURLY_RATE)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        try:
            self._checkout_car_from_db(plate, dlg.additional_payment, dlg.cost)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка БД", f"Не удалось оформить выезд:\n{e}")
            return

        self.tableCars.removeRow(row)
        self.update_monitoring()
        self.refresh_calculations()

        if dlg.result_action == "paid":
            QMessageBox.information(self, "Выезд оформлен", f"Автомобиль {plate} выехал.\nОплачено: {dlg.cost} ₽ (внесено {dlg.additional_payment} ₽).\nМесто №{place} освобождено.")
        else:
            remaining = dlg.cost - dlg.additional_payment
            QMessageBox.information(self, "Выезд оформлен", f"Автомобиль {plate} выехал.\nВнесено: {dlg.additional_payment} ₽\nЗадолженность: {remaining} ₽\nМесто №{place} освобождено.")
        self.statusbar.showMessage(f"Место №{place} свободно", 4000)

    def _checkout_car_from_db(self, plate, payment_amount, total_cost):
        """Завершает сессию и записывает платёж в PostgreSQL"""
        conn = get_connection()
        cur = conn.cursor()
        try:
            # Находим активную сессию для этого авто
            cur.execute(
                """SELECT ps.id FROM parking_sessions ps
                   JOIN vehicles v ON v.id = ps.vehicle_id
                   WHERE v.plate_number = %s AND ps.departure_time IS NULL
                   LIMIT 1""",
                (plate,)
            )
            row = cur.fetchone()
            if not row:
                raise Exception(f"Активная сессия для {plate} не найдена")

            session_id = row[0]

            # Завершаем сессию
            cur.execute(
                "UPDATE parking_sessions SET departure_time = %s WHERE id = %s",
                (datetime.now().strftime("%Y-%m-%d %H:%M"), session_id)
            )

            # Записываем платёж
            cur.execute(
                "INSERT INTO payments (session_id, amount) VALUES (%s, %s)",
                (session_id, payment_amount)
            )
        finally:
            cur.close()
            conn.close()

    # ---------- Load from DB ----------
    def load_data_from_db(self):
        """Загружает активные сессии из PostgreSQL"""
        conn = get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                """SELECT v.plate_number, v.make_model, o.full_name, o.phone,
                          sess.parking_spot_id, sess.arrival_time, d.percent
                   FROM parking_sessions sess
                   JOIN vehicles v ON v.id = sess.vehicle_id
                   JOIN owners o ON o.id = v.owner_id
                   JOIN parking_spots spot ON spot.id = sess.parking_spot_id
                   LEFT JOIN discounts d ON d.id = sess.discount_id
                   WHERE sess.departure_time IS NULL
                   ORDER BY sess.arrival_time"""
            )
            rows = cur.fetchall()
            for row in rows:
                plate, brand, owner, phone, spot_id, arrival, discount = row
                # Находим номер места по spot_id
                cur2 = conn.cursor()
                cur2.execute("SELECT number FROM parking_spots WHERE id = %s", (spot_id,))
                place_row = cur2.fetchone()
                cur2.close()
                place = place_row[0] if place_row else 1

                disc_int = int(discount) if discount else 0
                self.insert_row_to_table(plate, brand, owner, phone, place, arrival, disc_int)
        except Exception as e:
            print(f"Ошибка загрузки из БД: {e}")
        finally:
            cur.close()
            conn.close()


ParkingAppV2 = ParkingApp

if __name__ == "__main__":
    # Автоматическая инициализация БД при первом запуске
    init_database()

    app = QApplication(sys.argv)
    apply_light_palette(app)
    w = ParkingApp()
    w.show()
    sys.exit(app.exec())
