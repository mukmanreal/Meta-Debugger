import sys
import subprocess
import time
import shlex
import re
import ctypes

CONTROLLER_BUTTON_ALIASES = {
    'A': {'GAMEPAD', 'BTN_GAMEPAD', 'SOUTH', 'A_BUTTON', 'BTN_SOUTH'},
    'B': {'EAST', 'B_BUTTON', 'BTN_EAST'},
    'X': {'NORTH', 'Y_BUTTON', 'BTN_NORTH'},
    'Y': {'WEST', 'X_BUTTON', 'BTN_WEST'},
    'Left Grip': {'BTN_TL', 'KEY_LEFTSHIFT', 'LEFTSHIFT', 'GRIPLEFT', 'LEFT_GRIP'},
    'Right Grip': {'BTN_TR', 'KEY_RIGHTSHIFT', 'RIGHTSHIFT', 'GRIPRIGHT', 'RIGHT_GRIP'},
    'Left Trigger': {'ABS_Z', 'Z', 'BTN_TL2', 'TRIGGERLEFT', 'LEFT_TRIGGER'},
    'Right Trigger': {'ABS_RZ', 'RZ', 'BTN_TR2', 'TRIGGERRIGHT', 'RIGHT_TRIGGER'},
    'Left Menu': {'START', 'BTN_START', 'BTN_SELECT', 'SELECT', 'MENU_BUTTON', 'LEFT_MENU'},
    'Right Menu': {'FORWARD', 'KEY_FORWARD', 'RIGHT_MENU'},
    'System': {'MODE', 'BTN_MODE', 'HOME', 'KEY_HOME', 'SYSTEM'},
}
try:
	from PyQt6.QtWidgets import (
		QApplication,
		QWidget,
		QLabel,
		QVBoxLayout,
		QHBoxLayout,
		QPushButton,
		QPlainTextEdit,
		QLineEdit,
		QMessageBox,
		QScrollArea,
		QGridLayout,
		QSizePolicy,
		QFrame,
	)
	from PyQt6.QtGui import QFont, QTextDocument
	from PyQt6.QtCore import Qt, QThread, pyqtSignal
except Exception as e:
	print('Missing dependency: PyQt6')
	print()
	print('Install dependencies with:')
	print('  python -m pip install -r requirements.txt')
	print('Or install PyQt6 directly:')
	print('  python -m pip install PyQt6')
	print()
	print('If you use multiple Python versions, run the pip module from the same python:')
	print('  C:\\path\\to\\python.exe -m pip install PyQt6')
	sys.exit(1)


def get_devices():
	"""Return list of connected adb device ids (only those in 'device' state)."""
	try:
		out = subprocess.check_output(["adb", "devices"], stderr=subprocess.DEVNULL, text=True, timeout=5)
	except Exception:
		return []
	lines = out.splitlines()[1:]
	ids = []
	for l in lines:
		l = l.strip()
		if not l:
			continue
		parts = l.split()
		if len(parts) >= 2 and parts[1] == 'device':
			ids.append(parts[0])
	return ids


class AdbPoller(QThread):
	device_changed = pyqtSignal(bool, str)

	def __init__(self, interval=2.0):
		super().__init__()
		self.interval = interval
		self._running = True

	def run(self):
		prev = (False, '')
		while self._running:
			devices = get_devices()
			connected = len(devices) > 0
			device_id = devices[0] if connected else ''
			if (connected, device_id) != prev:
				self.device_changed.emit(connected, device_id)
				prev = (connected, device_id)
			time.sleep(self.interval)

	def stop(self):
		self._running = False
		self.wait()


class LogThread(QThread):
	log_line = pyqtSignal(str)

	def __init__(self, device_id=None):
		super().__init__()
		self.device_id = device_id
		self._running = True

	def run(self):
		args = ['adb']
		if self.device_id:
			args += ['-s', self.device_id]
		args += ['logcat', '-v', 'time']
		try:
			p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
		except Exception as e:
			self.log_line.emit(f'Failed to start logcat: {e}')
			return
		while self._running:
			line = p.stdout.readline()
			if not line:
				break
			self.log_line.emit(line.rstrip('\n'))
		try:
			p.terminate()
		except Exception:
			pass

	def stop(self):
		self._running = False
		self.wait()


class CommandThread(QThread):
	output_line = pyqtSignal(str)
	finished = pyqtSignal(int)

	def __init__(self, args, cwd=None, timeout=None):
		super().__init__()
		self.args = args
		self.cwd = cwd
		self.timeout = timeout
		self._running = True
		self._proc = None

	def run(self):
		try:
			# Start process and stream output
			self._proc = subprocess.Popen(self.args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=self.cwd)
		except Exception as e:
			self.output_line.emit(f'Failed to start: {e}')
			self.finished.emit(-1)
			return

		while self._running:
			line = self._proc.stdout.readline()
			if line:
				self.output_line.emit(line.rstrip('\n'))
			else:
				break

		try:
			rc = self._proc.wait(timeout=1)
		except Exception:
			rc = -1
		self.finished.emit(rc)

	def stop(self):
		self._running = False
		try:
			if self._proc:
				self._proc.terminate()
		except Exception:
			pass
		self.wait()


class ControllerMonitor(QThread):
	input_event = pyqtSignal(str)

	def __init__(self, device_id=None):
		super().__init__()
		self.device_id = device_id
		self._running = True
		self._proc = None

	def run(self):
		args = ['adb']
		if self.device_id:
			args += ['-s', self.device_id]
		args += ['shell', 'getevent', '-lt']
		try:
			self._proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
		except Exception as e:
			return
		while self._running:
			line = self._proc.stdout.readline()
			if not line:
				break
			self.input_event.emit(line.rstrip('\n'))

	def stop(self):
		self._running = False
		try:
			if self._proc:
				self._proc.terminate()
		except Exception:
			pass
		self.wait()


class MainWindow(QWidget):
	def __init__(self):
		super().__init__()
		self.setWindowTitle('Head Set Debugger')
		self.resize(1000, 700)
		self.device_id = ''
		self.current_cols = 1
		self.current_category = 'All Commands'
		self.controller_mappings = {}

		self.init_ui()
		self.poller = AdbPoller(interval=2.0)
		self.poller.device_changed.connect(self.on_device_changed)
		self.poller.start()

		self.log_thread = None

	def init_ui(self):
		layout = QVBoxLayout()

		header = QHBoxLayout()
		title = QLabel('Head Set Debugger')
		title.setObjectName('hero')
		title.setFont(QFont('Orbitron', 28))
		title.setStyleSheet('color: #ff5050; letter-spacing:2px;')
		header.addWidget(title, alignment=Qt.AlignmentFlag.AlignLeft)
		subtitle = QLabel('ADB command toolbox')
		subtitle.setStyleSheet('color: #e2b0b0; margin-left:12px;')
		header.addWidget(subtitle, alignment=Qt.AlignmentFlag.AlignLeft)

		self.status_circle = QLabel()
		self.status_circle.setObjectName('status_circle')
		self.status_circle.setFixedSize(24, 24)
		self.status_circle.setStyleSheet('border-radius:12px;')
		header.addWidget(self.status_circle, alignment=Qt.AlignmentFlag.AlignRight)

		self.device_label = QLabel('No device')
		self.device_label.setStyleSheet('color: #cfcfcf;')
		header.addWidget(self.device_label, alignment=Qt.AlignmentFlag.AlignRight)

		layout.addLayout(header)

		mid = QHBoxLayout()

		left_col = QVBoxLayout()
		# Log search controls
		search_row = QHBoxLayout()
		self.log_search_input = QLineEdit()
		self.log_search_input.setPlaceholderText('Search logs...')
		search_row.addWidget(self.log_search_input)
		self.find_next_btn = QPushButton('Next')
		self.find_next_btn.clicked.connect(self.find_next)
		search_row.addWidget(self.find_next_btn)
		self.find_prev_btn = QPushButton('Prev')
		self.find_prev_btn.clicked.connect(self.find_prev)
		search_row.addWidget(self.find_prev_btn)
		self.clear_search_btn = QPushButton('Clear')
		self.clear_search_btn.clicked.connect(self.clear_log_search)
		search_row.addWidget(self.clear_search_btn)
		left_col.addLayout(search_row)

		self.log_view = QPlainTextEdit()
		self.log_view.setReadOnly(True)
		# make the log view larger and expandable
		self.log_view.setMinimumHeight(450)
		self.log_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
		left_col.addWidget(self.log_view, 1)

		log_buttons = QHBoxLayout()
		self.start_log_btn = QPushButton('Start Logs')
		self.start_log_btn.clicked.connect(self.toggle_logs)
		log_buttons.addWidget(self.start_log_btn)

		self.clear_log_btn = QPushButton('Clear')
		self.clear_log_btn.clicked.connect(self.log_view.clear)
		log_buttons.addWidget(self.clear_log_btn)

		left_col.addLayout(log_buttons)

		# left_col gets slightly less width than commands area
		mid.addLayout(left_col, 2)

		# Right column: quick commands + command runner + output
		right_col = QVBoxLayout()
		# Quick commands panel: categories and commands
		quick_panel = QHBoxLayout()
		self.category_list = QLineEdit()
		# we'll use a simple QList-like layout via buttons for categories
		self.cat_layout = QVBoxLayout()
		# preset categories
		self.categories = ['All Commands', 'Device Info', 'App Management', 'Key Mapping', 'Display & Performance']
		for c in self.categories:
			btn = QPushButton(c)
			btn.setFixedHeight(28)
			btn.clicked.connect(lambda checked, cat=c: self.show_commands_for(cat))
			self.cat_layout.addWidget(btn)
		quick_panel.addLayout(self.cat_layout, 1)

		# commands area (scrollable)
		from PyQt6.QtWidgets import QScrollArea, QWidget
		self.commands_area_widget = QWidget()
		self.commands_area_layout = QGridLayout()
		self.commands_area_layout.setSpacing(12)
		self.commands_area_widget.setLayout(self.commands_area_layout)
		self.commands_scroll = QScrollArea()
		self.commands_scroll.setWidgetResizable(True)
		self.commands_scroll.setWidget(self.commands_area_widget)
		# disable horizontal scrolling to avoid side-scroll; cards will wrap text instead
		self.commands_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
		quick_panel.addWidget(self.commands_scroll, 3)

		right_col.addLayout(quick_panel)

		# command search for quick commands
		self.cmd_search = QLineEdit()
		self.cmd_search.setPlaceholderText('Filter commands...')
		self.cmd_search.textChanged.connect(self.filter_commands)
		right_col.addWidget(self.cmd_search)

		# store default quick commands (expanded categories)
		self.default_commands = {
			'All Commands': [],
			'Device Info': [
				('List connected devices', 'devices'),
				('Device properties', 'shell getprop'),
				('Android version', 'shell getprop ro.build.version.release'),
				('Device model', 'shell getprop ro.product.model'),
				('Battery status', 'shell dumpsys battery'),
				('Uptime', 'shell uptime'),
				('Build fingerprint', 'shell getprop ro.build.fingerprint'),
				('Kernel version', 'shell uname -a'),
				('Thermal info (root)', 'shell cat /sys/class/thermal/thermal_zone0/temp'),
			],
			'Display & Performance': [
				('Screenshot (capture+pull)', 'shell screencap -p /sdcard/screenshot.png && pull /sdcard/screenshot.png'),
				('Screen record', 'shell screenrecord /sdcard/record.mp4'),
				('Set refresh rate 90Hz', 'shell settings put system peak_refresh_rate 90'),
				('Set refresh rate 120Hz', 'shell settings put system peak_refresh_rate 120'),
				('GPU performance mode', 'shell setprop debug.oculus.gpuLevel 4'),
				('CPU performance mode', 'shell setprop debug.oculus.cpuLevel 4'),
				('Brightness control', 'shell settings put system screen_brightness 200'),
				('Disable screen timeout', 'shell settings put system screen_off_timeout 2147483647'),
				('Enable 30s timeout', 'shell settings put system screen_off_timeout 30000'),
				('Show current display info', 'shell dumpsys display'),
			],
			'App Management': [
				('List packages', 'shell pm list packages'),
				('List enabled apps', 'shell pm list packages -e'),
				('List disabled apps', 'shell pm list packages -d'),
				('Clear app data', 'shell pm clear <package>'),
				('Install APK', 'install <path_to_apk>'),
				('Uninstall package', 'uninstall <package>'),
			],
			'Key Mapping': [
				('Dump input devices', 'shell getevent -p'),
				('Monitor input events', 'shell getevent'),
			],
		}
		# Flatten into All Commands
		for v in self.default_commands.values():
			self.default_commands['All Commands'].extend(v)
		# show initial category
		self.current_command_buttons = []
		# determine initial columns based on width
		self.current_cols = 2 if self.width() > 1100 else 1
		self.current_category = 'All Commands'
		self.load_controller_mappings()
		self.show_commands_for(self.current_category)
		cmd_label = QLabel('ADB Command')
		cmd_label.setStyleSheet('color: #cfcfcf;')
		right_col.addWidget(cmd_label)

		self.cmd_input = QLineEdit()
		self.cmd_input.setPlaceholderText('e.g. shell getprop ro.product.model')
		right_col.addWidget(self.cmd_input)

		self.run_cmd_btn = QPushButton('Run')
		self.run_cmd_btn.clicked.connect(self.run_cmd)
		right_col.addWidget(self.run_cmd_btn)

		self.stop_cmd_btn = QPushButton('Stop')
		self.stop_cmd_btn.setEnabled(False)
		self.stop_cmd_btn.clicked.connect(self.stop_current_command)
		right_col.addWidget(self.stop_cmd_btn)

		self.cmd_output = QPlainTextEdit()
		self.cmd_output.setReadOnly(True)
		right_col.addWidget(self.cmd_output, 1)

		# Wireless connect controls
		wifi_row = QHBoxLayout()
		self.wifi_ip = QLineEdit()
		self.wifi_ip.setPlaceholderText('Headset IP (e.g. 192.168.1.20)')
		self.wifi_ip.setStyleSheet('min-width: 200px;')
		self.connect_wifi_btn = QPushButton('Connect Wirelessly')
		self.connect_wifi_btn.clicked.connect(self.connect_wireless_device)
		wifi_row.addWidget(self.wifi_ip)
		wifi_row.addWidget(self.connect_wifi_btn)
		right_col.addLayout(wifi_row)

		# Controller Inputs panel
		ctrl_frame = QFrame()
		ctrl_frame.setObjectName('panel')
		ctrl_layout = QVBoxLayout()
		ctrl_frame.setLayout(ctrl_layout)
		ctrl_title = QLabel('Controller Inputs')
		ctrl_title.setStyleSheet('color:#ff8b8b; font-weight:700;')
		ctrl_layout.addWidget(ctrl_title)
		# grid of controller buttons with status labels
		self.controller_states = {}
		self.controller_widgets = {}
		controller_groups = {
			'Left': ['X', 'Y', 'Left Grip', 'Left Trigger', 'Left Menu'],
			'Right': ['A', 'B', 'Right Grip', 'Right Trigger', 'Right Menu'],
		}
		for side_name, button_names in controller_groups.items():
			side_label = QLabel(side_name)
			side_label.setStyleSheet('color:#ff9fa8; font-weight:600; margin-top:8px;')
			ctrl_layout.addWidget(side_label)
			side_grid = QGridLayout()
			for i, name in enumerate(button_names):
				btn = QPushButton(name)
				btn.setCheckable(True)
				status = QLabel('Inactive')
				status.setStyleSheet('color:#d1d1d1;')
				btn.clicked.connect(lambda checked, n=name, s=status: self.toggle_controller(n, s, checked))
				mapbtn = QPushButton('Map')
				mapbtn.setFixedWidth(44)
				mapbtn.clicked.connect(lambda checked, n=name: self.map_controller_button(n))
				mapped = self.controller_mappings.get(name)
				if mapped:
					mapbtn.setToolTip(mapped)
				row = i // 2
				col = (i % 2) * 3
				side_grid.addWidget(btn, row, col)
				side_grid.addWidget(status, row, col + 1)
				side_grid.addWidget(mapbtn, row, col + 2)
				self.controller_states[name] = False
				self.controller_widgets[name] = (btn, status, mapbtn)
			ctrl_layout.addLayout(side_grid)
		# update map button tooltips from loaded mappings
		for name, mapped in self.controller_mappings.items():
			widgets = getattr(self, 'controller_widgets', {}).get(name)
			if widgets:
				btn, status, mapbtn = widgets
				mapbtn.setToolTip(mapped)
		# controller monitor controls
		self.ctrl_monitor = None
		mon_row = QHBoxLayout()
		self.start_monitor_btn = QPushButton('Start Controller Monitor')
		self.start_monitor_btn.clicked.connect(self.toggle_controller_monitor)
		mon_row.addWidget(self.start_monitor_btn)
		ctrl_layout.addLayout(mon_row)
		right_col.addWidget(ctrl_frame)

		mid.addLayout(right_col, 3)

		# Put the main mid content into a central widget and make it scrollable so smaller windows still show everything
		central_widget = QWidget()
		central_layout = QVBoxLayout()
		central_layout.addLayout(mid)
		central_widget.setLayout(central_layout)
		self.scroll = QScrollArea()
		self.scroll.setWidgetResizable(True)
		# avoid horizontal scrolling of the main content
		self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
		self.scroll.setWidget(central_widget)
		layout.addWidget(self.scroll)

		footer = QHBoxLayout()
		self.status_label = QLabel('Ready')
		self.status_label.setStyleSheet('color: #9aa0a6;')
		footer.addWidget(self.status_label)
		layout.addLayout(footer)

		self.setLayout(layout)
		self.apply_styles()

	def apply_styles(self):
		self.setStyleSheet('''
			QWidget { background: qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:1, stop:0 #080606, stop:1 #13080a); font-family: "Orbitron", Arial, sans-serif; }
			QLabel { color: #e6e6e6; }
			QLabel#hero { color: #ff6b6b; font-weight: 800; }
			QPlainTextEdit, QLineEdit { background: #071010; color: #8cffb9; border: 1px solid rgba(255,0,0,0.06); border-radius:6px; font-family: Consolas, "Courier New", monospace; }
			QPlainTextEdit { padding: 8px; }
			QLineEdit { padding: 6px; }
			QPushButton { background: rgba(20,8,8,0.7); color: #ffdddd; padding: 8px; border-radius: 6px; border: 1px solid rgba(255,0,0,0.1); }
			QPushButton:hover { background: rgba(40,12,12,0.9); }
			QWidget[status="connected"] QLabel#status_circle { background: #00ff66; border: 2px solid rgba(0,255,102,0.35); }
			QWidget[status="disconnected"] QLabel#status_circle { background: #ff0044; border: 2px solid rgba(255,0,68,0.35); }
			QFrame#panel { border: 1px solid #3e0505; background: linear-gradient(90deg, rgba(20,6,6,0.6), rgba(10,4,4,0.4)); border-radius:8px; padding:10px; }
			QFrame#panel QLabel { margin-bottom:6px; }
			QPushButton { min-width: 64px; }
			QPushButton#copy { background: transparent; border: 1px solid rgba(255,20,20,0.18); color: #ff9f9f; padding:4px; }
			QPushButton#run { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #c92a2a, stop:1 #7b0b0b); color: #fff; }
			/* Neon-like glow using border and color accents */
			QFrame#panel:hover { border-color: #ff3b3b; }
		''')

	def closeEvent(self, event):
		if self.poller:
			self.poller.stop()
		if self.log_thread:
			try:
				self.log_thread.stop()
			except Exception:
				pass
		event.accept()

	def on_device_changed(self, connected: bool, device_id: str):
		self.device_id = device_id
		if connected:
			self.setProperty('status', 'connected')
			self.status_circle.setStyleSheet('border-radius:12px;')
			self.device_label.setText(device_id)
			self.status_label.setText('Device connected')
		else:
			self.setProperty('status', 'disconnected')
			self.status_circle.setStyleSheet('border-radius:12px;')
			self.device_label.setText('No device')
			self.status_label.setText('No device')
		# force stylesheet refresh to pick up property change
		self.style().unpolish(self)
		self.style().polish(self)

	def toggle_logs(self):
		if self.log_thread and self.log_thread.isRunning():
			self.log_thread.stop()
			self.log_thread = None
			self.start_log_btn.setText('Start Logs')
			self.status_label.setText('Logs stopped')
			return

		if not self.device_id:
			QMessageBox.warning(self, 'No device', 'No ADB device connected.')
			return

		self.log_thread = LogThread(device_id=self.device_id)
		self.log_thread.log_line.connect(self.append_log)
		self.log_thread.start()
		self.start_log_btn.setText('Stop Logs')
		self.status_label.setText('Logging...')

	def append_log(self, line: str):
		self.log_view.appendPlainText(line)

	def clear_commands_area(self):
		# remove widgets from the grid layout cleanly
		layout = self.commands_area_layout
		while layout.count():
			item = layout.takeAt(0)
			w = item.widget()
			if w:
				w.deleteLater()
		self.current_command_buttons = []

	def load_controller_mappings(self):
		# load mappings and states from workspace files if present
		import json, os
		base = '.'
		mfile = os.path.join(base, 'controller_mappings.json')
		statefile = os.path.join(base, 'controller_states.json')
		try:
			if os.path.exists(mfile):
				with open(mfile, 'r', encoding='utf-8') as f:
					self.controller_mappings = json.load(f)
				if os.path.exists(statefile):
					with open(statefile, 'r', encoding='utf-8') as f:
						states = json.load(f)
						# update internal states
						for name, val in states.items():
							self.controller_states[name] = val
						# if UI exists, update widgets
						if hasattr(self, 'controller_widgets'):
							for name, val in states.items():
								btn, status = self.controller_widgets.get(name, (None, None))[:2]
								if status:
									status.setText('Active' if val else 'Inactive')
									status.setStyleSheet('color:#00ff88;' if val else 'color:#d1d1d1;')
		except Exception:
			self.controller_mappings = {}

	def save_controller_mappings(self):
		import json, os
		base = '.'
		mfile = os.path.join(base, 'controller_mappings.json')
		statefile = os.path.join(base, 'controller_states.json')
		try:
			with open(mfile, 'w', encoding='utf-8') as f:
				json.dump(self.controller_mappings, f, indent=2)
			with open(statefile, 'w', encoding='utf-8') as f:
				json.dump(self.controller_states, f, indent=2)
		except Exception:
			pass

	def show_commands_for(self, category: str):
		self.clear_commands_area()
		cmds = self.default_commands.get(category, [])
		# if 'All Commands' requested, include all
		if category == 'All Commands':
			cmds = []
			for v in self.default_commands.values():
				cmds.extend(v)
		# add buttons in a grid (cols based on current_cols)
		cols = getattr(self, 'current_cols', 1)
		r = 0
		c = 0
		for idx, (label, cmd) in enumerate(cmds):
			# ensure cmd text doesn't overflow and wrap
			card = QFrame()
			card.setObjectName('panel')
			card.setStyleSheet('padding:8px;')
			card_layout = QVBoxLayout()
			card.setLayout(card_layout)
			title = QLabel(label)
			title.setStyleSheet('color:#ff8b8b; font-weight:600;')
			cmdlabel = QLabel('$ ' + cmd)
			cmdlabel.setStyleSheet('color:#8cffb9; font-family: Consolas, "Courier New", monospace;')
			cmdlabel.setWordWrap(True)
			cmdlabel.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
			runbtn = QPushButton('Run')
			runbtn.clicked.connect(lambda checked, c=cmd: self.run_quick_command(c))
			runbtn.setObjectName('run')
			copybtn = QPushButton('Copy')
			copybtn.setObjectName('copy')
			copybtn.clicked.connect(lambda checked, c=cmd: QApplication.clipboard().setText(c))
			btnrow = QHBoxLayout()
			btnrow.addWidget(copybtn)
			btnrow.addStretch()
			btnrow.addWidget(runbtn)
			card_layout.addWidget(title)
			card_layout.addWidget(cmdlabel)
			card_layout.addLayout(btnrow)
			# make the card stretch horizontally
			self.commands_area_layout.addWidget(card, r, c)
			self.current_command_buttons.append(card)
			c += 1
			if c >= cols:
				c = 0
				r += 1

	def _discover_headset_ip(self):
		# Prefer auto-discovery from the device itself. Some Android devices expose the
		# active IP on wlan0/eth0, and we parse it from adb shell output.
		if self.device_id:
			adb_args = ['adb']
			if self.device_id:
				adb_args += ['-s', self.device_id]
			for shell_cmd in [
				"ip -f inet addr show wlan0 2>/dev/null || ip -f inet addr show eth0 2>/dev/null || ip route 2>/dev/null",
				"cat /proc/net/route 2>/dev/null",
			]:
				try:
					result = subprocess.run(adb_args + ['shell', shell_cmd], capture_output=True, text=True, timeout=10)
					out = (result.stdout or '') + (result.stderr or '')
					matches = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", out)
					if matches:
						for m in matches:
							parts = [int(p) for p in m.split('.')]
							if all(0 <= p <= 255 for p in parts):
								# Skip common non-device addresses.
								if m.startswith('127.') or m.startswith('0.'):
									continue
								return m
				except Exception:
					pass

		# fallback to manual box if user typed it in
		manual = self.wifi_ip.text().strip()
		if manual:
			return manual
		return ''

	def connect_wireless_device(self):
		ip = self._discover_headset_ip()
		if not ip:
			QMessageBox.warning(self, 'Missing IP', 'Could not detect the headset IP automatically. Enter it in the IP box first.')
			return

		self.wifi_ip.setText(ip)
		self.status_label.setText('Connecting wirelessly...')
		self.cmd_output.appendPlainText(f'Connecting to {ip}:5555...')

		try:
			subprocess.run(['adb', 'tcpip', '5555'], check=False, capture_output=True, text=True)
			time.sleep(1)
			result = subprocess.run(['adb', 'connect', f'{ip}:5555'], capture_output=True, text=True)
			output = (result.stdout or '') + (result.stderr or '')
			self.cmd_output.appendPlainText(output.strip() or 'No output.')
			if 'connected' in output.lower():
				self.status_label.setText('Wireless device connected')
				self.device_label.setText(f'{ip}:5555')
				self.device_id = f'{ip}:5555'
			else:
				self.status_label.setText('Wireless connect failed')
				QMessageBox.warning(self, 'Wireless connect failed', output or 'Could not connect to the device.')
		except Exception as e:
			self.status_label.setText('Wireless connect error')
			QMessageBox.critical(self, 'Error', str(e))

	def run_quick_command(self, cmd: str):
		# set and run; cmd here is the part after adb, e.g. 'shell getprop ...'
		self.cmd_input.setText(cmd)
		# run asynchronously
		self.run_cmd()

	def resizeEvent(self, event):
		# adjust columns for responsive layout
		width = self.width()
		cols = 2 if width > 1100 else 1
		if cols != getattr(self, 'current_cols', None):
			self.current_cols = cols
			# rebuild current category view with new cols
			self.show_commands_for(self.current_category)
		super().resizeEvent(event)

	def filter_commands(self, text: str):
		text = text.lower().strip()
		for btn in getattr(self, 'current_command_buttons', []):
			visible = True
			if text:
				visible = text in btn.text().lower()
			btn.setVisible(visible)

	def find_next(self):
		q = self.log_search_input.text()
		if not q:
			return
		# use QPlainTextEdit.find
		from PyQt6.QtGui import QTextDocument
		self.log_view.find(q)

	def find_prev(self):
		q = self.log_search_input.text()
		if not q:
			return
		from PyQt6.QtGui import QTextDocument
		# Find backward
		try:
			self.log_view.find(q, QTextDocument.FindFlag.FindBackward)
		except Exception:
			# fallback: no backward flag available
			self.log_view.find(q)

	def clear_log_search(self):
		self.log_search_input.clear()
		# move cursor to end
		cursor = self.log_view.textCursor()
		cursor.movePosition(cursor.MoveOperation.End)
		self.log_view.setTextCursor(cursor)

	def append_cmd_output(self, line: str):
		# append output from background command thread
		self.cmd_output.appendPlainText(line)

	def on_cmd_finished(self, rc: int):
		self.run_cmd_btn.setEnabled(True)
		self.stop_cmd_btn.setEnabled(False)
		if rc == 0:
			self.status_label.setText('Command finished')
		else:
			self.status_label.setText(f'Command finished (rc={rc})')
		self._cmd_thread = None

	def stop_current_command(self):
		if getattr(self, '_cmd_thread', None):
			self._cmd_thread.stop()
			self.status_label.setText('Stopping...')

	def map_controller_button(self, name: str):
		# show input dialog to assign a mapped adb command (after adb)
		from PyQt6.QtWidgets import QInputDialog
		text, ok = QInputDialog.getText(self, f'Map {name}', 'Enter adb subcommand (e.g. shell input keyevent 4):')
		if not ok:
			return
		cmd = text.strip()
		if cmd:
			self.controller_mappings[name] = cmd
			self.save_controller_mappings()
			# update tooltip if widget exists
			widgets = getattr(self, 'controller_widgets', {}).get(name)
			if widgets:
				btn, status, mapbtn = widgets
				mapbtn.setToolTip(cmd)
			QMessageBox.information(self, 'Mapped', f'{name} -> {cmd}')

	def toggle_controller(self, name: str, status_label: QLabel, checked: bool):
		# Toggle controller button state
		self.controller_states[name] = bool(checked)
		if checked:
			status_label.setText('Active')
			status_label.setStyleSheet('color:#00ff88;')
		else:
			status_label.setText('Inactive')
			status_label.setStyleSheet('color:#d1d1d1;')
		# save state
		self.save_controller_mappings()
		# if there's a mapped command for this button and toggled active, run it
		mapped = self.controller_mappings.get(name)
		if mapped and checked:
			self.run_quick_command(mapped)

	def toggle_controller_monitor(self):
		if getattr(self, 'ctrl_monitor', None):
			# stop
			try:
				self.ctrl_monitor.stop()
			except Exception:
				pass
			self.ctrl_monitor = None
			self.start_monitor_btn.setText('Start Controller Monitor')
			return
		# start monitor
		self.ctrl_monitor = ControllerMonitor(device_id=self.device_id)
		self.ctrl_monitor.input_event.connect(self.on_controller_event)
		self.ctrl_monitor.start()
		self.start_monitor_btn.setText('Stop Controller Monitor')

	def on_controller_event(self, line: str):
		line_upper = line.upper().strip()
		if not line_upper:
			return

		tokens = set(re.findall(r"\b[A-Z0-9_]+\b", line_upper))
		last_value = ''
		for token in re.findall(r"\b[0-9A-F]+\b", line_upper):
			last_value = token
		is_pressed = False
		if 'DOWN' in line_upper or 'PRESS' in line_upper:
			is_pressed = True
		elif 'UP' in line_upper or 'RELEASE' in line_upper:
			is_pressed = False
		elif last_value:
			try:
				is_pressed = int(last_value, 16) > 0
			except ValueError:
				is_pressed = False

		matched_names = []
		normalized_tokens = {re.sub(r'^(BTN_|KEY_|ABS_|REL_|EV_)', '', t) for t in tokens}

		# Trigger events are EV_ABS only. Ignore grip/menu completely on those lines.
		if 'EV_ABS' in line_upper:
			if 'ABS_Z' in tokens:
				matched_names.append('Left Trigger')
			if 'ABS_RZ' in tokens:
				matched_names.append('Right Trigger')
		else:
			# Explicit grip/menu checks for key events.
			if 'BTN_TL' in tokens or 'KEY_LEFTSHIFT' in tokens:
				matched_names.append('Left Grip')
			if 'BTN_TR' in tokens or 'KEY_RIGHTSHIFT' in tokens:
				matched_names.append('Right Grip')
			if 'BTN_START' in tokens or 'BTN_SELECT' in tokens:
				matched_names.append('Left Menu')
			if 'KEY_FORWARD' in tokens or 'BTN_MODE' in tokens:
				matched_names.append('Right Menu')

			# Generic button mapping for A/B/X/Y and other key-based inputs.
			for name in list(self.controller_states.keys()):
				if name in {'Left Trigger', 'Right Trigger', 'Left Grip', 'Right Grip', 'Left Menu', 'Right Menu'}:
					continue
				aliases = CONTROLLER_BUTTON_ALIASES.get(name, set())
				if not aliases:
					continue
				for alias in aliases:
					clean = re.sub(r'^(BTN_|KEY_|ABS_|REL_|EV_)', '', alias.upper())
					if clean in normalized_tokens:
						matched_names.append(name)
						break

		# Remove duplicates while keeping order.
		seen = set()
		unique_names = []
		for name in matched_names:
			if name in seen:
				continue
			seen.add(name)
			unique_names.append(name)

		for name in unique_names:
			widgets = self.controller_widgets.get(name)
			if not widgets:
				continue
			btn, status, mapbtn = widgets
			btn.setChecked(is_pressed)
			status.setText('Active' if is_pressed else 'Inactive')
			status.setStyleSheet('color:#00ff88;' if is_pressed else 'color:#d1d1d1;')
			self.controller_states[name] = is_pressed
			if is_pressed:
				mapped = self.controller_mappings.get(name)
				if mapped:
					self.run_quick_command(mapped)

		if hasattr(self, 'log_view'):
			self.log_view.appendPlainText(f"CTRL: {line} -> {unique_names or 'none'} -> {'pressed' if is_pressed else 'released'}")

	def _deactivate_controller(self, name, status_label, btn):
		# set inactive
		btn.setChecked(False)
		status_label.setText('Inactive')
		status_label.setStyleSheet('color:#d1d1d1;')
		self.controller_states[name] = False
		self.save_controller_mappings()

	def run_cmd(self):
		cmd_text = self.cmd_input.text().strip()
		if not cmd_text:
			return
		# sanitize input: users may type 'adb shell ...' — remove leading 'adb' if present
		try:
			parts = shlex.split(cmd_text)
		except Exception:
			parts = cmd_text.split()
		if parts and parts[0].lower() in ('adb', 'adb.exe'):
			parts = parts[1:]
		if not parts:
			return
		args = ['adb']
		if self.device_id:
			args += ['-s', self.device_id]
		args += parts
		# start background command thread to avoid blocking UI
		self.cmd_output.clear()
		self.status_label.setText('Running...')
		self.run_cmd_btn.setEnabled(False)
		self.stop_cmd_btn.setEnabled(True)
		self._cmd_thread = CommandThread(args)
		self._cmd_thread.output_line.connect(self.append_cmd_output)
		self._cmd_thread.finished.connect(self.on_cmd_finished)
		self._cmd_thread.start()



def ensure_single_instance():
	try:
		mutex = ctypes.windll.kernel32.CreateMutexW(None, False, 'HeadSetDebuggerSingleInstance')
		last_error = ctypes.windll.kernel32.GetLastError()
		if last_error == 183:  # ERROR_ALREADY_EXISTS
			return False
		return True
	except Exception:
		return True


def main():
	if not ensure_single_instance():
		return
	app = QApplication(sys.argv)
	w = MainWindow()
	w.show()
	sys.exit(app.exec())


if __name__ == '__main__':
	main()