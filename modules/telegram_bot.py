"""
modules/telegram_bot.py
──────────────────────────
Telegram Bot Polling Integration + 管理員審批系統

支援的指令（公共）：
  /market              — 評估市場環境 (regime, breadth, sectors)
  /watchlist           — 顯示觀察清單 (A/B/C 分級)
  /positions           — 顯示現有持倉
  /analyze <ticker>    — SEPA 深度分析 (BUY/WATCH/AVOID)
  /qm <ticker>         — QM Qullamaggie 6★ 評級
  /ml <ticker>         — ML Martin Luk 7★ 評級
  /scan                — SEPA 全市場掃描 (背景執行)
  /qm_scan             — QM 全市場掃描 (背景執行)
  /ml_scan             — ML 全市場掃描 (背景執行)
  /help                — 顯示幫助訊息

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

# Active scan tracking (prevent concurrent scans per user)
_active_scans: Dict[str, str] = {}  # {chat_id: scan_type}
_active_scans_lock = threading.Lock()

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


def register_bot_commands():
    """
    在 Telegram 中註冊 Bot 命令菜單
    此功能使用戶在輸入 / 時會看到可用的命令列表
    """
    try:
        url = _build_url("setMyCommands")
        commands = [
            {"command": "menu", "description": "打開 Mini App 菜單"},
            {"command": "market", "description": "評估市場環境"},
            {"command": "watchlist", "description": "顯示觀察清單"},
            {"command": "positions", "description": "顯示現有持倉"},
            {"command": "analyze", "description": "SEPA 深度分析 <股票代碼>"},
            {"command": "qm", "description": "Qullamaggie 6★ 評級 <股票代碼>"},
            {"command": "ml", "description": "Martin Luk 7★ 評級 <股票代碼>"},
            {"command": "scan", "description": "SEPA 全市場掃描"},
            {"command": "qm_scan", "description": "QM 全市場掃描"},
            {"command": "ml_scan", "description": "ML 全市場掃描"},
            {"command": "help", "description": "顯示幫助訊息"},
        ]
        
        data = {"commands": commands}
        resp = requests.post(url, json=data, timeout=_TG_REQUEST_TIMEOUT)
        result = resp.json()
        
        if result.get("ok"):
            logger.info(f"✅ 已註冊 {len(commands)} 個 Telegram 命令")
            return True
        else:
            logger.warning(f"setMyCommands 失敗: {result.get('description', 'unknown')}")
            return False
    except Exception as e:
        logger.error(f"register_bot_commands 失敗: {e}", exc_info=False)
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


# ═══════════════════════════════════════════════════════════════════════════════
# Feature Command Handlers
# ═══════════════════════════════════════════════════════════════════════════════

def _format_stars(stars: float) -> str:
    """Convert float star rating to ⭐ display."""
    if stars <= 0:
        return "☆"
    full = int(stars)
    half = "½" if (stars - full) >= 0.5 else ""
    return "⭐" * full + half


def _format_setup_type(setup_type) -> str:
    """Extract readable setup name from setup_type (str or dict)."""
    if not setup_type:
        return "N/A"
    if isinstance(setup_type, dict):
        primary = setup_type.get("primary_setup", "N/A")
        confidence = setup_type.get("confidence", 0)
        if confidence:
            return f"{primary} (信心度: {confidence:.0%})"
        return primary
    return str(setup_type)


def _chunk_message(text: str, max_len: int = 4000) -> List[str]:
    """Split long message into chunks for Telegram (4096 char limit)."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


def _send_chunked(text: str, chat_id: str, parse_mode: str = "HTML"):
    """Send a potentially long message in chunks."""
    for chunk in _chunk_message(text):
        send_message(chunk, chat_id=chat_id, parse_mode=parse_mode)


def _handle_watchlist_command(chat_id: str) -> str:
    """
    /watchlist 指令 — 顯示觀察清單 (A/B/C 分級)
    """
    try:
        from modules.watchlist import _load
        data = _load()

        grade_labels = {"A": "🏆 Grade A", "B": "🥈 Grade B", "C": "🥉 Grade C"}
        lines = ["<b>📋 觀察清單 (Watchlist)</b>"]

        total = 0
        for grade in ["A", "B", "C"]:
            tickers_dict = data.get(grade, {})
            count = len(tickers_dict)
            total += count

            lines.append(f"\n<b>{grade_labels[grade]} ({count}):</b>")

            if not tickers_dict:
                lines.append("  (空)")
            else:
                for ticker, info in tickers_dict.items():
                    if isinstance(info, dict):
                        rs = info.get("rs_rank", 0)
                        vcp = info.get("vcp_grade", "?")
                        pivot = info.get("pivot", 0)
                        price = info.get("price", 0)
                        added = info.get("added_date", "?")
                        pivot_str = f" | Pivot: ${pivot:.2f}" if pivot else ""
                        price_str = f" | 現價: ${price:.2f}" if price else ""
                        lines.append(
                            f"  • <code>{ticker}</code> RS:{int(rs)} VCP:{vcp}"
                            f"{pivot_str}{price_str} (加入: {added})"
                        )
                    else:
                        lines.append(f"  • <code>{ticker}</code>")

        lines.append(f"\n<i>共 {total} 只股票</i>")
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"_handle_watchlist_command 失敗: {e}", exc_info=True)
        return f"❌ 載入觀察清單失敗:\n{str(e)[:100]}"


def _handle_positions_command(chat_id: str) -> str:
    """
    /positions 指令 — 顯示持倉
    """
    try:
        from modules.position_monitor import _load
        data = _load()
        positions = data.get("positions", {})

        if not positions:
            return "💼 <b>持倉</b>\n\n無持倉記錄"

        lines = [f"<b>💼 持倉 ({len(positions)} 個)</b>"]

        for ticker, pos in positions.items():
            if not isinstance(pos, dict):
                continue
            buy_price = pos.get("buy_price", 0)
            shares = pos.get("shares", 0)
            stop_loss = pos.get("stop_loss", 0)
            target = pos.get("target", 0)
            rr = pos.get("rr", 0)
            entry_date = pos.get("entry_date", "?")

            lines.append(f"\n<b>📌 {ticker}</b>")
            lines.append(f"  入場: ${buy_price:.2f} × {shares} 股 ({entry_date})")
            if stop_loss:
                stop_pct = (buy_price - stop_loss) / buy_price * 100 if buy_price else 0
                lines.append(f"  止損: ${stop_loss:.2f} (-{stop_pct:.1f}%)")
            if target:
                lines.append(f"  目標: ${target:.2f} | R:R {rr:.1f}:1")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"_handle_positions_command 失敗: {e}", exc_info=True)
        return f"❌ 載入持倉失敗:\n{str(e)[:100]}"


def _handle_analyze_command(chat_id: str, ticker: str):
    """
    /analyze <ticker> 指令 — SEPA 深度分析 (非同步背景執行)
    
    支持兩種交付方式：
    1. 傳統文字回應 (長)
    2. Telegram Mini App (Rich UI)
    """
    ticker = ticker.upper().strip()
    if not ticker:
        send_message("❌ 用法: /analyze &lt;ticker&gt;\n例如: /analyze NVDA", chat_id=chat_id)
        return

    # 嘗試發送 Mini App 按鈕（需要用戶批准）
    # webAppInfo 格式要求 url 參數指向完整 HTTPS URL
    try:
        mini_app_url = f"https://sepa-stocklab.example.com/tg/app?ticker={ticker}"  # 替換為實際 URL
        reply_markup = {
            "inline_keyboard": [
                [
                    {
                        "text": "📊 在 Mini App 中分析",
                        "web_app": {
                            "url": mini_app_url
                        }
                    }
                ]
            ]
        }
        send_message(
            f"📊 分析 <code>{ticker}</code>...\n\n點擊下方按鈕以在 Mini App 中查看詳細分析。",
            chat_id=chat_id, parse_mode="HTML", reply_markup=reply_markup
        )
    except Exception as e:
        logger.debug(f"Mini App 按鈕發送失敗 (降級到文字): {e}")
        # 降級：發送傳統文字分析請求
        send_message(
            f"⏳ 正在進行 SEPA 分析: <code>{ticker}</code>...\n(約需 30-60 秒)",
            chat_id=chat_id, parse_mode="HTML"
        )

    def _run():
        try:
            from modules.stock_analyzer import analyze
            result = analyze(ticker, print_report=False)

            if result.get("error"):
                send_message(f"❌ 分析失敗: {result['error']}", chat_id=chat_id)
                return

            rec = result.get("recommendation", {})
            signal = rec.get("signal", "N/A")
            signal_emoji = "✅" if signal == "BUY" else "👀" if signal in ("WATCH", "MONITOR") else "❌"
            scores = result.get("sepa_scores", {})
            total_score = scores.get("total_score", 0)
            tt = result.get("trend_template", {})
            tt_passed = sum(tt.get("checks", {}).values())
            vcp = result.get("vcp", {})
            pos = result.get("position", {})

            text = (
                f"<b>📊 SEPA 分析: {ticker}</b>\n\n"
                f"{signal_emoji} <b>訊號: {signal}</b>\n"
                f"<i>{rec.get('reason', '')}</i>\n\n"
                f"<b>📈 SEPA 總分: {total_score:.0f}/100</b>\n"
                f"趨勢:{scores.get('trend_score',0):>3} | 基本面:{scores.get('fundamental_score',0):>3} | "
                f"催化劑:{scores.get('catalyst_score',0):>3}\n"
                f"入場:{scores.get('entry_score',0):>3} | 風險回報:{scores.get('rr_score',0):>3}\n\n"
                f"<b>趨勢模板 (TT):</b> {tt_passed}/10 通過\n"
                f"<b>VCP 評級:</b> {vcp.get('grade','?')} ({vcp.get('vcp_score',0)}/100)\n\n"
                f"<b>💰 入場計劃:</b>\n"
                f"入場: ${pos.get('entry_price',0):.2f} | 止損: ${pos.get('stop_price',0):.2f} "
                f"(-{pos.get('stop_pct',0):.1f}%)\n"
                f"目標: ${pos.get('target_price',0):.2f} | R:R {pos.get('rr_ratio',0):.1f}:1\n"
                f"倉位: {pos.get('shares',0):,} 股 = ${pos.get('position_value',0):,.0f} "
                f"({pos.get('position_pct',0):.1f}%)\n\n"
                f"<b>現價:</b> ${result.get('price',0):.2f} | <b>RS Rank:</b> {result.get('rs_rank',0):.0f}/99"
            )
            send_message(text, chat_id=chat_id, parse_mode="HTML")
            logger.info(f"✅ SEPA 分析完成: {ticker}")
        except Exception as e:
            logger.error(f"_handle_analyze_command 背景執行失敗: {e}", exc_info=True)
            send_message(f"❌ SEPA 分析失敗 ({ticker}):\n{str(e)[:100]}", chat_id=chat_id)

    threading.Thread(target=_run, daemon=True).start()


def _handle_qm_command(chat_id: str, ticker: str):
    """
    /qm <ticker> 指令 — QM Qullamaggie 6★ 分析 (非同步背景執行)
    """
    ticker = ticker.upper().strip()
    if not ticker:
        send_message("❌ 用法: /qm &lt;ticker&gt;\n例如: /qm NVDA", chat_id=chat_id)
        return

    send_message(
        f"⏳ 正在進行 QM 分析: <code>{ticker}</code>...\n(約需 30-60 秒)",
        chat_id=chat_id, parse_mode="HTML"
    )

    def _run():
        try:
            from modules.qm_analyzer import analyze_qm
            result = analyze_qm(ticker, print_report=False)

            if result.get("error"):
                send_message(f"❌ QM 分析失敗: {result['error']}", chat_id=chat_id)
                return

            stars = result.get("capped_stars", result.get("stars", 0))
            rec = result.get("recommendation", "PASS")
            rec_zh = result.get("recommendation_zh", "")
            star_display = _format_stars(stars)
            rec_emoji = "✅" if "BUY" in rec else "👀" if "WATCH" in rec else "❌"

            dim_labels = {
                "A": "A. 動能品質", "B": "B. ADR 波幅",
                "C": "C. 整固形態", "D": "D. 均線排列",
                "E": "E. 股票類型", "F": "F. 市場時機"
            }
            dims_lines = []
            for k, label in dim_labels.items():
                d = result.get("dim_scores", {}).get(k, {})
                if d:
                    dims_lines.append(f"  {label}: {d.get('score',0)}/{d.get('max_score',10)}")

            trade_plan = result.get("trade_plan", {})
            plan_text = ""
            if isinstance(trade_plan, dict) and trade_plan.get("entry"):
                target = trade_plan.get("target", 0)
                target_str = f"${target:.2f}" if target else "N/A"
                pos_pct = trade_plan.get("position_size_pct", 0)
                plan_text = (
                    f"\n\n<b>💰 交易計劃:</b>\n"
                    f"入場: ${trade_plan.get('entry',0):.2f} | 止損: ${trade_plan.get('stop',0):.2f} | "
                    f"目標: {target_str}\n"
                    f"建議倉位: {pos_pct:.0f}%" if pos_pct else "建議倉位: — (評分不足)"
                )

            text = (
                f"<b>⭐ QM 分析: {ticker}</b>\n\n"
                f"{star_display} <b>{stars:.1f}★</b>\n"
                f"{rec_emoji} <b>{rec}</b> {rec_zh}\n\n"
                f"<b>6 維度評分:</b>\n" + "\n".join(dims_lines) +
                f"\n\n<b>形態類型:</b> {_format_setup_type(result.get('setup_type'))}"
                + plan_text
            )
            send_message(text, chat_id=chat_id, parse_mode="HTML")
            logger.info(f"✅ QM 分析完成: {ticker}")
        except Exception as e:
            logger.error(f"_handle_qm_command 背景執行失敗: {e}", exc_info=True)
            send_message(f"❌ QM 分析失敗 ({ticker}):\n{str(e)[:100]}", chat_id=chat_id)

    threading.Thread(target=_run, daemon=True).start()


def _handle_ml_command(chat_id: str, ticker: str):
    """
    /ml <ticker> 指令 — ML Martin Luk 7★ 分析 (非同步背景執行)
    """
    ticker = ticker.upper().strip()
    if not ticker:
        send_message("❌ 用法: /ml &lt;ticker&gt;\n例如: /ml NVDA", chat_id=chat_id)
        return

    send_message(
        f"⏳ 正在進行 ML 分析: <code>{ticker}</code>...\n(約需 30-60 秒)",
        chat_id=chat_id, parse_mode="HTML"
    )

    def _run():
        try:
            from modules.ml_analyzer import analyze_ml
            result = analyze_ml(ticker, print_report=False)

            if result.get("error"):
                send_message(f"❌ ML 分析失敗: {result['error']}", chat_id=chat_id)
                return

            stars = result.get("capped_stars", result.get("stars", 0))
            rec = result.get("recommendation", "PASS")
            rec_zh = result.get("recommendation_zh", "")
            star_display = _format_stars(stars)
            rec_emoji = "✅" if "BUY" in rec else "👀" if "WATCH" in rec else "❌"

            dim_labels = {
                "A": "A. EMA 結構", "B": "B. 回調品質",
                "C": "C. AVWAP 共鳴", "D": "D. 成交量形態",
                "E": "E. 風險回報", "F": "F. 相對強度",
                "G": "G. 市場環境"
            }
            dims_lines = []
            for k, label in dim_labels.items():
                d = result.get("dim_scores", {}).get(k, {})
                if d:
                    dims_lines.append(f"  {label}: {d.get('score',0)}/{d.get('max_score',10)}")

            trade_plan = result.get("trade_plan", {})
            plan_text = ""
            if isinstance(trade_plan, dict) and trade_plan.get("entry"):
                target = trade_plan.get("target", 0)
                target_str = f"${target:.2f}" if target else "N/A"
                pos_pct = trade_plan.get("position_size_pct", 0)
                pos_str = f"{pos_pct:.0f}% (最大止損 2.5%)" if pos_pct else "— (評分不足)"
                plan_text = (
                    f"\n\n<b>💰 交易計劃:</b>\n"
                    f"入場: ${trade_plan.get('entry',0):.2f} | 止損: ${trade_plan.get('stop',0):.2f} | "
                    f"目標: {target_str}\n"
                    f"建議倉位: {pos_str}"
                )

            text = (
                f"<b>⭐ ML 分析: {ticker}</b>\n\n"
                f"{star_display} <b>{stars:.1f}★</b>\n"
                f"{rec_emoji} <b>{rec}</b> {rec_zh}\n\n"
                f"<b>7 維度評分:</b>\n" + "\n".join(dims_lines) +
                f"\n\n<b>形態類型:</b> {_format_setup_type(result.get('setup_type'))}"
                + plan_text
            )
            send_message(text, chat_id=chat_id, parse_mode="HTML")
            logger.info(f"✅ ML 分析完成: {ticker}")
        except Exception as e:
            logger.error(f"_handle_ml_command 背景執行失敗: {e}", exc_info=True)
            send_message(f"❌ ML 分析失敗 ({ticker}):\n{str(e)[:100]}", chat_id=chat_id)

    threading.Thread(target=_run, daemon=True).start()


def _handle_scan_command(chat_id: str, scan_type: str):
    """
    /scan | /qm_scan | /ml_scan 指令 — 後台全市場掃描 (非同步，需數分鐘)

    Args:
        scan_type: "SEPA" | "QM" | "ML"
    """
    with _active_scans_lock:
        if chat_id in _active_scans:
            existing = _active_scans[chat_id]
            send_message(f"⚠️ 掃描進行中 ({existing})\n請等待完成後再啟動新掃描", chat_id=chat_id)
            return
        _active_scans[chat_id] = scan_type

    scan_names = {"SEPA": "SEPA (Minervini)", "QM": "QM (Qullamaggie)", "ML": "ML (Martin Luk)"}
    scan_name = scan_names.get(scan_type, scan_type)

    send_message(
        f"🚀 <b>{scan_name} 掃描已開始</b>\n"
        f"⏳ 此操作需要 5-30 分鐘，完成後會通知\n"
        f"<i>掃描美股全市場...</i>",
        chat_id=chat_id, parse_mode="HTML"
    )

    def _run():
        import time as _time
        start_ts = _time.time()
        try:
            if scan_type == "SEPA":
                from modules.screener import run_scan
                results = run_scan(verbose=False)
            elif scan_type == "QM":
                from modules.qm_screener import run_qm_scan
                results = run_qm_scan(verbose=False)
            elif scan_type == "ML":
                from modules.ml_screener import run_ml_scan
                results = run_ml_scan(verbose=False)
            else:
                results = None

            elapsed = _time.time() - start_ts
            mins, secs = int(elapsed // 60), int(elapsed % 60)
            time_str = f"{mins}m {secs}s" if mins else f"{secs}s"

            if results is None or (hasattr(results, '__len__') and len(results) == 0):
                send_message(
                    f"✅ <b>{scan_name} 掃描完成</b> ({time_str})\n\n未找到符合條件的股票",
                    chat_id=chat_id, parse_mode="HTML"
                )
                return

            import pandas as pd
            if isinstance(results, pd.DataFrame):
                count = len(results)
                top_n = min(15, count)

                ticker_col = "ticker" if "ticker" in results.columns else (
                    results.columns[0] if len(results.columns) > 0 else None
                )
                score_col = next(
                    (c for c in ["sepa_score", "total_score", "score", "capped_stars", "stars"]
                     if c in results.columns), None
                )

                rows = []
                for i, (_, row) in enumerate(results.head(top_n).iterrows()):
                    t = row.get(ticker_col, "?") if ticker_col else "?"
                    s = f" — {score_col}: {row.get(score_col,0):.1f}" if score_col else ""
                    rows.append(f"  {i+1}. <code>{t}</code>{s}")

                full_text = (
                    f"✅ <b>{scan_name} 掃描完成</b> ({time_str})\n"
                    f"找到 <b>{count}</b> 只符合條件的股票\n\n"
                    f"<b>Top {top_n} 結果:</b>\n"
                    + "\n".join(rows)
                )
                _send_chunked(full_text, chat_id=chat_id, parse_mode="HTML")
            else:
                send_message(
                    f"✅ <b>{scan_name} 掃描完成</b> ({time_str})",
                    chat_id=chat_id, parse_mode="HTML"
                )

            logger.info(f"✅ {scan_type} 掃描完成 (Chat ID: {chat_id}, {time_str})")

        except Exception as e:
            logger.error(f"_handle_scan_command 背景執行失敗 [{scan_type}]: {e}", exc_info=True)
            send_message(f"❌ {scan_name} 掃描失敗:\n{str(e)[:150]}", chat_id=chat_id)

        finally:
            with _active_scans_lock:
                _active_scans.pop(chat_id, None)

    threading.Thread(target=_run, daemon=True).start()


def _handle_help_command() -> str:
    """
    /help 指令 — 顯示幫助

    Returns:
        幫助文字 (HTML 格式)
    """
    text = """<b>📋 SEPA StockLab — 可用指令</b>

<b>🎯 功能菜單:</b>
<b>/menu</b> — 打開完整功能菜單（推薦使用）

<b>📊 市場 &amp; 資訊:</b>
<b>/market</b> — 評估市場環境 (Regime, Breadth, Sectors)
<b>/watchlist</b> — 顯示觀察清單 (A/B/C 分級)
<b>/positions</b> — 顯示現有持倉

<b>🔍 單股分析 (需 30-60 秒):</b>
<b>/analyze &lt;ticker&gt;</b> — SEPA 深度分析 (BUY/WATCH/AVOID)
<b>/qm &lt;ticker&gt;</b> — QM Qullamaggie 6★ 評級
<b>/ml &lt;ticker&gt;</b> — ML Martin Luk 7★ 評級

<b>🚀 全市場掃描 (需 5-30 分鐘，完成後通知):</b>
<b>/scan</b> — SEPA (Minervini) 掃描
<b>/qm_scan</b> — QM (Qullamaggie) 掃描
<b>/ml_scan</b> — ML (Martin Luk) 掃描

<b>ℹ️ 其他:</b>
<b>/mini_app</b> — 打開 Qullamaggie Mini App 📱
<b>/help</b> — 顯示此幫助訊息

<i>SEPA StockLab Telegram Bot | 本地運行 (Polling mode)</i>"""
    return text.strip()


def _handle_unknown_command(text: str) -> str:
    """處理未知指令"""
    return f"❌ 未知指令: <code>{text[:50]}</code>\n輸入 /help 查看可用指令"


def _handle_menu_command(chat_id: str):
    """
    /menu 指令 — 打開 Mini App 菜單（主菜單）
    發送web_app按鈕連接到tg_menu頁面
    """
    mini_app_url = getattr(C, 'TG_MINI_APP_BASE_URL', 'http://localhost:5000') + '/tg/menu'
    reply_markup = {
        "inline_keyboard": [[{
            "text": "📊 打開功能菜單",
            "web_app": {"url": mini_app_url}
        }]]
    }
    msg = "<b>🎯 SEPA StockLab 功能菜單</b>\n\n點擊下方按鈕打開完整菜單"
    send_message(msg, chat_id=chat_id, reply_markup=reply_markup)
    logger.info(f"已發送菜單按鈕 (Chat ID: {chat_id})")


def _handle_mini_app_command(chat_id: str):
    """
    /mini_app 指令 — 打開 Qullamaggie Mini App（發送web_app按鈕）
    """
    mini_app_url = getattr(C, 'TG_MINI_APP_BASE_URL', 'http://localhost:5000') + '/tg/app'
    
    msg = """<b>📱 Qullamaggie Mini App</b>

點擊下方按鈕打開小程式進行個股分析"""
    
    reply_markup = {
        "inline_keyboard": [
            [
                {
                    "text": "📊 打開Mini App",
                    "web_app": {
                        "url": mini_app_url
                    }
                }
            ]
        ]
    }
    
    send_message(msg, chat_id=chat_id, reply_markup=reply_markup)
    logger.info(f"✅ Mini App 按鈕已發送 (Chat ID: {chat_id})")


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
    global _polling_stop_event
    
    if not _validate_config():
        logger.error("Telegram 設定無效，無法啟動 Polling")
        return
    
    # 重置停止事件，允許循環執行（重要！）
    _polling_stop_event.clear()
    
    # 載入已批准的 Chat ID
    _load_approved_ids()
    
    # 管理員 Chat ID 自動批准
    with _approval_lock:
        _APPROVED_IDS.add(str(C.TG_ADMIN_CHAT_ID))
        _save_approved_ids()
    
    # 註冊 Telegram 命令菜單
    register_bot_commands()
    
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
        # ── 非同步指令（背景執行，函數內部自行 send_message) ──────────────────
        if cmd == "/analyze":
            _handle_analyze_command(chat_id_str, args)
            return
        elif cmd == "/qm":
            _handle_qm_command(chat_id_str, args)
            return
        elif cmd == "/ml":
            _handle_ml_command(chat_id_str, args)
            return
        elif cmd == "/scan":
            _handle_scan_command(chat_id_str, "SEPA")
            return
        elif cmd == "/qm_scan":
            _handle_scan_command(chat_id_str, "QM")
            return
        elif cmd == "/ml_scan":
            _handle_scan_command(chat_id_str, "ML")
            return

        # 同步指令（即時回覆) ───────────────────────────────────────────────
        if cmd == "/market":
            reply = _handle_market_command(chat_id_str)
        elif cmd == "/watchlist":
            reply = _handle_watchlist_command(chat_id_str)
        elif cmd == "/positions":
            reply = _handle_positions_command(chat_id_str)
        elif cmd == "/menu":
            # 主菜單命令（發送web_app按鈕）
            _handle_menu_command(chat_id_str)
            return
        elif cmd == "/mini_app":
            # Mini App 命令（發送web_app按鈕）
            _handle_mini_app_command(chat_id_str)
            return
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
