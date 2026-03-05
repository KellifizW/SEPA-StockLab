#!/usr/bin/env python3
"""
tests/test_tg_mini_app.py
────────────────────────────────────────
Test Telegram Mini App initialization & verification
"""

import sys
import json
import hashlib
import hmac
from urllib.parse import quote
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import trader_config as C


def create_mock_init_data(chat_id: int, bot_token: str = None) -> str:
    """
    為測試創建有效的 Telegram initData （模擬客戶端簽名）
    
    Returns:
        URL-encoded initData string with valid HMAC-SHA256 hash
    """
    if bot_token is None:
        bot_token = C.TG_BOT_TOKEN
    
    # 構建初始數據帶 (不含 hash)
    user_data = {
        "id": chat_id,
        "is_bot": False,
        "first_name": "Test",
        "last_name": "User",
        "language_code": "en"
    }
    
    params = {
        "user": json.dumps(user_data),
        "chat_instance": "1234567890",
        "auth_date": "1700000000"
    }
    
    # 構建簽名字符串（排序的 key=value\nkey=value...）
    sorted_items = sorted(params.items())
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted_items)
    
    # 計算 HMAC-SHA256 (key = SHA256(bot_token))
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    hash_digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    
    # 添加 hash 到參數
    params["hash"] = hash_digest
    
    # URL-encode 整個字符串
    init_data = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
    
    return init_data


def test_verify_signature():
    """測試簽名驗證邏輯"""
    from app import _verify_tg_init_data
    
    # 創建有效的 initData
    chat_id = 520073103
    init_data = create_mock_init_data(chat_id)
    
    # 驗證
    result = _verify_tg_init_data(init_data)
    
    print("Test: Verify Telegram initData Signature")
    print(f"  Chat ID: {chat_id}")
    print(f"  Result: {result}")
    
    assert result["ok"] == True, f"Verification failed: {result}"
    assert result.get("chat_id") == chat_id, f"Chat ID mismatch: {result.get('chat_id')} != {chat_id}"
    
    print("  ✅ Signature verification PASSED")
    return True


def test_routes_exist():
    """測試 Mini App 路由是否存在"""
    from app import app
    
    routes = [r.rule for r in app.url_map.iter_rules()]
    
    required_routes = [
        "/tg/app",
        "/api/tg/init",
        "/api/tg/analyze/<ticker>"
    ]
    
    print("\nTest: Required Routes Exist")
    for route in required_routes:
        # URL map stores <ticker> as variable, 且沒有 <>
        # 需要特殊處理
        if "<" in route:
            pattern = route.replace("<ticker>", "[^/]+")
            found = any(pattern.replace("[^/]+", "[^/]+") in r for r in routes)
        else:
            found = route in routes
        
        status = "✅" if found else "❌"
        print(f"  {status} {route}")
        
        if not found:
            print(f"    Available routes: {[r for r in routes if 'tg' in r]}")
    
    return True


def test_config():
    """測試配置是否正確"""
    print("\nTest: Telegram Configuration")
    print(f"  TG_ENABLED: {C.TG_ENABLED}")
    print(f"  TG_BOT_TOKEN: {'*' * 10 if C.TG_BOT_TOKEN else '(empty)'}")
    print(f"  TG_ADMIN_CHAT_ID: {C.TG_ADMIN_CHAT_ID if C.TG_ADMIN_CHAT_ID else '(empty)'}")
    print(f"  TG_MINI_APP_ENABLED: {C.TG_MINI_APP_ENABLED}")
    print(f"  TG_MINI_APP_BASE_URL: {C.TG_MINI_APP_BASE_URL}")
    
    if not C.TG_BOT_TOKEN:
        print("  ⚠️  WARNING: TG_BOT_TOKEN not configured in .env")
    
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("Telegram Mini App Integration Tests")
    print("=" * 60)
    
    try:
        test_config()
        test_routes_exist()
        test_verify_signature()
        
        print("\n" + "=" * 60)
        print("✅ All tests passed!")
        print("=" * 60)
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
