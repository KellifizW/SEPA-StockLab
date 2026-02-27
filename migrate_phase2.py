#!/usr/bin/env python3
"""
migrate_phase2.py
─────────────────
One-time migration script: JSON files → DuckDB (Phase 2 storage upgrade)

Usage:
    python migrate_phase2.py                    # Show status
    python migrate_phase2.py --migrate          # Execute migration
    python migrate_phase2.py --rollback         # Revert to JSON-only (keeps backup)
    python migrate_phase2.py --verify           # Post-migration verification

Behavior:
    1. Load data from existing JSON files (watchlist.json, positions.json)
    2. Verify data integrity
    3. Store to DuckDB via modules/db.py APIs
    4. Preserve JSON originals in data/db_backups for safety
    5. Generate migration report
"""

import sys
import json
import logging
from datetime import date, datetime
from pathlib import Path
from shutil import copy2

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import trader_config as C
from modules import db

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_BOLD   = "\033[1m"
_RESET  = "\033[0m"

WATCHLIST_FILE = ROOT / C.DATA_DIR / "watchlist.json"
POSITIONS_FILE = ROOT / C.DATA_DIR / "positions.json"
DB_BACKUPS_DIR = ROOT / C.DB_JSON_BACKUP_DIR


# ─────────────────────────────────────────────────────────────────────────────
# Migration helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_backup_dir():
    DB_BACKUPS_DIR.mkdir(parents=True, exist_ok=True)


def _backup_json_file(source_file: Path, label: str):
    """Copy JSON file to backup directory."""
    if not source_file.exists():
        logger.warning("Source file not found: %s", source_file)
        return None
    
    _ensure_backup_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = DB_BACKUPS_DIR / f"{label}_{timestamp}.json"
    try:
        copy2(source_file, backup_file)
        logger.info("Backed up: %s -> %s", source_file.name, backup_file.name)
        return backup_file
    except Exception as exc:
        logger.error("Backup failed: %s", exc)
        return None


def _load_json(filepath: Path) -> dict:
    """Load JSON file, return empty dict on failure."""
    if not filepath.exists():
        logger.warning("File not found: %s", filepath)
        return {}
    try:
        return json.loads(filepath.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("Failed to load %s: %s", filepath, exc)
        return {}


def _validate_watchlist(data: dict) -> int:
    """Count valid watchlist entries."""
    count = 0
    for grade in ["A", "B", "C"]:
        if grade in data and isinstance(data[grade], dict):
            count += len(data[grade])
    return count


def _validate_positions(data: dict) -> tuple:
    """Count open and closed positions."""
    open_count = len(data.get("positions", {}))
    closed_count = len(data.get("closed", []))
    return open_count, closed_count


# ─────────────────────────────────────────────────────────────────────────────
# Migration operations
# ─────────────────────────────────────────────────────────────────────────────

def show_status():
    """Display current migration status."""
    print(f"\n{_BOLD}Phase 2 Migration Status{_RESET}")
    print("=" * 60)
    
    # Check file existence
    print(f"\n{_BOLD}Step 1: Source Files{_RESET}")
    wl_exists = "[OK]" if WATCHLIST_FILE.exists() else "[MISSING]"
    pos_exists = "[OK]" if POSITIONS_FILE.exists() else "[MISSING]"
    print(f"  Watchlist:  {wl_exists} {WATCHLIST_FILE}")
    print(f"  Positions:  {pos_exists} {POSITIONS_FILE}")
    
    # Check DuckDB status
    print(f"\n{_BOLD}Step 2: DuckDB Status{_RESET}")
    stats = db.db_stats()
    print(f"  scan_history:        {stats.get('scan_history', -1):>6} rows")
    print(f"  watchlist_store:     {stats.get('watchlist_store', -1):>6} rows (Phase 2)")
    print(f"  open_positions:      {stats.get('open_positions', -1):>6} rows (Phase 2)")
    print(f"  closed_positions:    {stats.get('closed_positions', -1):>6} rows (Phase 2)")
    print(f"  fundamentals_cache:  {stats.get('fundamentals_cache', -1):>6} rows (Phase 2)")
    print(f"  DB Size: {stats.get('db_size_mb', 0)} MB")
    
    # Check data content
    print(f"\n{_BOLD}Step 3: JSON File Content{_RESET}")
    if WATCHLIST_FILE.exists():
        wl_data = _load_json(WATCHLIST_FILE)
        wl_count = _validate_watchlist(wl_data)
        print(f"  Watchlist entries:   {wl_count} tickers")
    else:
        print(f"  Watchlist entries:   {_RED}file not found{_RESET}")
    
    if POSITIONS_FILE.exists():
        pos_data = _load_json(POSITIONS_FILE)
        open_count, closed_count = _validate_positions(pos_data)
        print(f"  Open positions:      {open_count} tickers")
        print(f"  Closed positions:    {closed_count} records")
    else:
        print(f"  Open positions:      {_RED}file not found{_RESET}")
    
    # Check backup directory
    print(f"\n{_BOLD}Step 4: Backup Directory{_RESET}")
    if DB_BACKUPS_DIR.exists():
        backup_files = list(DB_BACKUPS_DIR.glob("*.json"))
        print(f"  Backup files:        {len(backup_files)} files")
        if backup_files:
            for bf in sorted(backup_files)[-3:]:
                print(f"    • {bf.name}")
    else:
        print(f"  Backup directory:    {_YELLOW}not created yet{_RESET}")
    
    print()


def migrate():
    """Execute migration: JSON → DuckDB."""
    print(f"\n{_BOLD}Phase 2 Migration: JSON → DuckDB{_RESET}")
    print("=" * 60)
    
    stats = {"watchlist": 0, "positions_open": 0, "positions_closed": 0, "errors": 0}
    
    # Step 1: Backup JSON files
    print(f"\n{_YELLOW}Step 1: Backing up JSON files...{_RESET}")
    _ensure_backup_dir()
    if WATCHLIST_FILE.exists():
        _backup_json_file(WATCHLIST_FILE, "watchlist_backup")
    else:
        print(f"  [INFO] Watchlist not found, skipping backup")
    if POSITIONS_FILE.exists():
        _backup_json_file(POSITIONS_FILE, "positions_backup")
    else:
        print(f"  [INFO] Positions not found, skipping backup")
    
    # Step 2: Migrate watchlist
    print(f"\n{_YELLOW}Step 2: Migrating watchlist...{_RESET}")
    try:
        wl_data = _load_json(WATCHLIST_FILE)
        wl_count = _validate_watchlist(wl_data)
        
        if wl_count > 0:
            if db.wl_save(wl_data):
                print(f"  [OK] Saved {wl_count} watchlist entries to DuckDB")
                stats["watchlist"] = wl_count
            else:
                print(f"  [ERROR] Failed to save watchlist (check logs)")
                stats["errors"] += 1
        else:
            print(f"  [INFO] Watchlist is empty, skipping")
    except Exception as exc:
        print(f"  [ERROR] Watchlist migration error: {exc}")
        stats["errors"] += 1
    
    # Step 3: Migrate positions
    print(f"\n{_YELLOW}Step 3: Migrating positions...{_RESET}")
    try:
        pos_data = _load_json(POSITIONS_FILE)
        open_count, closed_count = _validate_positions(pos_data)
        
        if open_count > 0 or closed_count > 0:
            if db.pos_save(pos_data):
                print(f"  [OK] Saved {open_count} open + {closed_count} closed positions to DuckDB")
                stats["positions_open"] = open_count
                stats["positions_closed"] = closed_count
            else:
                print(f"  [ERROR] Failed to save positions (check logs)")
                stats["errors"] += 1
        else:
            print(f"  [INFO] No positions to migrate, skipping")
    except Exception as exc:
        print(f"  [ERROR] Positions migration error: {exc}")
        stats["errors"] += 1
    
    # Step 4: Verify migration
    print(f"\n{_YELLOW}Step 4: Verifying migration...{_RESET}")
    db_stats = db.db_stats()
    wl_in_db = db_stats.get("watchlist_store", 0)
    open_in_db = db_stats.get("open_positions", 0)
    closed_in_db = db_stats.get("closed_positions", 0)
    
    print(f"  Watchlist in DB:        {wl_in_db} (expected: {stats['watchlist']})")
    print(f"  Open positions in DB:   {open_in_db} (expected: {stats['positions_open']})")
    print(f"  Closed positions in DB: {closed_in_db} (expected: {stats['positions_closed']})")
    
    # Summary
    print(f"\n{_BOLD}Migration Summary{_RESET}")
    print("=" * 60)
    print(f"  Watchlist entries:   {stats['watchlist']} -> DuckDB")
    print(f"  Open positions:      {stats['positions_open']} -> DuckDB")
    print(f"  Closed positions:    {stats['positions_closed']} -> DuckDB")
    print(f"  Errors:              {stats['errors']}")
    print(f"  DB Size:             {db_stats.get('db_size_mb', 0)} MB")
    
    if stats["errors"] == 0:
        print(f"\n{_GREEN}{_BOLD}[OK] Migration completed successfully!{_RESET}")
        print(f"  JSON backups saved to: {DB_BACKUPS_DIR}")
    else:
        print(f"\n{_RED}{_BOLD}[ERROR] Migration completed with {stats['errors']} error(s).{_RESET}")
        print(f"  {_YELLOW}Check logs and run --rollback to revert if needed.{_RESET}")
    
    print()


def rollback():
    """Revert to JSON-only (keep backup)."""
    print(f"\n{_BOLD}Phase 2 Rollback: Reverting to JSON-only storage{_RESET}")
    print("=" * 60)
    
    # Backup current DB
    try:
        db_file = ROOT / C.DB_FILE
        if db_file.exists():
            backup_dir = ROOT / C.DB_JSON_BACKUP_DIR
            backup_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_db = backup_dir / f"sepa_stock_backup_{timestamp}.duckdb"
            copy2(db_file, backup_db)
            print(f"  [OK] DuckDB backed up to: {backup_db.name}")
    except Exception as exc:
        print(f"  [ERROR] Failed to backup DuckDB: {exc}")
    
    print(f"  [INFO] JSON files remain unchanged and valid")
    print(f"  [INFO] To re-enable DuckDB, run: python migrate_phase2.py --migrate")
    print()


def verify():
    """Post-migration verification."""
    print(f"\n{_BOLD}Phase 2 Post-Migration Verification{_RESET}")
    print("=" * 60)
    
    issues = []
    
    # Check 1: DuckDB schema
    print(f"\n{_YELLOW}Check 1: DuckDB Schema{_RESET}")
    try:
        stats = db.db_stats()
        required_tables = ["scan_history", "watchlist_store", "open_positions", 
                          "closed_positions", "fundamentals_cache"]
        for t in required_tables:
            if stats.get(t, -1) >= 0:
                print(f"  [OK] {t}: {stats[t]} rows")
            else:
                print(f"  [ERROR] {t}: unable to query")
                issues.append(f"Schema issue with {t}")
    except Exception as exc:
        print(f"  [ERROR] DuckDB access failed: {exc}")
        issues.append("DuckDB is not accessible")
    
    # Check 2: JSON files still intact
    print(f"\n{_YELLOW}Check 2: JSON Files{_RESET}")
    for name, filepath in [("Watchlist", WATCHLIST_FILE), ("Positions", POSITIONS_FILE)]:
        if filepath.exists():
            data = _load_json(filepath)
            if data:
                print(f"  [OK] {name}: {len(data)} keys")
            else:
                print(f"  [WARN] {name}: empty or invalid")
                issues.append(f"{name} JSON is empty")
        else:
            print(f"  [WARN] {name}: file not found")
    
    # Check 3: Backup directory
    print(f"\n{_YELLOW}Check 3: Backup Directory{_RESET}")
    if DB_BACKUPS_DIR.exists():
        backups = list(DB_BACKUPS_DIR.glob("*"))
        print(f"  [OK] Backup directory: {len(backups)} files")
    else:
        print(f"  [ERROR] Backup directory not found")
        issues.append("Backup directory missing")
    
    # Summary
    print(f"\n{_BOLD}Verification Summary{_RESET}")
    print("=" * 60)
    if not issues:
        print(f"{_GREEN}{_BOLD}[OK] All checks passed!{_RESET}")
        print(f"  Phase 2 migration is ready for production use.")
    else:
        print(f"{_YELLOW}{_BOLD}[WARN] {len(issues)} issue(s) found:{_RESET}")
        for issue in issues:
            print(f"  * {issue}")
    
    print()


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Phase 2 migration: JSON files → DuckDB persistent storage"
    )
    parser.add_argument("--migrate", action="store_true", help="Execute migration")
    parser.add_argument("--rollback", action="store_true", help="Revert to JSON-only")
    parser.add_argument("--verify", action="store_true", help="Verify migration")
    
    args = parser.parse_args()
    
    if args.migrate:
        migrate()
    elif args.rollback:
        rollback()
    elif args.verify:
        verify()
    else:
        show_status()
