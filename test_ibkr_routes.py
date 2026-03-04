#!/usr/bin/env python
"""
Test IBKR API routes directly
"""
import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Set up Flask app
os.environ['FLASK_ENV'] = 'testing'
import app as app_module

# Create test client
test_client = app_module.app.test_client()

print("Testing IBKR API Routes")
print("=" * 60)

# Test GET /api/ibkr/status
print("\n1. Testing GET /api/ibkr/status")
response = test_client.get('/api/ibkr/status')
print(f"   Status Code: {response.status_code}")
print(f"   Response: {response.get_json()}")

# Test POST /api/ibkr/connect
print("\n2. Testing POST /api/ibkr/connect (first time)")
response = test_client.post('/api/ibkr/connect')
print(f"   Status Code: {response.status_code}")
data = response.get_json()
print(f"   Response: {data}")

# Test GET /api/ibkr/status after connect
print("\n3. Testing GET /api/ibkr/status (after connect)")
response = test_client.get('/api/ibkr/status')
print(f"   Status Code: {response.status_code}")
print(f"   Response: {response.get_json()}")

# Test POST /api/ibkr/disconnect
print("\n4. Testing POST /api/ibkr/disconnect")
response = test_client.post('/api/ibkr/disconnect')
print(f"   Status Code: {response.status_code}")
print(f"   Response: {response.get_json()}")
