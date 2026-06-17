import os
import sys
import logging
from dotenv import load_dotenv

# Ensure core and other relative module imports operate correctly when compiled via Nuitka
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# Logging Configuration – Console + File (with milliseconds)
# ---------------------------------------------------------------------------
_LOG_FORMAT = "%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"
_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "encomm_erp.log")

_logger = logging.getLogger()
_logger.setLevel(logging.INFO)

_formatter = logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT)

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_formatter)
_logger.addHandler(_console_handler)

_file_handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
_file_handler.setFormatter(_formatter)
_logger.addHandler(_file_handler)

from infrastructure.database_service import DatabaseService
from presentation.main_window import MainWindow

def main():
    # Load env file configurations
    load_dotenv()

    # Configure application thresholds with fallback default parameters
    config = {
        "db_path": os.getenv("DB_PATH", "pharmacy.db"),
        "vat_rate": float(os.getenv("VAT_RATE", "0.15")),
        "low_stock_threshold": int(os.getenv("LOW_STOCK_THRESHOLD", "10")),
        "expiry_alert_days": int(os.getenv("EXPIRY_ALERT_DAYS", "30")),
    }

    logging.info("=== ENCOMM ERP STARTED ===")
    logging.info(f"Database location: {config['db_path']}")
    logging.info(f"VAT rate configured: {config['vat_rate'] * 100}%")
    logging.info(f"Low Stock warning limit: {config['low_stock_threshold']} units")
    logging.info(f"Expiry date warning limit: {config['expiry_alert_days']} days")

    try:
        # 1. Initialize SQLite Database Infrastructure
        db_service = DatabaseService(db_path=config["db_path"])
        
        # 2. Instantiate MainWindow — renders empty window immediately via internal update_idletasks()
        app = MainWindow(db_service=db_service, config=config)
        app.update_idletasks()  # belt-and-suspenders: ensure window pixels are on screen
        
        # 3. Start the CustomTkinter GUI main event loop (UI builds inside callbacks)
        logging.info("Starting main GUI application window...")
        app.mainloop()
        
    except Exception as e:
        logging.critical(f"Fatal error starting application: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
