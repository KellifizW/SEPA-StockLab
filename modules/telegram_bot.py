"""
modules/telegram_bot.py
──────────────────────────
Telegram Bot Polling Integration — 無需 Webhook

支援的指令：
  /market   — 評估市場環境 (regime, breadth, sectors)
  /help     — 顯示幫助訊息

實作方式：
  1. Polling — 程式每 TG_POLL_INTERVAL 秒主動詢問 Telegram 是否有新訊息
  2. 無需公開 IP 或 Webhook 配置
  3. Daemon thread 運行，Flask shutdown 時自動結束
"""

import sys
import logging
import threading
import time
from pathlib import Path
from typing import Optional, Dict, List

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import trader_config as C

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Telegram API Constants
# ─────────────────────────────────────────────────────────────────────────────
_TG_API_BASE = "https://api.telegram.org"
_TG_REQUEST_TIMEOUT = 15  # seconds
_TG_POLL_TIMEOUT = 10     # long-polling timeout

# Threading control
_polling_thread: Optional[threading.Thread] = None
_polling_stop_event = threading.Event()

# ─────────────────────────────────────────────────────────────────────────────
# ANSI Colors
# ─────────────────────────────────────────────────────────────────────────────
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


# ═══════════════════════════════════════════════════════════════════════════════
# Core API Wrappers
# ═══════════════════════════════════════════════════════════════════════════════

def _validate_config() -> bool:
    """檢查 Telegram 配置是否有效"""
    if not C.TG_BOT_TOKEN:
        logger.error("❌ TG_BOT_TOKEN 為空")
        return False
    if not C.TG_CHAT_ID:
        logger.error("❌ TG_CHAT_ID 為空")
        return False
    return True


def _build_url(method: str) -> str:
    """建立 Telegram API URL"""
    return f"{_TG_API_BASE}/bot{C.TG_BOT_TOKEN}/{method}"


def get_updates(offset: int = 0) -> List[Dict]:
    """
    取得新訊息
    
    Args:
        offset: 上次讀取的最後 update_id + 1
    
    Returns:
        [{"update_id": ..., "message": {"text": "/market", ...}}, ...]
    """
    try:
        url = _build_url("getUpdates")
        params = {
            "offset": offset,
            "timeout": _TG_POLL_TIMEOUT,
            "allowed_updates": ["message"]  # 只關心文字訊息
        }
        resp = requests.get(url, params=params, timeout=_TG_REQUEST_TIMEOUT)
        data = resp.json()
        
        if not data.get("ok"):
            logger.warning(f"Telegram API error: {data.get('description', 'unknown')}")
            return []
        
        return data.get("result", [])
    except requests.exceptions.ConnectTimeout:
        logger.warning("Telegram 連線逾時 (ConnectTimeout)")
        return []
    except requests.exceptions.ReadTimeout:
        # Long-polling 預期的超時，表示沒有新訊息
        return []
    except Exception as e:
        logger.error(f"get_updates 失敗: {e}", exc_info=False)
        return []


def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """
    傳訊息給使用者
    
    Args:
        text: 訊息內容（支持 HTML 格式）
        parse_mode: 解析模式 ("HTML", "MarkdownV2", None)
    
    Returns:
        成功與否
    """
    try:
        url = _build_url("sendMessage")
        data = {
            "chat_id": C.TG_CHAT_ID,
            "text": text,
        }
        if parse_mode:
            data["parse_mode"] = parse_mode
        
        resp = requests.post(url, json=data, timeout=_TG_REQUEST_TIMEOUT)
        result = resp.json()
        
        if not result.get("ok"):
            logger.warning(f"sendMessage 失敗: {result.get('description', 'unknown')}")
            return False
        
        return True
    except Exception as e:
        logger.error(f"send_message 失敗: {e}", exc_info=False)
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# Command Handlers
# ═══════════════════════════════════════════════════════════════════════════════

def _handle_market_command() -> str:
    """
    /market 指令 — 評估市場環境
    
    Returns:
        格式化的市場環境文字 (HTML 格式)
    """
    try:
        from modules.market_env import assess
        
        result = assess(verbose=False)
        
        regime = result.get("regime", "UNKNOWN")
        breadth_pct = result.get("breadth_pct", 0)
        spy_trend = result.get("spy_trend", "N/A")
        dist_days = result.get("distribution_days", 0)
        nh_nl = result.get("nh_nl_ratio", 0)
        
        # 取得領先和落後的板塊
        leading = result.get("leading_sectors", [])
        lagging = result.get("lagging_sectors", [])
        
        # 格式化輸出 (HTML 模式)
        leading_str = ", ".join(leading[:5]) if leading else "N/A"
        lagging_str = ", ".join(lagging[:5]) if lagging else "N/A"
        
        text = f"""<b>📊 市場環境評估</b>

<b>Regime:</b> <code>{regime}</code>
<b>SPY Trend:</b> <code>{spy_trend}</code>
<b>Breadth:</b> <code>{breadth_pct:.1f}%</code>
<b>Distribution Days:</b> <code>{dist_days}</code>
<b>NH/NL Ratio:</b> <code>{nh_nl:.2f}</code>

<b>💹 領先板塊:</b>
{leading_str}

<b>📉 落後板塊:</b>
{lagging_str}

<i>評估時間: {result.get("assessed_at", "N/A")}</i>"""
        return text.strip()
    except Exception as e:
        logger.error(f"_handle_market_command 失敗: {e}", exc_info=True)
        return f"❌ 評估市場環境失敗:\n{str(e)[:100]}"


def _handle_help_command() -> str:
    """
    /help 指令 — 顯示幫助
    
    Returns:
        幫助文字 (HTML 格式)
    """
    text = """<b>📋 可用指令</b>

<b>/market</b> — 評估市場環境 (Regime, Breadth, Sectors)
<b>/help</b> — 顯示此幫助訊息

<i>SEPA StockLab Telegram Bot</i>
<i>Polling mode (本地運行)</i>"""
    return text.strip()


def _handle_unknown_command(text: str) -> str:
    """處理未知指令"""
    return f"❌ 未知指令: <code>{text[:50]}</code>\n輸入 /help 查看可用指令"


# ═══════════════════════════════════════════════════════════════════════════════
# Polling Loop
# ═══════════════════════════════════════════════════════════════════════════════

def start_polling():
    """
    啟動 Polling 迴圈 — 在 daemon thread 中執行
    
    每 TG_POLL_INTERVAL 秒檢查一次新訊息，處理指令並回覆
    """
    if not _validate_config():
        logger.error("Telegram 設定無效，無法啟動 Polling")
        return
    
    logger.info(f"🟢 Telegram Polling 已啟動 (interval: {C.TG_POLL_INTERVAL}s)")
    
    offset = 0  # Telegram update_id offset
    consecutive_errors = 0
    
    while not _polling_stop_event.is_set():
        try:
            # 取得新訊息
            updates = get_updates(offset)
            
            # 重置錯誤計數
            if updates:
                consecutive_errors = 0
            
            # 處理每個訊息
            for update in updates:
                try:
                    _process_update(update)
                    # 更新 offset，防止重複處理
                    offset = update.get("update_id", offset) + 1
                except Exception as e:
                    logger.error(f"處理 update 失敗: {e}", exc_info=False)
            
            # 等待下一次 polling
            time.sleep(C.TG_POLL_INTERVAL)
        
        except Exception as e:
            consecutive_errors += 1
            logger.error(f"Polling 迴圈錯誤 (#{consecutive_errors}): {e}", exc_info=False)
            
            # 連續錯誤超過 5 次，等待更久
            if consecutive_errors > 5:
                logger.warning("連續錯誤過多，等待 10 秒後重試...")
                time.sleep(10)
            else:
                time.sleep(C.TG_POLL_INTERVAL * 2)
    
    logger.info("🟡 Telegram Polling 已停止")


def _process_update(update: Dict):
    """
    處理單個 update

    Args:
        update: {"update_id": ..., "message": {"text": "...", "chat": {"id": ...}}}
    """
    try:
        msg = update.get("message", {})
        if not msg:
            return
        
        msg_text = msg.get("text", "").strip()
        chat_id = msg.get("chat", {}).get("id")
        
        # 驗證是否來自正確的 Chat ID
        if str(chat_id) != str(C.TG_CHAT_ID):
            logger.warning(f"⚠️ 訊息來自非授權 Chat ID: {chat_id}")
            return
        
        # 解析指令
        if not msg_text:
            return
        
        # 提取指令（第一個 '/' 後的單詞）
        cmd_parts = msg_text.split()
        cmd = cmd_parts[0].lower()
        
        logger.info(f"📨 收到指令: {cmd}")
        
        # 分派指令
        if cmd == "/market":
            reply = _handle_market_command()
        elif cmd == "/help":
            reply = _handle_help_command()
        else:
            reply = _handle_unknown_command(msg_text)
        
        # 傳送回覆
        success = send_message(reply, parse_mode="HTML")
        
        if success:
            logger.info(f"✅ 回覆已傳送")
        else:
            logger.warning(f"⚠️ 回覆傳送失敗")
    
    except Exception as e:
        logger.error(f"_process_update 異常: {e}", exc_info=True)


def stop_polling():
    """停止 Polling 迴圈"""
    _polling_stop_event.set()
    logger.info("Telegram Polling 已標記為停止")
