import statistics
import sys
import threading
import time
from datetime import datetime

import matplotlib
import serial
from PyQt5.QtCore import pyqtSlot, Qt
from PyQt5.QtWidgets import QPushButton, QHBoxLayout, QVBoxLayout, QMainWindow, QWidget, QStackedWidget, \
    QFileDialog, QLabel, QFrame, QMessageBox, QSlider
from PyQt5 import QtWidgets, QtCore, QtGui
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

matplotlib.use('Qt5Agg')


# Take care of code indexing, if it starts with zero it may be important for condition checking or indexing later
class SerialCode:
    START_MEASUREMENT = 1
    STOP_MEASUREMENT = 2


class ButtonCode:
    BACK = 1
    STOP = 2


class StackCode:
    HOME = 0
    GRAPH = 1


class ErrorCode:
    NO_ERROR = 0
    INVALID_CONTENT = 1
    NOT_ENOUGH_DATA = 2


class MplCanvas(FigureCanvas):

    def __init__(self, width=30, height=12, dpi=50):
        fig = Figure(figsize=(width, height), dpi=dpi)
        self.axes = fig.add_subplot(111)
        super(MplCanvas, self).__init__(fig)


class EcgWindow(QMainWindow):

    def __init__(self, serial_port, baud_rate, ser, ecg_data, is_measurement_in_progress):
        super(EcgWindow, self).__init__()

        self.serial_port = serial_port
        self.baud_rate = baud_rate
        self.ser = ser
        self.ecg_data = ecg_data
        self.is_measurement_in_progress = is_measurement_in_progress

        self.plot = None
        self.display_count = 2000
        self.xdata = list(range(self.display_count))
        self.ydata = []
        self.min_ydata_value = 0
        self.max_ydata_value = 4095
        self.reset_ydata()

        self.last_processed_index = 0
        self.sampling_time = 2  # milliseconds
        self.sampling_rate = int(1 / (self.sampling_time * pow(10, -3)))  # hertz

        self.measurement_duration = 30  # seconds
        self.time_between_health_data_calculations = 3  # seconds
        self.last_time_health_data_calculated = 0  # seconds

        self.r_peak_detectable = True
        self.r_peak_upper_threshold = 2600
        self.r_peak_lower_threshold = 2000
        self.r_peak_intervals = []  # stores the milliseconds between R peaks
        self.r_peak_interval_counter = 0

        self.button_style = """
            QPushButton {
                border: 2px solid #000;
                border-radius: 3px;
                font-size: 16px;
                background: #000;
                color: #fff;
            }
            QPushButton:hover {
                color: #000;
                background: rgb(255, 218, 87);
            }
        """
        self.button_size = (200, 40)
        self.start_button_default_text = "Start ECG measurement"
        self.load_button_default_text = "Load ECG data"
        self.exit_button_default_text = "Exit"
        self.stop_button_default_text = "Stop ECG measurement"
        self.back_button_default_text = "Back to home screen"
        self.universal_button = QPushButton(self.stop_button_default_text)

        self.label_style = """
            QLabel {
                border: 2px solid #000;
                border-radius: 3px;
                font-size: 16px;
                background: rgb(254, 112, 117);
                color: #000;
            }
        """
        self.label_size = (300, 40)
        self.countdown_label_default_text = "Time remaining: "
        self.rr_interval_label_default_text = "RR interval: "
        self.bpm_label_default_text = "BPM: "
        self.countdown_label = QLabel()
        self.rr_interval_label = QLabel()
        self.bpm_label = QLabel()

        self.home_stack = QWidget()
        self.graph_stack = QWidget()

        self.file_dialog = QFileDialog()
        self.message_box = QMessageBox()

        self.slider = QSlider(Qt.Horizontal, self)
        self.slider_min_value = 0
        self.slider_max_value = 100
        self.slider_jump_value = 0

        self.canvas = MplCanvas()
        self.canvas.axes.set_xlabel("Time (s)", fontsize=20)
        self.canvas.axes.set_ylabel("Measured Voltage (V)", fontsize=20)
        self.canvas.axes.tick_params(axis='both', labelsize=20)
        self.canvas.axes.set_yticks([0, 1024, 2048, 3072, 4095])
        self.canvas.axes.set_yticklabels([0, 0.82, 1.65, 2.47, 3.3])

        self.init_home_ui()
        self.init_graph_ui()

        self.stack = QStackedWidget()
        self.stack.addWidget(self.home_stack)
        self.stack.addWidget(self.graph_stack)

        self.setCentralWidget(self.stack)
        self.setWindowTitle('ECG measurement application')
        self.setWindowIcon(QtGui.QIcon('resources/ecg_icon.png'))

        self.show()

        self.timer = QtCore.QTimer()
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.tick_method)

    def init_home_ui(self):
        layout = QVBoxLayout()

        self.file_dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        self.file_dialog.setViewMode(QFileDialog.ViewMode.List)
        self.file_dialog.setNameFilter("Text Files (*.txt)")

        self.message_box.setIcon(QMessageBox.Critical)
        self.message_box.setStandardButtons(QMessageBox.Ok)

        start_button = QPushButton(self.start_button_default_text)
        start_button.setFixedSize(*self.button_size)
        start_button.setStyleSheet(self.button_style)
        start_button.clicked.connect(self.start_button_action)

        load_button = QPushButton(self.load_button_default_text)
        load_button.setFixedSize(*self.button_size)
        load_button.setStyleSheet(self.button_style)
        load_button.clicked.connect(self.load_button_action)

        exit_button = QPushButton(self.exit_button_default_text)
        exit_button.setFixedSize(*self.button_size)
        exit_button.setStyleSheet(self.button_style)
        exit_button.clicked.connect(self.exit_button_action)

        layout.addWidget(start_button)
        layout.addWidget(load_button)
        layout.addWidget(exit_button)

        layout.setAlignment(Qt.AlignHCenter)
        self.home_stack.setLayout(layout)

    def init_graph_ui(self):
        layout = QVBoxLayout()
        header_layout = QHBoxLayout()

        self.universal_button.setFixedSize(*self.button_size)
        self.universal_button.setStyleSheet(self.button_style)
        self.universal_button.clicked.connect(self.stop_button_action)

        self.countdown_label.setFrameShape(QFrame.Panel)
        self.countdown_label.setText(self.countdown_label_default_text)
        self.countdown_label.setFixedSize(*self.label_size)
        self.countdown_label.setStyleSheet(self.label_style)

        self.rr_interval_label.setFrameShape(QFrame.Panel)
        self.rr_interval_label.setText(self.rr_interval_label_default_text)
        self.rr_interval_label.setFixedSize(*self.label_size)
        self.rr_interval_label.setStyleSheet(self.label_style)

        self.bpm_label.setFrameShape(QFrame.Panel)
        self.bpm_label.setText(self.bpm_label_default_text)
        self.bpm_label.setFixedSize(*self.label_size)
        self.bpm_label.setStyleSheet(self.label_style)

        self.slider.valueChanged[int].connect(self.slider_action)
        self.slider.setMinimum(self.slider_min_value)
        self.slider.setMaximum(self.slider_max_value)
        self.slider.setFixedSize(500, 30)
        self.slider.hide()

        header_layout.addWidget(self.countdown_label)
        header_layout.addWidget(self.rr_interval_label)
        header_layout.addWidget(self.bpm_label)
        header_layout.addWidget(self.universal_button)
        header_widget = QWidget()
        header_widget.setFixedHeight(60)
        header_widget.setLayout(header_layout)

        layout.addWidget(header_widget)
        layout.addWidget(self.canvas)
        layout.addWidget(self.slider, alignment=Qt.AlignHCenter)

        self.graph_stack.setLayout(layout)

    @pyqtSlot()
    def start_button_action(self):
        if self.connect_serial_port():
            self.start_measurement()
            self.switch_stack(StackCode.GRAPH)

    @pyqtSlot()
    def load_button_action(self):
        selected_filename_with_path = self.select_file()
        file_content = self.load_file(selected_filename_with_path)
        if file_content:
            self.load_measurement(file_content)
            self.switch_universal_button(ButtonCode.BACK)
            self.switch_stack(StackCode.GRAPH)

    @pyqtSlot()
    def exit_button_action(self):
        self.close()

    @pyqtSlot()
    def stop_button_action(self):
        self.switch_universal_button(ButtonCode.BACK)
        self.stop_measurement()

    @pyqtSlot()
    def back_button_action(self):
        self.switch_universal_button(ButtonCode.STOP)
        if self.ser[0]:
            self.ser[0].close()
        self.switch_stack(StackCode.HOME)

    def connect_serial_port(self):
        try:
            self.ser[0] = serial.Serial(self.serial_port[0], self.baud_rate[0])
            return True
        except:
            self.message_box.setWindowTitle("Connection error")
            self.message_box.setText("Measurement unit is not connected on COM3 serial port.")
            self.message_box.exec_()
        return False

    def start_measurement(self):
        self.write_serial(SerialCode.START_MEASUREMENT)
        self.reset_measurement_data_and_graph_ui()
        self.timer.start()
        self.is_measurement_in_progress[0] = True

    def reset_measurement_data_and_graph_ui(self):
        self.ecg_data.clear()
        self.r_peak_intervals.clear()
        self.last_processed_index = 0
        self.last_time_health_data_calculated = 0
        self.r_peak_interval_counter = 0
        self.r_peak_detectable = True
        self.reset_ydata()
        self.reset_labels()
        self.canvas.axes.set_xticks([])
        self.slider.hide()
        self.update_plot()

    def switch_stack(self, stack_index):
        self.stack.setCurrentIndex(stack_index)

    def select_file(self):
        if self.file_dialog.exec():
            return self.file_dialog.selectedFiles()[0]
        return None

    def load_file(self, filename_with_path):
        error_code = ErrorCode.NO_ERROR
        if filename_with_path:
            try:
                with open(filename_with_path, 'r') as file:
                    file_content = file.readlines()
                    file_content = [int(line.strip()) for line in file_content]
            except:
                error_code = ErrorCode.INVALID_CONTENT

            if len(file_content) < self.display_count:
                error_code = ErrorCode.NOT_ENOUGH_DATA

            if error_code:
                self.message_box.setWindowTitle("File loading error")
                match error_code:
                    case ErrorCode.INVALID_CONTENT:
                        self.message_box.setText("The selected file is not supported or has invalid content.")
                    case ErrorCode.NOT_ENOUGH_DATA:
                        self.message_box.setText("The selected file does not contain enough data.")
                self.message_box.exec_()
            else:
                return file_content
        return None

    def load_measurement(self, file_content):
        self.reset_measurement_data_and_graph_ui()
        self.ecg_data.extend(file_content)
        self.initialize_slider()
        self.detect_r_peaks()
        self.calculate_and_update_label_values()

    def switch_universal_button(self, button_code):
        self.universal_button.clicked.disconnect()
        match button_code:
            case ButtonCode.BACK:
                self.universal_button.clicked.connect(self.back_button_action)
                self.universal_button.setText(self.back_button_default_text)
            case ButtonCode.STOP:
                self.universal_button.clicked.connect(self.stop_button_action)
                self.universal_button.setText(self.stop_button_default_text)

    def reset_ydata(self):
        self.ydata = [self.min_ydata_value] + [self.max_ydata_value] * (self.display_count - 1)

    def reset_labels(self):
        self.countdown_label.setText(self.countdown_label_default_text + "—")
        self.rr_interval_label.setText(self.rr_interval_label_default_text + "—")
        self.bpm_label.setText(self.bpm_label_default_text + "—")

    def stop_measurement(self):
        self.is_measurement_in_progress[0] = False
        self.write_serial(SerialCode.STOP_MEASUREMENT)
        self.timer.stop()
        self.switch_universal_button(ButtonCode.BACK)

    def tick_method(self):
        if self.is_measurement_finished():
            self.stop_measurement()
            self.initialize_slider()
        else:
            self.process_fresh_data()
            self.update_plot()
        self.calculate_and_update_label_values()

    def is_measurement_finished(self):
        return self.measurement_duration == self.get_elapsed_time()

    def get_elapsed_time(self):
        return int(len(self.ecg_data) / self.sampling_rate)

    def process_fresh_data(self):
        self.ydata = self.ydata[len(self.ecg_data[self.last_processed_index:]):] + self.ecg_data[
                                                                                   self.last_processed_index:]
        self.detect_r_peaks()
        self.last_processed_index = len(self.ecg_data)

    def detect_r_peaks(self):
        for i in range(self.last_processed_index, len(self.ecg_data) - 1):
            self.r_peak_interval_counter += 1
            if self.r_peak_detectable and self.ecg_data[i] > self.r_peak_upper_threshold \
                    and self.ecg_data[i] > self.ecg_data[i + 1]:
                self.r_peak_intervals.append(self.r_peak_interval_counter * self.sampling_time)
                self.r_peak_interval_counter = 0
                self.r_peak_detectable = False
            elif self.ecg_data[i] < self.r_peak_lower_threshold:
                self.r_peak_detectable = True

    def get_rr_interval(self):
        return round(statistics.fmean(self.r_peak_intervals) * pow(10, -3), 1)

    def get_bpm(self):
        return int((len(self.r_peak_intervals) + 1) / self.get_elapsed_time() * 60)

    def calculate_and_update_label_values(self):
        elapsed_time = self.get_elapsed_time()

        if self.is_measurement_in_progress[0]:
            self.countdown_label.setText(self.countdown_label_default_text + str(
                self.measurement_duration - elapsed_time) + " seconds")
        else:
            self.countdown_label.setText(self.countdown_label_default_text + "—")

        if not self.is_measurement_in_progress[0] or self.is_health_data_ready_for_calculation():
            self.last_time_health_data_calculated = elapsed_time
            try:
                self.rr_interval_label.setText(
                    self.rr_interval_label_default_text + str(self.get_rr_interval()) + " second")
                self.bpm_label.setText(self.bpm_label_default_text + str(self.get_bpm()) + " beats")
            except:
                self.stop_measurement()
                self.message_box.setWindowTitle("Signal processing error")
                self.message_box.setText("The measured signal does not meet the requirements of a real ecg signal.")
                self.message_box.exec_()

    def is_health_data_ready_for_calculation(self):
        return self.get_elapsed_time() - self.last_time_health_data_calculated == self.time_between_health_data_calculations

    def calculate_and_update_x_axis_values(self, max_displayed_data):
        self.canvas.axes.set_xticks([0, 500, 1000, 1500, 2000])
        # division by thousands is necessary to convert milliseconds to seconds
        self.canvas.axes.set_xticklabels(
            [int((max_displayed_data - 2000) * self.sampling_time / 1000),
             int((max_displayed_data - 1500) * self.sampling_time / 1000),
             int((max_displayed_data - 1000) * self.sampling_time / 1000),
             int((max_displayed_data - 500) * self.sampling_time / 1000),
             int(max_displayed_data * self.sampling_time / 1000)])

    def update_plot(self):
        if self.plot is None:
            plot_refs = self.canvas.axes.plot(self.xdata, self.ydata, 'r')
            self.plot = plot_refs[0]
        else:
            self.plot.set_ydata(self.ydata)

        self.canvas.draw()

    def write_serial(self, code):
        if self.ser[0]:
            self.ser[0].write(str(code).encode('ascii'))

    def initialize_slider(self):
        self.slider.setValue(self.slider_min_value)
        self.calculate_slider_jump_value()
        self.slider_action(0)
        self.slider.show()

    def calculate_slider_jump_value(self):
        ecg_data_max_from_index = len(self.ecg_data) - self.display_count
        self.slider_jump_value = ecg_data_max_from_index / self.slider_max_value

    def slider_action(self, value):
        display_data_from_index = int(value * self.slider_jump_value)
        if display_data_from_index + self.display_count > len(self.ecg_data):
            display_data_to_index = len(self.ecg_data)
            display_data_from_index = display_data_to_index - self.display_count
        else:
            display_data_to_index = display_data_from_index + self.display_count

        self.calculate_and_update_x_axis_values(display_data_to_index + 1)
        self.ydata = self.ecg_data[display_data_from_index:display_data_to_index]
        self.update_plot()


def read_serial(ser, ecg_data, is_measurement_in_progress):
    while True:
        if is_measurement_in_progress[0] and ser[0]:
            try:
                data = ser[0].readline().strip()
                if data:
                    ecg_data.append(int(data))
            except:
                print("Error reading from serial port at " + str(datetime.now()))
        else:
            time.sleep(0.5)


g_serial_port = ["COM3"]
g_baud_rate = [115200]
g_ser = [None]
g_ecg_data = []
g_is_measurement_in_progress = [False]

serial_read_thread = threading.Thread(target=read_serial,
                                      args=(g_ser, g_ecg_data, g_is_measurement_in_progress))
serial_read_thread.daemon = True
serial_read_thread.start()

app = QtWidgets.QApplication(sys.argv)
w = EcgWindow(g_serial_port, g_baud_rate, g_ser, g_ecg_data, g_is_measurement_in_progress)
app.exec_()
