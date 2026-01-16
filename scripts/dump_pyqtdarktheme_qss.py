import sys

import qdarktheme
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)

# Dump dark theme
dark_stylesheet = qdarktheme.load_stylesheet("dark")
with open("pyqtdarktheme_dark.qss", "w") as f:
    f.write(dark_stylesheet)
print("Saved: pyqtdarktheme_dark.qss")

# Dump light theme
light_stylesheet = qdarktheme.load_stylesheet("light")
with open("pyqtdarktheme_light.qss", "w") as f:
    f.write(light_stylesheet)
print("Saved: pyqtdarktheme_light.qss")
