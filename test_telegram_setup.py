#!/usr/bin/env python3
"""
測試 Telegram Bot 設定是否正確

說明：
  1. 將 BotFather 給你的 Token 填入 TG_BOT_TOKEN
  2. 將你的 Chat ID 填入 TG_CHAT_ID
  3. 執行此腳本驗證設定
"""

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import trader_config as C
from modules.telegram_bot import send_message, get_updates

print("=" * 60)
print("  🤖 Telegram Bot 設定測試")
print("=" * 60)

# 檢查配置
print("\n[步驟 1] 檢查配置...")
print(f"  TG_ENABLED:       {C.TG_ENABLED}")
print(f"  TG_BOT_TOKEN:     {'✅ 已設定' if C.TG_BOT_TOKEN else '❌ 未設定'}")
print(f"  TG_CHAT_ID:       {'✅ 已設定' if C.TG_CHAT_ID else '❌ 未設定'}")
print(f"  TG_POLL_INTERVAL: {C.TG_POLL_INTERVAL} 秒")

if not C.TG_BOT_TOKEN or not C.TG_CHAT_ID:
    print("\n❌ 錯誤: Token 或 Chat ID 未設定")
    print("\n使用方法:")
    print("  1. 編輯 trader_config.py")
    print("  2. 找到 'TELEGRAM BOT' 區塊（約第末尾）")
    print("  3. 填入你的 TG_BOT_TOKEN 和 TG_CHAT_ID")
    print("  4. 設定 TG_ENABLED = True")
    print("  5. 重新執行本測試")
    sys.exit(1)

# 測試發送訊息
print("\n[步驟 2] 測試 API 連線和訊息發送...")
test_msg = "✅ Telegram Bot 連線測試成功！"
success = send_message(test_msg)

if success:
    print(f"  ✅ 訊息已傳送")
    print(f"\n檢查你的 Telegram 是否收到測試訊息...")
else:
    print(f"  ❌ 訊息發送失敗")
    print(f"  請檢查:")
    print(f"    - Token 是否正確")
    print(f"    - Chat ID 是否正確")
    print(f"    - 網路連線是否正常")
    sys.exit(1)

# 測試接收訊息
print("\n[步驟 3] 監聽傳入訊息 (timeout 10 秒)...")
print("  嘗試在 Telegram 中輸入 /help，看看程式是否能接收...")
try:
    updates = get_updates(offset=0)
    if updates:
        print(f"  ✅ 收到 {len(updates)} 條訊息")
        for u in updates[:1]:
            txt = u.get("message", {}).get("text", "")
            print(f"     示例: {txt[:50]}")
    else:
        print(f"  ℹ️  暫無新訊息（這是正常的，如果你沒有剛輸入）")
except Exception as e:
    print(f"  ❌ 接收訊息失敗: {e}")
    sys.exit(1)

print("\n" + "=" * 60)
print("✅ 所有測試完成！")
print("\n下一步:")
print("  1. 在 trader_config.py 中將 TG_ENABLED 改為 True")
print("  2. 執行 start_web.py 啟動程式")
print("  3. 在 Telegram 中輸入 /market 查詢市場環境")
print("=" * 60)
