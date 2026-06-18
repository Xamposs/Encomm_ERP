#!/usr/bin/env python3
"""Continuous automated test runner — polls test_suite.py and logs results."""
import os
import sys
import time
import subprocess
import logging
from datetime import datetime

# Project root (parent of tests/)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── Isolated logging engine (bound to automation.log only) ──
LOG_FILE = os.path.join(BASE_DIR, "tests", "automation.log")

logger = logging.getLogger("AutomationEngine")
logger.setLevel(logging.INFO)

_formatter = logging.Formatter(
    "%(asctime)s - [AUTOMATION ENGINE] - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

_file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
_file_handler.setFormatter(_formatter)
logger.addHandler(_file_handler)

# Prevent propagation to root logger (avoid double-logging if root has handlers)
logger.propagate = False

# ── Configuration ──
HEARTBEAT_SECONDS = int(os.getenv("TEST_HEARTBEAT", "300"))
SUITE_PATH = os.path.join(BASE_DIR, "tests", "test_suite.py")


def run_test_suite():
    """Execute pytest on the suite and return (exit_code, stdout, stderr)."""
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    venv_python = os.path.join(root_dir, ".venv", "Scripts", "python.exe")

    if os.path.exists(venv_python):
        pytest_cmd = [venv_python, "-m", "pytest", SUITE_PATH, "-v"]
    else:
        pytest_cmd = ["pytest", SUITE_PATH, "-v"]

    result = subprocess.run(
        pytest_cmd,
        capture_output=True,
        text=True,
        cwd=BASE_DIR,
        timeout=120
    )
    return result.returncode, result.stdout, result.stderr


def main():
    logger.info("=== Automation Engine Started ===")
    logger.info(f"Suite: {SUITE_PATH}")
    logger.info(f"Heartbeat: {HEARTBEAT_SECONDS}s")

    while True:
        try:
            print(f"🔄 [{datetime.now().strftime('%H:%M:%S')}] Launching automated health check loop...")
            exit_code, stdout, stderr = run_test_suite()

            if exit_code == 0:
                print(f"✅ [{datetime.now().strftime('%H:%M:%S')}] Health check: PASS! All core modules are operational.")
                logger.info("HEALTH CHECK: PASS. All systems operational.")
            else:
                print(f"❌ [{datetime.now().strftime('%H:%M:%S')}] Health check: FAIL! Code regression detected. Inspect 'tests/automation.log' for details.")
                logger.error("HEALTH CHECK: FAIL (exit code %d)", exit_code)
                logger.error("--- TEST FAILURE TRACEBACK ---")
                if stderr:
                    for line in stderr.strip().splitlines():
                        logger.error(line)
                if stdout:
                    for line in stdout.strip().splitlines():
                        logger.error(line)
                logger.error("--- END TRACEBACK ---")

        except subprocess.TimeoutExpired:
            print(f"⚠️ [{datetime.now().strftime('%H:%M:%S')}] Health check: TIMEOUT after 120s.")
            logger.error("HEALTH CHECK: TIMEOUT — test suite did not complete within 120s.")
        except Exception as e:
            print(f"⚠️ [{datetime.now().strftime('%H:%M:%S')}] Critical Runner Exception: {e}")
            logger.exception("HEALTH CHECK: CRASH — unhandled exception in runner loop.")

        time.sleep(HEARTBEAT_SECONDS)


if __name__ == "__main__":
    main()
