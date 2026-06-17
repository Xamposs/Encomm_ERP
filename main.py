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

    try:
        # 1. Initialize SQLite Database Infrastructure
        db_service = DatabaseService(db_path=config["db_path"])

        # 2. Hydrate config from DB (persisted values take priority over env defaults)
        #    Seed DB with env defaults on first run if no persisted values exist.
        db_config = db_service.get_all_config()
        if db_config:
            logging.info(f"Loaded {len(db_config)} config keys from database.")
        else:
            logging.info("No persisted config found — seeding database with env defaults.")
            db_service.bulk_set_config({
                "vat_rate": str(config["vat_rate"]),
                "low_stock_threshold": str(config["low_stock_threshold"]),
                "expiry_alert_days": str(config["expiry_alert_days"]),
            })
            db_config = db_service.get_all_config()

        # DB-prioritized config merge: DB value wins, env is fallback
        def _cfg_db_or_env(key: str, cast=float):
            db_val = db_config.get(key)
            if db_val is not None:
                try:
                    return cast(db_val)
                except (ValueError, TypeError):
                    logging.warning(f"Invalid DB value for '{key}': {db_val!r}, falling back to env default.")
            return config[key]

        config["vat_rate"] = _cfg_db_or_env("vat_rate", float)
        config["low_stock_threshold"] = _cfg_db_or_env("low_stock_threshold", int)
        config["expiry_alert_days"] = _cfg_db_or_env("expiry_alert_days", int)

        logging.info(f"Final config — VAT: {config['vat_rate']*100}%, "
                     f"LowStock: {config['low_stock_threshold']}, "
                     f"ExpiryAlert: {config['expiry_alert_days']}d")

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
