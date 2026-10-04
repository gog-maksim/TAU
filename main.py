import sys
import json
import os
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
        # если внесено меньше — считаем что остаток доплачен сейчас
        self.additional_payment = self.spin_pay.value()
        # для демо: если не хватает — предлагаем подтвердить как долг
        if self.additional_payment < self.cost:
            # если нажал "Подтвердить выезд" без полной оплаты — трактуем как долг
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
    """Основной класс — перенесен из v2 по ТЗ п.7"""
    def __init__(self):
        super().__init__()
        uic.loadUi("parking.ui", self)

        self.DATA_FILE = "parking_base_v2.json"
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
        self.place_buttons = {}  # place:int -> QPushButton
        self._build_grid()

        # Таймер авто-пересчета каждую минуту
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_calculations)
        self.timer.start(60_000)

        self.load_data_from_file()
        self.refresh_calculations()
        self.update_monitoring()

    # ---------- Grid ----------
    def _build_grid(self):
        # очистить если уже есть
        layout = self.gridParking
        # удалить старые если есть (на случай перезапуска)
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
        # клик по сетке — выбрать место в combo если свободно
        occupied = self._occupied_places()
        if place in occupied:
            # подсветить строку с этим местом
            for row in range(self.tableCars.rowCount()):
                if self.tableCars.item(row, 4).text() == str(place):
                    self.tableCars.selectRow(row)
                    break
            return
        # свободно — выбрать
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
        # красивая длительность
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

        # простая валидация госномера (не строгая)
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

        # телефон — опционально, но если введен — проверим
        if phone and len(re.sub(r"\D", "", phone)) < 7:
            QMessageBox.warning(self, "Ошибка", "Номер телефона указан некорректно.")
            return

        # скидка
        m = re.search(r"(\d+)%", discount_text)
        discount = int(m.group(1)) if m else 0

        time_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        # вставляем — длительность/стоимость посчитаются в refresh
        self.insert_row_to_table(plate, brand, owner, phone, place, time_str, discount)

        self.save_data_to_file()
        self.refresh_calculations()
        self.update_monitoring()

        # очистка
        self.input_plate.clear()
        self.input_brand.clear()
        self.input_owner.clear()
        self.input_phone.clear()
        # перевести combo на следующее свободное
        self._select_next_free_place()

        self.statusbar.showMessage(f"Автомобиль {plate} зарегистрирован на место №{place}", 4000)

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
        # 0 plate,1 brand,2 owner,3 phone,4 place,5 time,6 duration,7 cost,8 discount,9 status
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

        # длительность/стоимость — заполнит refresh
        self.tableCars.setItem(row, 6, _it("—"))
        self.tableCars.setItem(row, 7, _it("—"))
        self.tableCars.setItem(row, 8, _it(f"{discount}%"))

        status_item = QTableWidgetItem("На стоянке")
        status_item.setForeground(QColor("#1d4ed8"))
        self.tableCars.setItem(row, 9, status_item)

        # выравнивание по центру + цвет
        for col in (4,6,7,8,9):
            it = self.tableCars.item(row, col)
            if it:
                it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        # левые колонки тоже явно темные
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
            # длительность
            dur_item = self.tableCars.item(row, 6)
            if dur_item:
                dur_item.setText(dur)
                dur_item.setForeground(QColor("#1a2b4c"))
            # стоимость
            cost_item = self.tableCars.item(row, 7)
            if cost_item:
                cost_item.setText(f"{cost} ₽")
                # подсветка если долго стоит (>24ч)
                if "д" in dur:
                    cost_item.setForeground(QColor("#7c3aed"))
                else:
                    cost_item.setForeground(QColor("#1a2b4c"))

    def update_monitoring(self):
        occupied = len(self._occupied_places())
        free = TOTAL_PLACES - occupied
        self.label_occupancy.setText(f"Занято: {occupied} / {TOTAL_PLACES}  •  Свободно: {free}")
        self.progressOccupancy.setValue(occupied)
        # цвет прогресса
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

        # обновить доступность combo — задизейблить занятые (визуально через стиль, но QComboBox не поддерживает disable отдельных items просто)
        # поэтому оставим все, но при выборе занятого покажем предупреждение (уже в add_car)

    def _on_table_select(self):
        # при выборе строки — подсветить место на сетке и в combo
        row = self.tableCars.currentRow()
        if row < 0:
            return
        try:
            place = self.tableCars.item(row, 4).text()
            # временно отключить сигнал чтобы не мигать
            self.combo_place.blockSignals(True)
            idx = self.combo_place.findText(place)
            # не меняем combo если место занято выбранным авто — просто подсветим
            # но для наглядности подсветим grid
            occ = self._occupied_places()
            sel = int(place) if place.isdigit() else -1
            for p, btn in self.place_buttons.items():
                occ_p = p in occ
                # выделить выбранное место авто рамкой
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

        # подтверждение — удаляем
        self.tableCars.removeRow(row)
        self.save_data_to_file()
        self.update_monitoring()
        self.refresh_calculations()

        if dlg.result_action == "paid":
            QMessageBox.information(self, "Выезд оформлен", f"Автомобиль {plate} выехал.\nОплачено: {dlg.cost} ₽ (внесено {dlg.additional_payment} ₽).\nМесто №{place} освобождено.")
        else:
            remaining = dlg.cost - dlg.additional_payment
            QMessageBox.information(self, "Выезд оформлен", f"Автомобиль {plate} выехал.\nВнесено: {dlg.additional_payment} ₽\nЗадолженность: {remaining} ₽\nМесто №{place} освобождено.")
        self.statusbar.showMessage(f"Место №{place} свободно", 4000)

    # ---------- Save / Load ----------
    def save_data_to_file(self):
        data = []
        for row in range(self.tableCars.rowCount()):
            data.append({
                "plate": self.tableCars.item(row, 0).text(),
                "brand": self.tableCars.item(row, 1).text(),
                "owner": self.tableCars.item(row, 2).text(),
                "phone": self.tableCars.item(row, 3).text(),
                "place": self.tableCars.item(row, 4).text(),
                "time": self.tableCars.item(row, 5).text(),
                "discount": self.tableCars.item(row, 8).text(),
                "status": self.tableCars.item(row, 9).text(),
            })
        with open(self.DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

    def load_data_from_file(self):
        # пробуем v2, если нет — мигрируем из старого parking_base.json
        path = self.DATA_FILE
        if not os.path.exists(path) and os.path.exists("parking_base.json"):
            # миграция старого формата
            try:
                with open("parking_base.json", "r", encoding="utf-8") as f:
                    old = json.load(f)
                migrated = []
                used_places = set()
                for i, car in enumerate(old):
                    # подобрать свободное место
                    place = i+1
                    while place in used_places and place <= TOTAL_PLACES:
                        place += 1
                    used_places.add(place)
                    migrated.append({
                        "plate": f"А{100+place:03d}ВС 799",
                        "brand": car.get("brand","—"),
                        "owner": car.get("owner","—"),
                        "phone": "",
                        "place": str(place),
                        "time": car.get("time", datetime.now().strftime("%Y-%m-%d %H:%M")),
                        "discount": car.get("discount","0%"),
                        "status": "На стоянке"
                    })
                with open(path, "w", encoding="utf-8") as out:
                    json.dump(migrated, out, ensure_ascii=False, indent=4)
            except Exception as e:
                print(f"Миграция не удалась: {e}")
                return

        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for car in data:
                disc = car.get("discount","0%").replace("%","")
                try:
                    disc_int = int(disc)
                except:
                    disc_int = 0
                self.insert_row_to_table(
                    car.get("plate",""),
                    car.get("brand",""),
                    car.get("owner",""),
                    car.get("phone",""),
                    int(car.get("place",1)) if str(car.get("place","1")).isdigit() else 1,
                    car.get("time", datetime.now().strftime("%Y-%m-%d %H:%M")),
                    disc_int
                )
        except Exception as e:
            print(f"Ошибка чтения {path}: {e}")


ParkingAppV2 = ParkingApp  # alias для совместимости с main_v2

if __name__ == "__main__":
    app = QApplication(sys.argv)
    apply_light_palette(app)
    w = ParkingApp()
    w.show()
    sys.exit(app.exec())
