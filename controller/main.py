"""
SNMP Simulator Controller — application entry point.

Run with:
    python -m controller.main

Or with elevated privileges to enable IP alias management:
    sudo python -m controller.main
"""
import logging
import sys

from PySide6.QtWidgets import QApplication

from controller.gui.main_window import MainWindow


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    app = QApplication(sys.argv)
    app.setApplicationName("SNMP Simulator")
    app.setOrganizationName("WorkSIMs")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
