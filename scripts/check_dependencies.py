#!/usr/bin/env python
"""
SEPA-StockLab - Dependency Checker (Python version)
First-time setup tool to verify all required packages are installed.
"""
import subprocess
import sys

PACKAGES = [
    "finvizfinance",
    "yfinance", 
    "pandas_ta",
    "pandas",
    "jinja2",
    "tabulate",
    "pyarrow",
    "requests",
    "lxml",
    "flask",
    "flask-cors",
    "duckdb"
]

def check_package_installed(pkg):
    """Check if a package is installed"""
    try:
        __import__(pkg.replace('-', '_'))
        return True
    except ImportError:
        return False

def main():
    print("\n" + "="*70)
    print("  SEPA-StockLab - First Time Setup Tool")
    print("  (SEPA-StockLab - 首次設置檢查工具)")
    print("="*70 + "\n")
    
    # Step 1: Check Python version
    print("[1/4] Checking Python version...")
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info < (3, 9):
        print(f"  [ERROR] Python {py_version} detected (need 3.9+)")
        print("  Please upgrade Python from: https://www.python.org/downloads/")
        return False
    print(f"  [OK] Python {py_version}\n")
    
    # Step 2: Check pip
    print("[2/4] Checking pip...")
    result = subprocess.run([sys.executable, "-m", "pip", "--version"], 
                          capture_output=True, text=True)
    if result.returncode != 0:
        print("  [ERROR] pip not available")
        return False
    pip_version = result.stdout.strip()
    print(f"  [OK] {pip_version}\n")
    
    # Step 3: Check installed packages
    print("[3/4] Checking required packages...")
    missing = []
    for pkg in PACKAGES:
        # Handle special case: flask-cors -> flask_cors
        import_name = pkg.replace('-', '_')
        if check_package_installed(import_name):
            print(f"  [OK] {pkg}")
        else:
            print(f"  [MISS] {pkg} - Not installed")
            missing.append(pkg)
    print()
    
    # Step 4: Install missing packages
    if missing:
        print(f"[4/4] Installing {len(missing)} missing package(s)...")
        print()
        result = subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"],
                              capture_output=False, text=True)
        if result.returncode != 0:
            print("\n[ERROR] Installation failed!")
            print("Please check your internet connection and try again.")
            return False
        print("\n[OK] All dependencies installed successfully!")
    else:
        print("[4/4] Checking for missing packages...")
        print("  [OK] All dependencies already installed!")
    
    # Success
    print("\n" + "="*70)
    print("              SETUP COMPLETE! (設置完成!)")
    print("="*70 + "\n")
    print("You can now start the application:")
    print("(您現在可以啟動應用程序)\n")
    print("Option 1 - Web UI (Web 界面):")
    print("  python app.py")
    print("  OR double-click: start_web.bat\n")
    print("Option 2 - Command Line (命令列):")
    print("  python minervini.py --help\n")
    print("Documentation (文檔):")
    print("  - GUIDE.md (中文使用教程)")
    print("  - README.md (English)\n")
    print("="*70 + "\n")
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
