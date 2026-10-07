"""Desktop startup and local rotating application logs."""
from logging.handlers import RotatingFileHandler
import logging
from pathlib import Path
import sys
from PySide6.QtWidgets import QApplication, QMessageBox
from app.repositories.alias_repository import default_database_path
from app.ui.workspace_window import WorkspaceWindow


def run_gui(database_path: str | Path | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName('KPI KAM')
    app.setOrganizationName('KPI_KAM')
    database_path = Path(database_path) if database_path else default_database_path()
    handler = None
    try:
        log_dir = database_path.parent / 'logs'
        log_dir.mkdir(parents=True,exist_ok=True)
        handler = RotatingFileHandler(log_dir/'app.log',maxBytes=2_000_000,backupCount=3,encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)
        window = WorkspaceWindow(database_path)
    except Exception:
        logging.exception('Application startup failed')
        if handler is not None:
            logging.getLogger().removeHandler(handler)
            handler.close()
        QMessageBox.critical(None,'KPI KAM','Не удалось открыть настройки и историю. Проверьте доступ к папке данных.')
        return 2
    try:
        window.show()
        return app.exec()
    finally:
        window.close()
        logging.getLogger().removeHandler(handler)
        handler.close()
