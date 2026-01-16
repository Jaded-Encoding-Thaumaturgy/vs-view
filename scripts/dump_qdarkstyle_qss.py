import sys

import qdarkstyle
from PySide6.QtWidgets import QApplication
from qdarkstyle.dark.palette import DarkPalette
from qdarkstyle.light.palette import LightPalette

app = QApplication(sys.argv)

# Dump dark theme
dark_stylesheet = qdarkstyle.load_stylesheet(palette=DarkPalette)
with open("qdarkstyle_dark.qss", "w") as f:
    f.write(dark_stylesheet)
print("Saved: qdarkstyle_dark.qss")

# Dump light theme
light_stylesheet = qdarkstyle.load_stylesheet(palette=LightPalette)
with open("qdarkstyle_light.qss", "w") as f:
    f.write(light_stylesheet)
print("Saved: qdarkstyle_light.qss")
