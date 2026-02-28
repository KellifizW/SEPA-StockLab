#!/usr/bin/env python3
import requests
resp = requests.post('http://localhost:5000/api/qm/analyze', json={'ticker': 'ASTI'}, timeout=30)
data = resp.json()
plan = data['result']['trade_plan']
print(f"API Response: day2_stop = {plan['day2_stop']} (type: {type(plan['day2_stop']).__name__})")
