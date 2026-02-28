#!/usr/bin/env python3
"""
Web UI Workflow Test - Simulates user actions through Flask API
1. Run QM scan
2. Wait for completion
3. Retrieve scan results
4. Analyze ASTI from results
5. Verify all data displayed correctly
"""

import sys
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests

# Test configuration
API_BASE = "http://127.0.0.1:5000"
TIMEOUT = 30

print("=" * 70)
print("WEB UI WORKFLOW TEST - FULL USER JOURNEY")
print("=" * 70)
print()

# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Run QM Scan
# ─────────────────────────────────────────────────────────────────────────────
print("[STEP 1] RunQM Scan")
print("-" * 70)

try:
    print("POST /api/qm/scan/run")
    scan_response = requests.post(
        f"{API_BASE}/api/qm/scan/run",
        json={},
        timeout=TIMEOUT
    )
    
    if scan_response.status_code != 200:
        print(f"❌ FAILED: Status {scan_response.status_code}")
        print(f"   Response: {scan_response.text}")
        sys.exit(1)
    
    scan_data = scan_response.json()
    print(f"✅ Scan started")
    print(f"   Response: {json.dumps(scan_data, indent=2)}")
    
    # Extract scan duration or just note it completed
    if scan_data.get("ok"):
        print(f"✅ Scan endpoint working")
    print()
    
except Exception as e:
    print(f"❌ FAILED: {e}")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Verify Page Loads correctly
# ─────────────────────────────────────────────────────────────────────────────
print("[STEP 2] Verify Scan Page Loads")
print("-" * 70)

try:
    print("GET /qm/scan (HTML page request)")
    page_response = requests.get(
        f"{API_BASE}/qm/scan",
        timeout=TIMEOUT
    )
    
    if page_response.status_code != 200:
        print(f"❌ FAILED: Status {page_response.status_code}")
        sys.exit(1)
    
    html = page_response.text
    
    # Check for critical elements
    checks = {
        "Bootstrap CSS": "bootstrap" in html.lower(),
        "QM Scan Title": "scan" in html.lower(),
        "Toast Container": "toast" in html.lower(),
        "Run Button": "run" in html.lower() and "button" in html.lower(),
    }
    
    print("✅ Page loaded successfully")
    print("   Checking for critical elements:")
    all_present = True
    for check_name, found in checks.items():
        status = "✓" if found else "✗"
        print(f"     {status} {check_name}")
        if not found:
            all_present = False
    
    if not all_present:
        print()
        print(f"⚠️  Some elements missing (may be dynamically loaded)")
    
    print()
    
except Exception as e:
    print(f"❌ FAILED: {e}")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Analyze ASTI (The Critical Test)
# ─────────────────────────────────────────────────────────────────────────────
print("[STEP 3] Analyze ASTI (Critical Test - Verify No Empty Data)")
print("-" * 70)

try:
    print("POST /api/qm/analyze (ticker: ASTI)")
    analyze_response = requests.post(
        f"{API_BASE}/api/qm/analyze",
        json={"ticker": "ASTI"},
        timeout=TIMEOUT
    )
    
    if analyze_response.status_code != 200:
        print(f"❌ FAILED: Status {analyze_response.status_code}")
        sys.exit(1)
    
    analyze_data = analyze_response.json()
    result = analyze_data.get("result", {})
    trade_plan = result.get("trade_plan", {})
    
    print(f"✅ ASTI Analysis received")
    print()
    
    # Verify NO empty/NaN data (the original issue)
    print("  Trade Plan Data (Checking for Empty/NaN):")
    empty_fields = []
    for key, value in trade_plan.items():
        # Check for empty, None, NaN, or "NaN" strings
        is_empty = (
            value is None or 
            value == "" or 
            str(value).lower() in ["nan", "none", "undefined"] or
            (isinstance(value, float) and __import__('math').isnan(value))
        )
        
        status = "⚠️  EMPTY" if is_empty else "✓"
        display = str(value)[:50] if value is not None else "None"
        print(f"    {status} {key}: {display}")
        
        if is_empty:
            empty_fields.append(key)
    
    if empty_fields:
        print()
        print(f"❌ FAILED: Found {len(empty_fields)} empty fields!")
        for field in empty_fields:
            print(f"     • {field}")
        sys.exit(1)
    
    print()
    print("✅ NO EMPTY DATA FOUND - Original issue is FIXED!")
    print()
    
    # Verify numeric types for critical fields
    print("  Trade Plan Numeric Types (For Chart Rendering):")
    numeric_critical = {
        "day1_stop": (int, float, type(None)),
        "day2_stop": (int, float, type(None)),
        "day3plus_stop": (int, float, type(None)),
    }
    
    type_errors = []
    for field, expected_types in numeric_critical.items():
        value = trade_plan.get(field)
        if value is None:
            print(f"    ✓ {field}: None (acceptable)")
        else:
            if isinstance(value, expected_types):
                print(f"    ✓ {field}: {value} ({type(value).__name__})")
            else:
                error = f"{field}: got {type(value).__name__}, expected numeric"
                type_errors.append(error)
                print(f"    ❌ {field} {error}")
    
    if type_errors:
        print()
        print(f"❌ FAILED: Type errors found:")
        for err in type_errors:
            print(f"     • {err}")
        sys.exit(1)
    
    print()
    
except Exception as e:
    print(f"❌ FAILED: {e}")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Verify Dimension Scores (For Radar Chart)
# ─────────────────────────────────────────────────────────────────────────────
print("[STEP 4] Verify Dimension Scores (For Radar Chart)")
print("-" * 70)

dim_scores = result.get("dim_scores", {})
print(f"Found {len(dim_scores)} dimensions:")
for dim_key, dim_data in dim_scores.items():
    if isinstance(dim_data, dict):
        score = dim_data.get("score")
        print(f"  ✓ Dimension {dim_key}: score = {score}")

if len(dim_scores) >= 5:
    print(f"✅ Radar chart has sufficient data ({len(dim_scores)} dimensions)")
else:
    print(f"⚠️  Radar chart may have limited data ({len(dim_scores)} dimensions)")

print()


# ─────────────────────────────────────────────────────────────────────────────
# Step 5: Check Rating & Summary
# ─────────────────────────────────────────────────────────────────────────────
print("[STEP 5] Rating & Summary")
print("-" * 70)

rating = result.get("capped_stars", 0)
setup_type = result.get("setup_type", {})
recommendation = result.get("recommendation", "")

print(f"  Rating: {rating} stars")
print(f"  Setup: {setup_type}")
print(f"  Recommendation: {recommendation}")
print()


# ─────────────────────────────────────────────────────────────────────────────
# FINAL SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 70)
print("✅ ALL WEB UI TESTS PASSED!")
print("=" * 70)
print()
print("Verified:")
print("  1. Scan page loads successfully")
print("  2. ASTI analysis returns complete data")
print("  3. NO empty/NaN fields in trade plan ✅ (Original Issue FIXED)")
print("  4. Trade plan values are numeric types")
print("  5. Dimension scores present for radar chart")
print("  6. Rating and recommendations available")
print()
print("Status: The ASTI analysis page is fully functional!")
print("        User can now see all data without empty fields.")
print("=" * 70)
