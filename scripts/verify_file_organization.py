#!/usr/bin/env python
"""
Verify file organization is correct and all modules can be imported.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

print("\n" + "="*70)
print("文件整理验证 (File Organization Verification)")
print("="*70 + "\n")

# Test 1: Import core modules
print("[1] 核心模块 (Core Modules)")
print("-" * 70)
try:
    import trader_config as C
    print("  ✓ trader_config imported successfully")
except Exception as e:
    print(f"  ✗ trader_config: {e}")
    sys.exit(1)

try:
    from modules import data_pipeline
    print("  ✓ modules.data_pipeline imported successfully")
except Exception as e:
    print(f"  ✗ modules.data_pipeline: {e}")
    sys.exit(1)

# Test 2: Verify script file locations
print("\n[2] 脚本文件位置 (Script File Locations)")
print("-" * 70)
scripts_dir = ROOT / "scripts"
required_scripts = [
    "check_dependencies.py",
    "check_positions.py",
    "diagnose.py",
    "quick_check.py",
    "verify_phase2.py",
    "migrate_phase2.py",
    "perf_test.py",
]

for script in required_scripts:
    script_path = scripts_dir / script
    if script_path.exists():
        print(f"  ✓ {script}")
    else:
        print(f"  ✗ {script} (not found)")

# Test 3: Verify test file locations
print("\n[3] 测试文件位置 (Test File Locations)")
print("-" * 70)
tests_dir = ROOT / "tests"
required_tests = [
    "test_api_position.py",
    "test_app_import.py",
    "test_phase2_implementation.py",
    "test_phase3_endpoints.py",
    "test_positions.py",
    "test_position_add.py",
    "test_position_complete.py",
]

for test_file in required_tests:
    test_path = tests_dir / test_file
    if test_path.exists():
        print(f"  ✓ {test_file}")
    else:
        print(f"  ✗ {test_file} (not found)")

# Test 4: Verify documentation locations
print("\n[4] 文档文件位置 (Documentation Locations)")
print("-" * 70)
docs_dir = ROOT / "docs"
required_docs = [
    "GUIDE.md",
    "README.md",
    "stockguide.md",
    "PHASE2_IMPLEMENTATION.md",
]

for doc in required_docs:
    doc_path = docs_dir / doc
    if doc_path.exists():
        print(f"  ✓ {doc}")
    else:
        print(f"  ✗ {doc} (not found)")

# Test 5: Verify batch file locations
print("\n[5] 批处理文件位置 (Batch File Locations)")
print("-" * 70)
bin_dir = ROOT / "bin"
required_bats = [
    "open_this_first_time.bat",
    "open_this_first_time_py.bat",
    "start_web.bat",
]

for bat in required_bats:
    bat_path = bin_dir / bat
    if bat_path.exists():
        print(f"  ✓ {bat}")
    else:
        print(f"  ✗ {bat} (not found)")

# Test 6: Verify core files in root
print("\n[6] 根目录核心文件 (Root Core Files)")
print("-" * 70)
required_root = [
    "app.py",
    "minervini.py",
    "start_web.py",
    "run_app.py",
    "trader_config.py",
    "requirements.txt",
]

for fname in required_root:
    fpath = ROOT / fname
    if fpath.exists():
        print(f"  ✓ {fname}")
    else:
        print(f"  ✗ {fname} (not found)")

print("\n" + "="*70)
print("✅ 文件整理验证完成 (File Organization Verification Complete)")
print("="*70 + "\n")
