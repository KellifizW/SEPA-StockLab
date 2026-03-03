"""
modules/telegram_bot.py
──────────────────────────
Telegram Bot Polling Integration + 管理員審批系統

支援的指令（公共）：
  /market   — 評估市場環境 (regime, breadth, sectors)
  /help     — 顯示幫助訊息
  
支援的指令（管理員專用）：
  /approve <chat_id>     — 批准新用戶
  /deny <chat_id>        — 拒絕新用戶
  /admin_list            — 查看待批准列表

新用戶流程：
  1. 新用戶 (Chat ID: 520073103) 輸入任何指令
  2. Bot 檢查白名單，發現未批准
  3. Bot 發送審批請求給管理員
  4. 管理員輸入 /approve 520073103
  5. 新用戶被加入白名單，可正常使用
"""

import sys
import logging
import threading
import time
import json
from pathlib import Path
from typing import Optional, Dict, List, Set

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

# Approval & Whitelist
_APPROVED_FILE = ROOT / "data" / "approved_chat_ids.json"
_PENDING_REQUESTS: Dict[str, int] = {}  # {chat_id: timestamp}
_APPROVED_IDS: Set[str] = set()

# Threading control
_polling_thread: Optional[threading.Thread] = None
_polling_stop_event = threading.Event()
_approval_lock = threading.Lock()

# ─────────────────────────────────────────────────────────────────────────────
# ANSI Colors
# ─────────────────────────────────────────────────────────────────────────────
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


# ═══════════════════════════════════════════════════════════════════════════════
# Persistence: Load/Save Approved IDs
# ═══════════════════════════════════════════════════════════════════════════════

def _load_approved_ids():
    """從檔案載入已批准的 Chat ID"""
    global _APPROVED_IDS
    try:
        if _APPROVED_FILE.exists():
            with open(_APPROVED_FILE, "r") as f:
                data = json.load(f)
                _APPROVED_IDS = set(str(cid) for cid in data.get("approved", []))
                logger.info(f"已載入 {len(_APPROVED_IDS)} 個已批准的 Chat ID")
    except Exception as e:
        logger.error(f"無法載入已批准列表: {e}")
        _APPROVED_IDS = set()


def _save_approved_ids():
    """將已批准的 Chat ID 保存到檔案"""
    try:
        _APPROVED_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_APPROVED_FILE, "w") as f:
            json.dump({"approved": sorted(list(_APPROVED_IDS))}, f, indent=2)
    except Exception as e:
        logger.error(f"無法保存已批准列表: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# Core API Wrappers
# ═══════════════════════════════════════════════════════════════════════════════

def _validate_config() -> bool:
    """檢查 Telegram 配置是否有效"""
    if not C.TG_BOT_TOKEN:
        logger.error("❌ TG_BOT_TOKEN 為空")
        return False
    if not C.TG_ADMIN_CHAT_ID:
        logger.error("❌ TG_ADMIN_CHAT_ID 為空")
        return False
    return True


def _build_url(method: str) -> str:
    """建立 Telegram API URL"""
    return f"{_TG_API_BASE}/bot{C.TG_BOT_TOKEN}/{method}"


def get_updates(offset: int = 0) -> List[Dict]:
    """
    取得新訊息和 callback queries
    
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
            "allowed_updates": ["message", "callback_query"]  # 接收訊息和按鈕點擊
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


def send_message(text: str, chat_id: Optional[str] = None, parse_mode: str = "HTML", reply_markup: Optional[Dict] = None) -> bool:
    """
    傳訊息給使用者
    
    Args:
        text: 訊息內容（支持 HTML 格式）
        chat_id: 目標 Chat ID（預設為管理員）
        parse_mode: 解析模式 ("HTML", "MarkdownV2", None)
        reply_markup: Inline keyboard markup (dict with "inline_keyboard" key)
    
    Returns:
        成功與否
    """
    if chat_id is None:
        chat_id = C.TG_ADMIN_CHAT_ID
    
    try:
        url = _build_url("sendMessage")
        data = {
            "chat_id": chat_id,
            "text": text,
        }
        if parse_mode:
            data["parse_mode"] = parse_mode
        if reply_markup:
            data["reply_markup"] = reply_markup
        
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
# Approval System
# ═══════════════════════════════════════════════════════════════════════════════

def _is_approved(chat_id: str) -> bool:
    """檢查 Chat ID 是否已批准"""
    return str(chat_id) in _APPROVED_IDS


def _is_admin(chat_id: str) -> bool:
    """檢查是否為管理員"""
    return str(chat_id) == str(C.TG_ADMIN_CHAT_ID)


def _request_approval(chat_id: str):
    """向管理員發送新用戶批准請求（帶快速按鈕）"""
    with _approval_lock:
        if chat_id in _PENDING_REQUESTS:
            # 5 分鐘內已發送過請求，不重複
            if time.time() - _PENDING_REQUESTS[chat_id] < 300:
                return
        
        _PENDING_REQUESTS[chat_id] = time.time()
    
    msg = f"""<b>🔔 新 Chat ID 訪問請求</b>

<b>Chat ID:</b> <code>{chat_id}</code>

點擊下方按鈕以批准或拒絕此用戶"""
    
    # Inline keyboard with approve/deny buttons
    reply_markup = {
        "inline_keyboard": [
            [
                {
                    "text": "✅ 批准",
                    "callback_data": f"approve_{chat_id}"
                },
                {
                    "text": "❌ 拒絕",
                    "callback_data": f"deny_{chat_id}"
                }
            ]
        ]
    }
    
    send_message(msg, chat_id=C.TG_ADMIN_CHAT_ID, reply_markup=reply_markup)
    logger.info(f"已向管理員發送批准請求 (Chat ID: {chat_id})")


# ═══════════════════════════════════════════════════════════════════════════════
# Command Handlers
# ═══════════════════════════════════════════════════════════════════════════════

def _handle_market_command(chat_id: str) -> str:
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


def _handle_approve_command(chat_id: str, args: str) -> str:
    """
    /approve <chat_id> 指令 — 批准新用戶（管理員專用）
    """
    if not _is_admin(chat_id):
        return "❌ 此指令只有管理員可用"
    
    if not args:
        return "❌ 用法: /approve [chat_id]"
    
    target_chat_id = args.strip()
    
    with _approval_lock:
        if target_chat_id in _APPROVED_IDS:
            return f"⚠️ Chat ID {target_chat_id} 已被批准"
        
        _APPROVED_IDS.add(target_chat_id)
        _save_approved_ids()
    
    # 通知新用戶
    notify_msg = f"✅ 恭喜！你已被批准使用此 Bot\n輸入 /help 查看可用指令"
    send_message(notify_msg, chat_id=target_chat_id)
    
    logger.info(f"✅ Chat ID {target_chat_id} 已被批准")
    return f"✅ Chat ID {target_chat_id} 已被批准"


def _handle_deny_command(chat_id: str, args: str) -> str:
    """
    /deny <chat_id> 指令 — 拒絕新用戶（管理員專用）
    """
    if not _is_admin(chat_id):
        return "❌ 此指令只有管理員可用"
    
    if not args:
        return "❌ 用法: /deny [chat_id]"
    
    target_chat_id = args.strip()
    
    # 通知拒絕的用戶
    notify_msg = "❌ 你的訪問請求已被拒絕"
    send_message(notify_msg, chat_id=target_chat_id)
    
    # 從待審清單中移除
    _PENDING_REQUESTS.pop(target_chat_id, None)
    
    logger.info(f"❌ Chat ID {target_chat_id} 已被拒絕")
    return f"❌ Chat ID {target_chat_id} 的請求已被拒絕"


def _handle_admin_list_command(chat_id: str) -> str:
    """
    /admin_list 指令 — 查看待批准列表（管理員專用）
    """
    if not _is_admin(chat_id):
        return "❌ 此指令只有管理員可用"
    
    pending = [(cid, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))) 
               for cid, ts in sorted(_PENDING_REQUESTS.items())]
    approved = sorted(list(_APPROVED_IDS))
    
    text = f"""<b>📊 管理員面板</b>

<b>⏳ 待批准用戶 ({len(pending)}):</b>
"""
    if pending:
        for cid, ts in pending:
            text += f"\n<code>{cid}</code> (請求時間: {ts})"
            text += f"\n  /approve {cid}  |  /deny {cid}"
    else:
        text += "\n(無待審用戶)"
    
    text += f"\n\n<b>✅ 已批准用戶 ({len(approved)}):</b>\n"
    if approved:
        for cid in approved[:10]:  # 只顯示前 10 個
            text += f"\n<code>{cid}</code>"
        if len(approved) > 10:
            text += f"\n... 及其他 {len(approved) - 10} 個"
    else:
        text += "\n(無已批准用戶)"
    
    return text.strip()


# ═══════════════════════════════════════════════════════════════════════════════
# Callback Query Handlers
# ═══════════════════════════════════════════════════════════════════════════════

def _answer_callback_query(callback_query_id: str, text: str = ""):
    """
    回覆 callback query（按鈕點擊確認）
    
    Args:
        callback_query_id: Callback query ID
        text: 提示文本
    """
    try:
        url = _build_url("answerCallbackQuery")
        data = {
            "callback_query_id": callback_query_id,
            "text": text,
            "show_alert": False
        }
        
        resp = requests.post(url, json=data, timeout=_TG_REQUEST_TIMEOUT)
        result = resp.json()
        
        if not result.get("ok"):
            logger.warning(f"answerCallbackQuery 失敗: {result.get('description', 'unknown')}")
            return False
        
        return True
    except Exception as e:
        logger.error(f"_answer_callback_query 失敗: {e}", exc_info=False)
        return False


def _handle_callback_query(callback_query: Dict):
    """
    處理按鈕點擊事件
    
    Args:
        callback_query: {"id": ..., "from": {"id": ...}, "data": "approve_520073103"}
    """
    try:
        callback_data = callback_query.get("data", "")
        admin_chat_id = callback_query.get("from", {}).get("id")
        callback_query_id = callback_query.get("id")
        
        if not admin_chat_id:
            return
        
        admin_chat_id_str = str(admin_chat_id)
        
        # 確認只有管理員才能點擊按鈕
        if not _is_admin(admin_chat_id_str):
            logger.warning(f"🔒 非管理員嘗試使用按鈕: {admin_chat_id_str}")
            return
        
        # 解析 callback_data: "approve_520073103" 或 "deny_520073103"
        if callback_data.startswith("approve_"):
            target_chat_id = callback_data[8:]  # 去掉 "approve_" 前綴
            
            with _approval_lock:
                if target_chat_id not in _APPROVED_IDS:
                    _APPROVED_IDS.add(target_chat_id)
                    _save_approved_ids()
                    
                    # 通知新用戶
                    notify_msg = f"✅ 恭喜！你已被批准使用此 Bot\n輸入 /help 查看可用指令"
                    send_message(notify_msg, chat_id=target_chat_id)
                    
                    # 更新管理員消息
                    msg_text = f"""<b>✅ 已批准</b>

Chat ID: <code>{target_chat_id}</code>
操作者: <code>{admin_chat_id_str}</code>"""
                    send_message(msg_text, chat_id=admin_chat_id_str)
                    
                    logger.info(f"✅ Chat ID {target_chat_id} 已被批准 (by {admin_chat_id_str})")
                    
                    # 移除待審記錄
                    _PENDING_REQUESTS.pop(target_chat_id, None)
        
        elif callback_data.startswith("deny_"):
            target_chat_id = callback_data[5:]  # 去掉 "deny_" 前綴
            
            # 通知拒絕的用戶
            notify_msg = "❌ 你的訪問請求已被拒絕"
            send_message(notify_msg, chat_id=target_chat_id)
            
            # 更新管理員消息
            msg_text = f"""<b>❌ 已拒絕</b>

Chat ID: <code>{target_chat_id}</code>
操作者: <code>{admin_chat_id_str}</code>"""
            send_message(msg_text, chat_id=admin_chat_id_str)
            
            # 移除待審記錄
            _PENDING_REQUESTS.pop(target_chat_id, None)
            
            logger.info(f"❌ Chat ID {target_chat_id} 已被拒絕 (by {admin_chat_id_str})")
        
        # 向用戶確認按鈕已點擊（Telegram 的 "popover" 提示）
        if callback_query_id:
            _answer_callback_query(callback_query_id, "✅ 已處理")
    
    except Exception as e:
        logger.error(f"_handle_callback_query 異常: {e}", exc_info=True)


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
    
    # 載入已批准的 Chat ID
    _load_approved_ids()
    
    # 管理員 Chat ID 自動批准
    with _approval_lock:
        _APPROVED_IDS.add(str(C.TG_ADMIN_CHAT_ID))
        _save_approved_ids()
    
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
        update: {"update_id": ..., "message": {"text": "...", "chat": {"id": ...}}} or
                {"update_id": ..., "callback_query": {"id": ..., "from": {"id": ...}, "data": "..."}}
    """
    try:
        # 檢查是否為 callback_query（按鈕點擊）
        if "callback_query" in update:
            callback_query = update.get("callback_query", {})
            _handle_callback_query(callback_query)
            return
        
        # 處理文本訊息
        msg = update.get("message", {})
        if not msg:
            return
        
        msg_text = msg.get("text", "").strip()
        chat_id = msg.get("chat", {}).get("id")
        
        if not msg_text or chat_id is None:
            return
        
        chat_id_str = str(chat_id)
        
        # 提取指令（第一個 '/' 後的單詞）
        cmd_parts = msg_text.split()
        cmd = cmd_parts[0].lower()
        args = " ".join(cmd_parts[1:]) if len(cmd_parts) > 1 else ""
        
        logger.info(f"📨 收到指令: {cmd} (Chat ID: {chat_id_str})")
        
        # 檢查是否為管理員
        is_admin = _is_admin(chat_id_str)
        
        # 管理員指令（無需審批）
        if is_admin:
            if cmd == "/approve":
                reply = _handle_approve_command(chat_id_str, args)
                send_message(reply, chat_id=chat_id_str)
                return
            elif cmd == "/deny":
                reply = _handle_deny_command(chat_id_str, args)
                send_message(reply, chat_id=chat_id_str)
                return
            elif cmd == "/admin_list":
                reply = _handle_admin_list_command(chat_id_str)
                send_message(reply, chat_id=chat_id_str)
                return
        
        # 非管理員：檢查是否已批准
        if not is_admin and not _is_approved(chat_id_str):
            # 發送批准請求給管理員
            _request_approval(chat_id_str)
            reply = "⏳ 你的訪問請求已發送給管理員，請等待批准"
            send_message(reply, chat_id=chat_id_str)
            logger.info(f"⏳ Chat ID {chat_id_str} 未批准，已發送審批請求")
            return
        
        # 公共指令
        if cmd == "/market":
            reply = _handle_market_command(chat_id_str)
        elif cmd == "/help":
            reply = _handle_help_command()
        else:
            reply = _handle_unknown_command(msg_text)
        
        # 傳送回覆
        success = send_message(reply, chat_id=chat_id_str, parse_mode="HTML")
        
        if success:
            logger.info(f"✅ 回覆已傳送 (Chat ID: {chat_id_str})")
        else:
            logger.warning(f"⚠️ 回覆傳送失敗 (Chat ID: {chat_id_str})")
    
    except Exception as e:
        logger.error(f"_process_update 異常: {e}", exc_info=True)


def stop_polling():
    """停止 Polling 迴圈"""
    _polling_stop_event.set()
    logger.info("Telegram Polling 已標記為停止")
