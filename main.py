import sys
import json
import os
from datetime import datetime
from PyQt6 import uic
from PyQt6.QtWidgets import QApplication, QMainWindow, QTableWidgetItem, QMessageBox, QHeaderView
from PyQt6.QtCore import Qt


class ParkingApp(QMainWindow):
    def __init__(self):
        super().__init__()
        # Загружаем графику из твоего файла parking.ui
        uic.loadUi("parking.ui", self)

        # Базовые настройки
        self.HOURLY_RATE = 100  # Тариф: 100 рублей в час
        self.DATA_FILE = "parking_base.json"  # Файл, куда сохраняются машины

        # Настройка таблицы (растягиваем столбцы на всю ширину)
        self.tableCars.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        # Связываем кнопки с функциями (сигналы и слоты, как в Qt Creator)
        self.btn_add.clicked.connect(self.add_car)

        # Добавляем кнопку "Выписать авто" (если ее нет в ui, создадим программно)
        # Она будет удалять выбранную строчку
        if not hasattr(self, 'btn_remove'):
            from PyQt6.QtWidgets import QPushButton
            self.btn_remove = QPushButton("Выписать авто (Выезд)", self)
            self.btn_remove.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold; padding: 8px;")
            self.btn_remove.clicked.connect(self.remove_car)
            # Добавляем её в левую панель формы
            self.centralwidget.layout().itemAt(0).layout().addWidget(self.btn_remove)
        else:
            self.btn_remove.clicked.connect(self.remove_car)

        # Автоматически загружаем сохраненные машины при старте
        self.load_data_from_file()

    def add_car(self):
        """Слот для добавления машины на стоянку"""
        brand = self.input_brand.text().strip()
        owner = self.input_owner.text().strip()
        hours_str = self.input_hours.text().strip()

        # Валидация полей
        if not brand or not owner or not hours_str:
            QMessageBox.warning(self, "Ошибка", "Пожалуйста, заполните все поля ввода!")
            return

        try:
            hours = int(hours_str)
            if hours <= 0: raise ValueError
        except ValueError:
            QMessageBox.warning(self, "Ошибка", "В поле 'Часов' должно быть целое положительное число!")
            return

        # Логика расчетов (ТАУ-составляющая)
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        discount_text = self.combo_discount.currentText()

        # Считаем скидку
        discount_percent = 0
        if "10%" in discount_text:
            discount_percent = 10
        elif "20%" in discount_text:
            discount_percent = 20

        # Итоговая стоимость
        cost = int((hours * self.HOURLY_RATE) * (1 - discount_percent / 100))
        status = self.combo_status.currentText()

        # Вставляем строку в интерфейс
        self.insert_row_to_table(brand, owner, current_time, hours, cost, discount_percent, status)

        # Сохраняем в файл JSON
        self.save_data_to_file()

        # Очищаем форму для новой машины
        self.input_brand.clear()
        self.input_owner.clear()
        self.input_hours.clear()

    def insert_row_to_table(self, brand, owner, time, hours, cost, discount, status):
        """Вспомогательная функция отрисовки строки таблицы"""
        row = self.tableCars.rowCount()
        self.tableCars.insertRow(row)

        self.tableCars.setItem(row, 0, QTableWidgetItem(brand))
        self.tableCars.setItem(row, 1, QTableWidgetItem(owner))
        self.tableCars.setItem(row, 2, QTableWidgetItem(time))
        self.tableCars.setItem(row, 3, QTableWidgetItem(str(hours)))
        self.tableCars.setItem(row, 4, QTableWidgetItem(f"{cost} руб."))
        self.tableCars.setItem(row, 5, QTableWidgetItem(f"{discount}%"))

        # Красиво подсвечиваем статус оплаты
        status_item = QTableWidgetItem(status)
        if status == "Задолженность":
            status_item.setForeground(Qt.GlobalColor.red)  # Должники — красные
        else:
            status_item.setForeground(Qt.GlobalColor.darkGreen)  # Оплачено — зеленые
        self.tableCars.setItem(row, 6, status_item)

    def remove_car(self):
        """Слот для оформления выезда автомобиля"""
        current_row = self.tableCars.currentRow()

        if current_row < 0:
            QMessageBox.warning(self, "Выезд авто", "Сначала кликните по строке с машиной в таблице!")
            return

        brand = self.tableCars.item(current_row, 0).text()
        owner = self.tableCars.item(current_row, 1).text()
        cost_str = self.tableCars.item(current_row, 4).text()
        status = self.tableCars.item(current_row, 6).text()

        # Информационное окно для оператора
        msg = f"Автомобиль: {brand}\nВладелец: {owner}\nК оплате: {cost_str}\nСтатус: {status}\n\nПодтвердить выезд?"
        confirm = QMessageBox.question(self, "Оформление выезда", msg,
                                       QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)

        if confirm == QMessageBox.StandardButton.Yes:
            self.tableCars.removeRow(current_row)
            self.save_data_to_file()  # Пересохраняем базу без этой машины
            QMessageBox.information(self, "Успех", "Машина успешно выписана с автостоянки!")

    def save_data_to_file(self):
        """Сохранение всей таблицы в JSON-файл"""
        data_list = []
        for row in range(self.tableCars.rowCount()):
            car = {
                "brand": self.tableCars.item(row, 0).text(),
                "owner": self.tableCars.item(row, 1).text(),
                "time": self.tableCars.item(row, 2).text(),
                "hours": self.tableCars.item(row, 3).text(),
                "cost": self.tableCars.item(row, 4).text(),
                "discount": self.tableCars.item(row, 5).text(),
                "status": self.tableCars.item(row, 6).text()
            }
            data_list.append(car)

        with open(self.DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data_list, f, ensure_ascii=False, indent=4)

    def load_data_from_file(self):
        """Загрузка базы данных машин при запуске"""
        if not os.path.exists(self.DATA_FILE):
            return

        try:
            with open(self.DATA_FILE, "r", encoding="utf-8") as f:
                data_list = json.load(f)
                for car in data_list:
                    # Чистим строки от знаков рубля и процентов для функции вставки
                    disc = car["discount"].replace("%", "")
                    cost = car["cost"].replace(" руб.", "")
                    self.insert_row_to_table(
                        car["brand"], car["owner"], car["time"],
                        int(car["hours"]), cost, disc, car["status"]
                    )
        except Exception as e:
            print(f"Ошибка чтения файла базы данных: {e}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ParkingApp()
    window.show()
    sys.exit(app.exec())
