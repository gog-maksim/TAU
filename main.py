import sys
import math
import re
from datetime import datetime
from PyQt6 import uic
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTableWidgetItem, QMessageBox,
    QHeaderView, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSpinBox, QDialogButtonBox, QFrame, QGridLayout,
    QToolBar, QLineEdit, QTableWidget, QDateEdit, QCheckBox, QComboBox
)
from PyQt6.QtCore import Qt, QTimer, QDate
from PyQt6.QtGui import QPalette, QColor

try:
    from db_config import test_connection
    from db_init import init_database
    _DB_AVAILABLE = True
except ImportError:
    _DB_AVAILABLE = False
    test_connection = None
    init_database = None


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
    def __init__(self, parent, car: dict, hourly_rate: int, known_cost=None, cost_source="расчет"):
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
        duration_h, calc_cost = self._calc()
        cost = int(known_cost) if known_cost is not None else calc_cost
        cost_tag = "из БД" if known_cost is not None else cost_source
        self.label_duration = QLabel(f"Текущая длительность: <b>{self._fmt_duration(duration_h)}</b>")
        self.label_duration.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.label_duration)

        self.label_cost = QLabel(f"Расчетная стоимость: <b>{cost} ₽</b>  <span style='color:#6b7a90'>( {duration_h} ч × {hourly_rate} ₽ × скидка, {cost_tag} )</span>")
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
        pay = self.spin_pay.value()
        if pay > self.cost:
            over = pay - self.cost
            ans = QMessageBox.question(self, "Переплата",
                                       f"Внесено на {over} ₽ больше стоимости ({self.cost} ₽).\nПодтвердить?")
            if ans != QMessageBox.StandardButton.Yes:
                return
        self.additional_payment = pay
        # для демо: если не хватает — предлагаем подтвердить как долг
        if self.additional_payment < self.cost:
            # если нажал "Подтвердить выезд" без полной оплаты — трактуем как долг
            self.result_action = "debt"
        else:
            self.result_action = "paid"
        self.accept()


def _make_table(cols):
    t = QTableWidget()
    t.setColumnCount(len(cols))
    t.setHorizontalHeaderLabels(cols)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.verticalHeader().setVisible(False)
    t.setAlternatingRowColors(True)
    return t


class BlacklistDialog(QDialog):
    """п.8: чёрный список. Удаления нет — только флаг is_active."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Чёрный список")
        self.setMinimumSize(620, 420)
        self.setStyleSheet(LIGHT_QSS)
        lay = QVBoxLayout(self)

        form = QHBoxLayout()
        self.input_plate = QLineEdit()
        self.input_plate.setPlaceholderText("Гос.номер")
        self.input_reason = QLineEdit()
        self.input_reason.setPlaceholderText("Причина")
        btn_add = QPushButton("Добавить")
        btn_add.setStyleSheet("background:#c0392b; color:white; font-weight:bold; padding:8px; border-radius:6px;")
        btn_add.clicked.connect(self.add_entry)
        form.addWidget(self.input_plate)
        form.addWidget(self.input_reason)
        form.addWidget(btn_add)
        lay.addLayout(form)

        self.table = _make_table(["ID", "Номер", "Марка", "Причина", "Дата", "Активна"])
        lay.addWidget(self.table)

        row = QHBoxLayout()
        self.btn_toggle = QPushButton("Деактивировать / восстановить")
        self.btn_toggle.clicked.connect(self.toggle_entry)
        btn_close = QPushButton("Закрыть")
        btn_close.clicked.connect(self.accept)
        row.addWidget(self.btn_toggle)
        row.addWidget(btn_close)
        lay.addLayout(row)
        self.reload()

    def reload(self):
        from db_repo import get_blacklist
        try:
            rows = get_blacklist(active_only=False)
        except Exception as e:
            QMessageBox.warning(self, "БД", str(e))
            return
        self.table.setRowCount(0)
        for r in rows:
            i = self.table.rowCount()
            self.table.insertRow(i)
            self.table.setItem(i, 0, QTableWidgetItem(str(r["id"])))
            self.table.setItem(i, 1, QTableWidgetItem(r["plate"] or ""))
            self.table.setItem(i, 2, QTableWidgetItem(r["brand"] or ""))
            self.table.setItem(i, 3, QTableWidgetItem(r["reason"] or ""))
            self.table.setItem(i, 4, QTableWidgetItem(r["date_added"] or ""))
            self.table.setItem(i, 5, QTableWidgetItem("Да" if r["is_active"] else "Нет"))

    def add_entry(self):
        from db_repo import add_blacklist_entry
        plate = self.input_plate.text().strip().upper()
        if len(plate) < 5:
            QMessageBox.warning(self, "Ошибка", "Укажите гос.номер.")
            return
        try:
            bid = add_blacklist_entry(plate, self.input_reason.text().strip())
        except Exception as e:
            QMessageBox.warning(self, "БД", str(e))
            return
        print(f"[DB-bl] добавлен {plate} id={bid}")
        self.input_plate.clear()
        self.input_reason.clear()
        self.reload()

    def toggle_entry(self):
        from db_repo import set_blacklist_active
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "ЧС", "Выберите запись.")
            return
        bid = int(self.table.item(row, 0).text())
        plate = self.table.item(row, 1).text()
        active = self.table.item(row, 5).text() == "Да"
        verb = "деактивировать" if active else "восстановить"
        ans = QMessageBox.question(self, "Чёрный список", f"{verb.capitalize()} запись {plate}?")
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            set_blacklist_active(bid, not active)
        except Exception as e:
            QMessageBox.warning(self, "БД", str(e))
            return
        print(f"[DB-bl] id={bid} active={not active}")
        self.reload()


class DiscountsDialog(QDialog):
    """п.8: скидки. Отключение — флаг is_active, записи храним."""
    def __init__(self, parent):
        super().__init__(parent)
        self.parent_app = parent
        self.setWindowTitle("Скидки")
        self.setMinimumSize(520, 420)
        self.setStyleSheet(LIGHT_QSS)
        lay = QVBoxLayout(self)

        form = QHBoxLayout()
        self.input_name = QLineEdit()
        self.input_name.setPlaceholderText("Название")
        self.spin_percent = QSpinBox()
        self.spin_percent.setRange(0, 100)
        self.spin_percent.setSuffix(" %")
        btn_add = QPushButton("Добавить")
        btn_add.setStyleSheet("background:#27ae60; color:white; font-weight:bold; padding:8px; border-radius:6px;")
        btn_add.clicked.connect(self.add_discount)
        form.addWidget(self.input_name)
        form.addWidget(self.spin_percent)
        form.addWidget(btn_add)
        lay.addLayout(form)

        self.table = _make_table(["ID", "Название", "%", "Активна"])
        self.table.itemSelectionChanged.connect(self._on_select)
        lay.addWidget(self.table)

        row = QHBoxLayout()
        self.btn_save = QPushButton("Сохранить изменения")
        self.btn_save.clicked.connect(self.save_selected)
        self.btn_toggle = QPushButton("Отключить / включить")
        self.btn_toggle.clicked.connect(self.toggle_selected)
        btn_close = QPushButton("Закрыть")
        btn_close.clicked.connect(self.accept)
        row.addWidget(self.btn_save)
        row.addWidget(self.btn_toggle)
        row.addWidget(btn_close)
        lay.addLayout(row)
        self.reload()

    def reload(self):
        from db_repo import get_discounts
        try:
            rows = get_discounts(active_only=False)
        except Exception as e:
            QMessageBox.warning(self, "БД", str(e))
            return
        self.table.setRowCount(0)
        for r in rows:
            i = self.table.rowCount()
            self.table.insertRow(i)
            self.table.setItem(i, 0, QTableWidgetItem(str(r["id"])))
            self.table.setItem(i, 1, QTableWidgetItem(r["name"]))
            self.table.setItem(i, 2, QTableWidgetItem(str(r["percent"])))
            self.table.setItem(i, 3, QTableWidgetItem("Да" if r.get("is_active") else "Нет"))

    def _on_select(self):
        row = self.table.currentRow()
        if row < 0:
            return
        self.input_name.setText(self.table.item(row, 1).text())
        try:
            self.spin_percent.setValue(int(self.table.item(row, 2).text()))
        except Exception:
            pass

    def add_discount(self):
        from db_repo import add_discount
        try:
            did = add_discount(self.input_name.text(), self.spin_percent.value())
        except Exception as e:
            QMessageBox.warning(self, "БД", str(e))
            return
        print(f"[DB-disc] добавлена id={did}")
        self.input_name.clear()
        self.reload()
        self.parent_app.refresh_reference_data()

    def save_selected(self):
        from db_repo import update_discount
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Скидки", "Выберите запись.")
            return
        ans = QMessageBox.question(
            self, "Скидки",
            "Изменение создаст новую версию скидки (старая деактивируется, история не меняется).\nПродолжить?")
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            nid = update_discount(int(self.table.item(row, 0).text()), self.input_name.text(), self.spin_percent.value())
        except Exception as e:
            QMessageBox.warning(self, "БД", str(e))
            return
        print(f"[DB-disc] новая версия id={nid}")
        self.reload()
        self.parent_app.refresh_reference_data()

    def toggle_selected(self):
        from db_repo import set_discount_active
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Скидки", "Выберите запись.")
            return
        active = self.table.item(row, 3).text() == "Да"
        verb = "отключить" if active else "включить"
        ans = QMessageBox.question(self, "Скидки", f"{verb.capitalize()} скидку «{self.table.item(row, 1).text()}»?")
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            set_discount_active(int(self.table.item(row, 0).text()), not active)
        except Exception as e:
            QMessageBox.warning(self, "БД", str(e))
            return
        self.reload()
        self.parent_app.refresh_reference_data()


class TariffDialog(QDialog):
    """п.8: тариф. Изменение = новая версия, старые не трогаем."""
    def __init__(self, parent):
        super().__init__(parent)
        self.parent_app = parent
        self.setWindowTitle("Тариф")
        self.setMinimumWidth(380)
        self.setStyleSheet(LIGHT_QSS)
        lay = QVBoxLayout(self)
        self.label_cur = QLabel()
        lay.addWidget(self.label_cur)
        row = QHBoxLayout()
        row.addWidget(QLabel("Новый тариф (₽/час):"))
        self.spin_rate = QSpinBox()
        self.spin_rate.setRange(1, 10000)
        self.spin_rate.setSingleStep(10)
        row.addWidget(self.spin_rate)
        lay.addLayout(row)
        hint = QLabel("Старый тариф деактивируется, но остаётся в БД — история не меняется.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        btns = QHBoxLayout()
        btn_save = QPushButton("Сохранить")
        btn_save.setStyleSheet("background:#27ae60; color:white; font-weight:bold; padding:8px; border-radius:6px;")
        btn_save.clicked.connect(self.save)
        btn_close = QPushButton("Закрыть")
        btn_close.clicked.connect(self.accept)
        btns.addWidget(btn_save)
        btns.addWidget(btn_close)
        lay.addLayout(btns)
        self.reload()

    def reload(self):
        from db_repo import get_tariffs
        try:
            t = get_tariffs()[0]
        except Exception as e:
            QMessageBox.warning(self, "БД", str(e))
            return
        self.label_cur.setText(f"Текущий: <b>{t['name']} — {t['hourly_rate']} ₽/час</b> (id={t['id']})")
        self.label_cur.setTextFormat(Qt.TextFormat.RichText)
        self.spin_rate.setValue(int(t["hourly_rate"]))

    def save(self):
        from db_repo import replace_active_tariff
        ans = QMessageBox.question(
            self, "Тариф",
            f"Установить тариф {self.spin_rate.value()} ₽/час?\nСтарый сохранится в БД для истории.")
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            nid = replace_active_tariff(self.spin_rate.value())
        except Exception as e:
            QMessageBox.warning(self, "БД", str(e))
            return
        print(f"[DB-tariff] новый id={nid} rate={self.spin_rate.value()}")
        self.parent_app.refresh_reference_data()
        self.reload()


class HistoryDialog(QDialog):
    """п.9: завершённые стоянки. Только чтение, ничего не удаляем."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("История стоянок")
        self.setMinimumSize(900, 520)
        self.setStyleSheet(LIGHT_QSS)
        lay = QVBoxLayout(self)

        filt = QHBoxLayout()
        self.f_plate = QLineEdit()
        self.f_plate.setPlaceholderText("Номер")
        self.f_plate.setMaximumWidth(130)
        self.f_owner = QLineEdit()
        self.f_owner.setPlaceholderText("ФИО")
        self.f_owner.setMaximumWidth(170)
        self.f_status = QComboBox()
        self.f_status.addItems(["Все", "Оплачено", "Долг"])
        self.f_use_period = QCheckBox("Период")
        self.f_from = QDateEdit()
        self.f_from.setCalendarPopup(True)
        self.f_from.setDisplayFormat("yyyy-MM-dd")
        self.f_to = QDateEdit()
        self.f_to.setCalendarPopup(True)
        self.f_to.setDisplayFormat("yyyy-MM-dd")
        self.f_from.setDate(QDate.currentDate().addMonths(-1))
        self.f_to.setDate(QDate.currentDate())
        btn_find = QPushButton("Найти")
        btn_find.clicked.connect(self._apply_filter)
        for wdg in (self.f_plate, self.f_owner):
            wdg.textChanged.connect(self._apply_filter)
        self.f_status.currentTextChanged.connect(self._apply_filter)
        filt.addWidget(self.f_plate)
        filt.addWidget(self.f_owner)
        filt.addWidget(self.f_status)
        filt.addWidget(self.f_use_period)
        filt.addWidget(self.f_from)
        filt.addWidget(self.f_to)
        filt.addWidget(btn_find)
        lay.addLayout(filt)

        self.table = _make_table(["Сессия", "Номер", "Марка", "Владелец", "Место",
                                  "Въезд", "Выезд", "Тариф", "Скидка", "Итого", "Оплачено", "Долг"])
        lay.addWidget(self.table)
        row = QHBoxLayout()
        self.label_count = QLabel()
        btn_refresh = QPushButton("Обновить")
        btn_refresh.clicked.connect(self.reload)
        btn_close = QPushButton("Закрыть")
        btn_close.clicked.connect(self.accept)
        row.addWidget(self.label_count)
        row.addWidget(btn_refresh)
        row.addWidget(btn_close)
        lay.addLayout(row)
        self.reload()

    def reload(self):
        from db_repo import get_full_history
        try:
            rows = get_full_history()
        except Exception as e:
            QMessageBox.warning(self, "БД", str(e))
            return
        self.table.setRowCount(0)
        for r in rows:
            i = self.table.rowCount()
            self.table.insertRow(i)
            vals = [r["session_id"], r["plate"], r["brand"], r["owner"], r["place"],
                    r["arrival"], r["departure"], f'{r["rate"]} ₽/ч', f'{r["discount"]}%',
                    f'{r["total"]} ₽', f'{r["paid"]} ₽', f'{r["debt"]} ₽']
            for c, v in enumerate(vals):
                self.table.setItem(i, c, QTableWidgetItem(str(v)))
        print(f"[DB-history] показано {len(rows)} завершённых стоянок")
        self._apply_filter()

    def _apply_filter(self):
        plate = self.f_plate.text().strip().lower()
        owner = self.f_owner.text().strip().lower()
        status = self.f_status.currentText()
        use_period = self.f_use_period.isChecked()
        d_from = self.f_from.date().toString("yyyy-MM-dd")
        d_to = self.f_to.date().toString("yyyy-MM-dd")
        shown = 0
        for r in range(self.table.rowCount()):
            ok = True
            if plate and plate not in self.table.item(r, 1).text().lower():
                ok = False
            if ok and owner and owner not in self.table.item(r, 3).text().lower():
                ok = False
            if ok and status != "Все":
                debt_txt = self.table.item(r, 11).text()
                is_paid = debt_txt.startswith("0 ")
                if status == "Оплачено" and not is_paid:
                    ok = False
                if status == "Долг" and is_paid:
                    ok = False
            if ok and use_period:
                day = self.table.item(r, 5).text()[:10]
                if not (d_from <= day <= d_to):
                    ok = False
            self.table.setRowHidden(r, not ok)
            shown += ok
        self.label_count.setText(f"Показано: {shown} из {self.table.rowCount()}")


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
    """Главное окно: въезд/выезд/мониторинг/схема. Источник данных — PostgreSQL."""
    def __init__(self):
        super().__init__()
        uic.loadUi("parking.ui", self)

        self.HOURLY_RATE = HOURLY_RATE

        # --- верхняя панель: разделы ---
        toolbar = QToolBar("Разделы", self)
        toolbar.setMovable(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)
        toolbar.addAction("Чёрный список").triggered.connect(self.open_blacklist)
        toolbar.addAction("Скидки").triggered.connect(self.open_discounts)
        toolbar.addAction("Тариф").triggered.connect(self.open_tariff)
        toolbar.addAction("История").triggered.connect(self.open_history)

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
        self.input_plate.editingFinished.connect(self._autofill_by_plate)
        self.combo_place.currentTextChanged.connect(self._highlight_grid_selection)
        self.tableCars.itemSelectionChanged.connect(self._on_table_select)

        # Сетка 10x10
        self.place_buttons = {}  # place:int -> QPushButton
        self._build_grid()

        # Таймер авто-пересчета каждую минуту
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_calculations)
        self.timer.start(60_000)

        # --- подключение к PostgreSQL ---
        self.db_ok = False
        self.db_message = "модуль БД недоступен"
        self.db_snapshot = None
        if _DB_AVAILABLE:
            try:
                init_database()
                ok, msg = test_connection()
                self.db_ok = ok
                self.db_message = "подключено" if ok else msg
            except Exception as e:
                self.db_ok = False
                self.db_message = str(e)

        if not self.db_ok:
            QMessageBox.critical(self, "Нет связи с БД",
                                 f"Не удалось подключиться к PostgreSQL:\n{self.db_message}")
            self.tariff_id = None
            self.tariff_name = "Стандартный"
            self.statusbar.showMessage("БД: нет связи", 6000)
            return

        self.refresh_reference_data()
        if getattr(self, "tariff_id", None) is None:
            self.tariff_id = None
            self.tariff_name = "Стандартный"
        self.load_table_from_db()
        self.refresh_calculations()
        self.update_monitoring()
        self.statusbar.showMessage(
            f"БД: подключено (тариф:{self.tariff_name} {self.HOURLY_RATE} ₽/ч, "
            f"скидок:{self.combo_discount.count()}, на стоянке:{self.tableCars.rowCount()})",
            6000
        )

    def refresh_reference_data(self):
        """Перечитать справочники из БД (тарифы/скидки/занятость) и обновить виджеты."""
        if not self.db_ok:
            return
        try:
            from db_repo import get_snapshot
            self.db_snapshot = get_snapshot()
        except Exception as e:
            print(f"[DB-read] ошибка чтения: {e}")
            return
        self.reload_pricing_from_db()

    def open_blacklist(self):
        BlacklistDialog(self).exec()

    def open_discounts(self):
        DiscountsDialog(self).exec()

    def open_tariff(self):
        TariffDialog(self).exec()

    def open_history(self):
        HistoryDialog(self).exec()

    def load_table_from_db(self):
        """Перестроить таблицу и сетку из активных сессий БД."""
        from db_repo import get_active_sessions
        active = get_active_sessions()
        self.tableCars.setRowCount(0)
        for car in active:
            try:
                disc = int(car.get("discount_percent", 0))
            except Exception:
                disc = 0
            self.insert_row_to_table(
                car.get("plate", ""),
                car.get("brand", ""),
                car.get("owner", ""),
                car.get("phone", "") or "",
                int(car.get("place", 1)),
                car.get("time", datetime.now().strftime("%Y-%m-%d %H:%M")),
                disc,
            )
        self.refresh_calculations()
        self.update_monitoring()
        print(f"[DB-load] загружено из БД: {len(active)} авто, места={[c['place'] for c in active]}")
        return active

    def reload_pricing_from_db(self):
        """п.3: подтянуть тариф и скидки из БД в виджеты. Возвращает True если из БД."""
        if not self.db_ok or not self.db_snapshot:
            return False
        tariffs = self.db_snapshot.get("tariffs", [])
        discounts = self.db_snapshot.get("discounts", [])
        if tariffs:
            t = tariffs[0]  # пока один активный тариф
            self.HOURLY_RATE = int(t["hourly_rate"])
            self.tariff_id = int(t["id"])
            self.tariff_name = str(t["name"])
        if discounts:
            cur_text = self.combo_discount.currentText()
            m = re.search(r"(\d+)%", cur_text)
            cur_pct = int(m.group(1)) if m else 0
            self.combo_discount.blockSignals(True)
            self.combo_discount.clear()
            for d in discounts:
                self.combo_discount.addItem(f"{d['name']} ({d['percent']}%)", userData=int(d["id"]))
            restored = False
            for i in range(self.combo_discount.count()):
                if f"{cur_pct}%" in self.combo_discount.itemText(i):
                    self.combo_discount.setCurrentIndex(i)
                    restored = True
                    break
            if not restored:
                self.combo_discount.setCurrentIndex(0)
            self.combo_discount.blockSignals(False)
        try:
            self.label_info.setText(f"* — обязательные поля\nТариф: {self.tariff_name} — {self.HOURLY_RATE} ₽/час (из БД)")
        except Exception:
            pass
        print(f"[DB-pricing] тариф={self.tariff_name} {self.HOURLY_RATE} ₽/ч (id={self.tariff_id}), "
              f"скидок={self.combo_discount.count()}")
        return True

    def current_discount_id(self):
        """id скидки из комбо для будущих записей в БД (п.5)."""
        try:
            data = self.combo_discount.itemData(self.combo_discount.currentIndex())
            return int(data) if data is not None else None
        except Exception:
            return None

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

    def _autofill_by_plate(self):
        """Автоподстановка: если авто уже есть в БД — заполнить пустые поля."""
        if not self.db_ok:
            return
        plate = re.sub(r"\s+", " ", self.input_plate.text()).strip().upper()
        if len(plate) < 5:
            return
        try:
            from db_repo import get_vehicle_by_plate
            card = get_vehicle_by_plate(plate)
        except Exception:
            return
        if not card:
            return
        if not self.input_brand.text().strip() and card.get("brand") and card["brand"] != "(ЧС)":
            self.input_brand.setText(card["brand"])
        if not self.input_owner.text().strip() and card.get("owner") and card["owner"] != "(ЧС)":
            self.input_owner.setText(card["owner"])
        if not self.input_phone.text().strip() and card.get("phone"):
            self.input_phone.setText(card["phone"])
        if card.get("last_discount_id"):
            for i in range(self.combo_discount.count()):
                if self.combo_discount.itemData(i) == card["last_discount_id"]:
                    self.combo_discount.setCurrentIndex(i)
                    break
        self.statusbar.showMessage(f"Данные {plate} подставлены из БД", 4000)

    # ---------- Add ----------
    def add_car(self):
        plate = re.sub(r"\s+", " ", self.input_plate.text()).strip().upper()
        brand = self.input_brand.text().strip()
        owner = re.sub(r"\s+", " ", self.input_owner.text()).strip()
        phone = self.input_phone.text().strip()
        place_str = self.combo_place.currentText().strip()
        discount_text = self.combo_discount.currentText()

        if not plate or not brand or not owner or not place_str:
            QMessageBox.warning(self, "Ошибка", "Заполните обязательные поля: гос.номер, марка, ФИО, место.")
            return

        # госномер: буквы (кириллица/латиница), цифры, пробел и дефис; минимум 5 символов
        if len(plate) < 5 or not re.fullmatch(r"[А-ЯЁA-Z0-9 \-]+", plate):
            QMessageBox.warning(self, "Ошибка", "Гос.номер некорректен: допускаются буквы, цифры, пробел и дефис.")
            return

        if len(brand) < 2:
            QMessageBox.warning(self, "Ошибка", "Укажите марку/модель (минимум 2 символа).")
            return

        if len(owner.replace(" ", "")) < 3:
            QMessageBox.warning(self, "Ошибка", "ФИО владельца слишком короткое.")
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

        # телефон — опционально, но если введен — допустимый формат (7–15 цифр)
        digits = re.sub(r"\D", "", phone)
        if phone and not 7 <= len(digits) <= 15:
            QMessageBox.warning(self, "Ошибка", "Номер телефона некорректен (нужно 7–15 цифр).")
            return

        if not self.db_ok:
            QMessageBox.warning(self, "БД", "Нет связи с базой данных.")
            return

        # чёрный список: предупреждаем, решение за оператором
        try:
            from db_repo import is_blacklisted
            bl = is_blacklisted(plate)
        except Exception:
            bl = None
        if bl:
            ans = QMessageBox.question(
                self, "Автомобиль в чёрном списке",
                f"⚠️ {plate} в чёрном списке.\nПричина: {bl.get('reason') or '—'}\n\nПродолжить регистрацию въезда?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if ans != QMessageBox.StandardButton.Yes:
                self.statusbar.showMessage(f"Въезд {plate} отклонён оператором (ЧС)", 4000)
                return

        # скидка — только активная из БД
        if self.current_discount_id() is None:
            QMessageBox.warning(self, "Ошибка", "Выберите действующую скидку из списка.")
            return

        # скидка
        m = re.search(r"(\d+)%", discount_text)
        discount = int(m.group(1)) if m else 0

        time_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        # --- въезд: пишем в БД ---
        db_session_id = None
        try:
            from db_repo import create_parking_entry, get_snapshot
            if not self.tariff_id:
                raise ValueError("Тариф не загружен из БД")
            db_session_id = create_parking_entry(
                plate, brand, owner, phone, place,
                tariff_id=self.tariff_id,
                discount_id=self.current_discount_id(),
                arrival_time=time_str,
            )
            print(f"[DB-write] въезд: {plate} место {place} session={db_session_id}")
            try:
                self.db_snapshot = get_snapshot()
            except Exception:
                pass
        except Exception as e:
            QMessageBox.warning(self, "БД: въезд отклонен", str(e))
            return

        # вставляем — длительность/стоимость посчитаются в refresh
        self.insert_row_to_table(plate, brand, owner, phone, place, time_str, discount)

        self.refresh_calculations()
        self.update_monitoring()

        # очистка
        self.input_plate.clear()
        self.input_brand.clear()
        self.input_owner.clear()
        self.input_phone.clear()
        # перевести combo на следующее свободное
        self._select_next_free_place()

        msg = f"✅ Автомобиль {plate} успешно зарегистрирован на место №{place}"
        if db_session_id is not None:
            msg += f" (сессия {db_session_id})"
        self.statusbar.showMessage(msg, 4000)

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

        # стоимость из БД (свежий calc_session_cost); машины нет среди текущих — обновить таблицу
        db_session_id = None
        db_cost = None
        if self.db_ok:
            try:
                from db_repo import get_active_session_by_plate, calc_current_cost
                sess = get_active_session_by_plate(plate)
                if sess:
                    db_session_id = int(sess["session_id"])
                    db_cost = calc_current_cost(db_session_id)
                    print(f"[DB-cost] {plate} session={db_session_id} cost={db_cost} (stored={sess.get('stored_cost')})")
            except Exception as e:
                print(f"[DB-cost] ошибка: {e}")
        if db_session_id is None:
            QMessageBox.warning(self, "Выезд", f"Автомобиля {plate} нет среди текущих — таблица будет обновлена.")
            self.load_table_from_db()
            return

        dlg = CheckoutDialog(self, car, self.HOURLY_RATE, known_cost=db_cost)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        # выезд одной транзакцией: платёж + закрытие сессии (история сохраняется)
        try:
            from db_repo import checkout_session, get_snapshot
            closed = checkout_session(db_session_id, dlg.additional_payment)
            db_departure = closed["departure_time"]
            print(f"[DB-exit] session={db_session_id} departure={db_departure} "
                  f"total={closed['total_cost']} paid={closed['paid_total']}")
            try:
                self.db_snapshot = get_snapshot()
            except Exception:
                pass
        except Exception as e:
            QMessageBox.warning(self, "БД: выезд не оформлен", str(e))
            self.load_table_from_db()
            return

        # подтверждение — убираем строку из таблицы (в БД история сохраняется)
        self.tableCars.removeRow(row)
        self.update_monitoring()
        self.refresh_calculations()

        if dlg.result_action == "paid":
            QMessageBox.information(self, "Выезд оформлен", f"✅ Выезд оформлен.\nАвтомобиль {plate} выехал.\nОплачено: {dlg.cost} ₽.\nВремя выезда: {db_departure}.\nМесто №{place} освобождено.")
        else:
            remaining = dlg.cost - dlg.additional_payment
            QMessageBox.information(self, "Выезд оформлен", f"✅ Выезд оформлен. Задолженность: {remaining} ₽\nВнесено: {dlg.additional_payment} ₽.\nВремя выезда: {db_departure}.\nМесто №{place} освобождено.")
        self.statusbar.showMessage(f"Место №{place} свободно", 4000)

ParkingAppV2 = ParkingApp  # alias для совместимости с main_v2

if __name__ == "__main__":
    app = QApplication(sys.argv)
    apply_light_palette(app)
    w = ParkingApp()
    w.show()
    sys.exit(app.exec())
