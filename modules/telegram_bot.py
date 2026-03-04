"""
modules/telegram_bot.py
──────────────────────────
Telegram Bot Polling Integration + 管理員審批系統

支援的指令（公共）：
  /market              — 評估市場環境 (regime, breadth, sectors)
  /dashboard           — 儀表板概覽 (市場、持倉、觀察清單)
  /watchlist           — 顯示觀察清單 (A/B/C 分級)
  /positions, /position— 顯示現有持倉
  /account             — 顯示帳戶信息和統計
  /analyze             — SEPA 深度分析 (會詢問股票代碼)
  /qm                  — QM Qullamaggie 6★ 評級 (會詢問股票代碼)
  /ml                  — ML Martin Luk 7★ 評級 (會詢問股票代碼)
  /trade               — 新建交易 (買入/賣出，逐步引導)
  /help                — 顯示幫助訊息

支援的指令（管理員專用）：
  /scan                — SEPA (Minervini) 掃描 (背景執行, 5-30 分鐘)
  /qm_scan             — QM (Qullamaggie) 掃描 (背景執行, 5-30 分鐘)
  /ml_scan             — ML (Martin Luk) 掃描 (背景執行, 5-30 分鐘)
  /approve <chat_id>   — 批准新用戶
  /deny <chat_id>      — 拒絕新用戶
  /admin_list          — 查看待批准列表

交互式指令流程：
  /analyze, /qm, /ml:
    1. 用戶輸入 /analyze（無參數）
    2. Bot 詢問: 分析哪個股票?
    3. 用戶輸入股票代碼
    4. 後台執行分析

  /trade:
    1. 用戶輸入 /trade
    2. Bot 展示按鈕: 買入 / 賣出
    3. 用戶選擇
    4. Bot 一步步詢問: 股票代碼 → 入場價 → 數量 → 止損價
    5. Bot 確認並計算 R:R

新用戶流程：
  1. 新用戶輸入任何指令
  2. Bot 檢查白名單，發現未批准
  3. Bot 發送審批請求給管理員
  4. 管理員點擊 ✅ 批准 / ❌ 拒絕 按鈕
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

# User interaction state machine (for multi-step commands)
# {chat_id: {"state": "waiting_for_analyze_ticker", "data": {...}}}
_user_states: Dict[str, Dict] = {}
_user_states_lock = threading.Lock()

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
    /analyze 指令 — SEPA 深度分析 (非同步背景執行)
    
    若 ticker 為空：詢問用戶要分析哪個股票，並進入狀態機制
    若 ticker 有值：直接進行分析
    """
    ticker = ticker.upper().strip()
    
    # 若無 ticker，進入狀態機制詢問用戶
    if not ticker:
        with _user_states_lock:
            _user_states[chat_id] = {
                "state": "waiting_for_analyze_ticker",
                "data": {}
            }
        send_message("🔍 請輸入要進行 SEPA 分析的股票代碼 (例如: NVDA)", chat_id=chat_id)
        return

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
    /qm 指令 — QM Qullamaggie 6★ 分析 (非同步背景執行)
    
    若 ticker 為空：詢問用戶要分析哪個股票，並進入狀態機制
    若 ticker 有值：直接進行分析
    """
    ticker = ticker.upper().strip()
    
    # 若無 ticker，進入狀態機制詢問用戶
    if not ticker:
        with _user_states_lock:
            _user_states[chat_id] = {
                "state": "waiting_for_qm_ticker",
                "data": {}
            }
        send_message("🔍 請輸入要進行 QM 分析的股票代碼 (例如: NVDA)", chat_id=chat_id)
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
    /ml 指令 — ML Martin Luk 7★ 分析 (非同步背景執行)
    
    若 ticker 為空：詢問用戶要分析哪個股票，並進入狀態機制
    若 ticker 有值：直接進行分析
    """
    ticker = ticker.upper().strip()
    
    # 若無 ticker，進入狀態機制詢問用戶
    if not ticker:
        with _user_states_lock:
            _user_states[chat_id] = {
                "state": "waiting_for_ml_ticker",
                "data": {}
            }
        send_message("🔍 請輸入要進行 ML 分析的股票代碼 (例如: NVDA)", chat_id=chat_id)
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

<b>📊 市場 &amp; 資訊:</b>
<b>/market</b> — 評估市場環境 (Regime, Breadth, Sectors)
<b>/dashboard</b> — 儀表板概覽 (市場、持倉、觀察清單)
<b>/watchlist</b> — 顯示觀察清單 (A/B/C 分級)
<b>/positions</b> — 顯示現有持倉
<b>/position</b> — 別名於 /positions
<b>/account</b> — 顯示帳戶信息和統計

<b>🔍 單股分析 (需 30-60 秒):</b>
<b>/analyze</b> — SEPA 深度分析 (會詢問股票代碼)
<b>/qm</b> — QM Qullamaggie 6★ 評級 (會詢問股票代碼)
<b>/ml</b> — ML Martin Luk 7★ 評級 (會詢問股票代碼)

<b>💼 交易:</b>
<b>/trade</b> — 新建交易 (買入/賣出，逐步引導)

<b>🚀 全市場掃描 (管理員專用，需 5-30 分鐘):</b>
<b>/scan</b> — SEPA (Minervini) 掃描
<b>/qm_scan</b> — QM (Qullamaggie) 掃描
<b>/ml_scan</b> — ML (Martin Luk) 掃描

<b>ℹ️ 其他:</b>
<b>/help</b> — 顯示此幫助訊息

<i>SEPA StockLab Telegram Bot | 本地運行 (Polling mode)</i>"""
    return text.strip()


def _handle_unknown_command(text: str) -> str:
    """處理未知指令"""
    return f"❌ 未知指令: <code>{text[:50]}</code>\n輸入 /help 查看可用指令"


# ═══════════════════════════════════════════════════════════════════════════════
# New Command Handlers: Dashboard, Position, Account, Trade
# ═══════════════════════════════════════════════════════════════════════════════

def _handle_dashboard_command(chat_id: str) -> str:
    """
    /dashboard 指令 — 儀表板概覽
    
    結合市場狀況、持倉摘要、觀察清單統計
    """
    try:
        lines = ["<b>📊 Telegram Bot 儀表板</b>\n"]
        
        # 市場狀況
        try:
            from modules.market_env import assess
            result = assess(verbose=False)
            regime = result.get("regime", "UNKNOWN")
            breadth = result.get("breadth_pct", 0)
            lines.append(f"<b>📈 市場:</b> {regime} | Breadth: {breadth:.0f}%\n")
        except Exception as e:
            logger.warning(f"無法載入市場環境: {e}")
            lines.append("<b>📈 市場:</b> (無法載入)\n")
        
        # 持倉摘要
        try:
            from modules.position_monitor import _load
            pos_data = _load()
            positions = pos_data.get("positions", {})
            pos_count = len(positions)
            if positions:
                total_val = sum(p.get("position_value", 0) for p in positions.values())
                lines.append(f"<b>💼 持倉:</b> {pos_count} 個 | 總值: ${total_val:,.0f}\n")
            else:
                lines.append(f"<b>💼 持倉:</b> 無\n")
        except Exception as e:
            logger.warning(f"無法載入持倉: {e}")
            lines.append("<b>💼 持倉:</b> (無法載入)\n")
        
        # 觀察清單統計
        try:
            from modules.watchlist import _load
            wl_data = _load()
            total_wl = sum(len(wl_data.get(g, {})) for g in ["A", "B", "C"])
            if total_wl > 0:
                a_count = len(wl_data.get("A", {}))
                b_count = len(wl_data.get("B", {}))
                c_count = len(wl_data.get("C", {}))
                lines.append(f"<b>📋 觀察清單:</b> 共 {total_wl} | A:{a_count} B:{b_count} C:{c_count}")
            else:
                lines.append(f"<b>📋 觀察清單:</b> 無")
        except Exception as e:
            logger.warning(f"無法載入觀察清單: {e}")
            lines.append("<b>📋 觀察清單:</b> (無法載入)")
        
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"_handle_dashboard_command 失敗: {e}", exc_info=True)
        return f"❌ 儀表板載入失敗:\n{str(e)[:100]}"


def _handle_position_command(chat_id: str) -> str:
    """
    /position 指令 — 別名於 /positions
    """
    return _handle_positions_command(chat_id)


def _handle_account_command(chat_id: str) -> str:
    """
    /account 指令 — 顯示帳戶信息和統計
    """
    try:
        from modules.position_monitor import _load
        pos_data = _load()
        
        lines = ["<b>💰 帳戶信息</b>\n"]
        
        # 帳戶設置
        try:
            account_size = C.ACCOUNT_SIZE
            max_risk = C.MAX_RISK_PER_TRADE_PCT
            lines.append(f"<b>帳戶規模:</b> ${account_size:,.0f}\n")
            lines.append(f"<b>最大風險 (per trade):</b> {max_risk:.1f}%\n")
        except Exception as e:
            logger.warning(f"無法載入帳戶參數: {e}")
        
        # 持倉統計
        positions = pos_data.get("positions", {})
        if positions:
            pos_values = [p.get("position_value", 0) for p in positions.values()]
            total_pos_val = sum(pos_values)
            pos_pct = (total_pos_val / account_size * 100) if account_size else 0
            
            lines.append(f"\n<b>持倉統計:</b>")
            lines.append(f"  持倉數量: {len(positions)}")
            lines.append(f"  總投資額: ${total_pos_val:,.0f}")
            lines.append(f"  帳戶佔比: {pos_pct:.1f}%")
        else:
            lines.append(f"\n<b>持倉統計:</b> 無持倉")
        
        return "\n".join(lines)
    except Exception as e:
        logger.error(f"_handle_account_command 失敗: {e}", exc_info=True)
        return f"❌ 帳戶信息載入失敗:\n{str(e)[:100]}"


def _handle_trade_command(chat_id: str):
    """
    /trade 指令 — 新建交易（交互式流程）
    
    步驟:
    1. 檢查 IBKR 帳戶連接狀態
    2. 若已連接，詢問 buy / sell
    3. 根據選擇詢問後續信息 (股票代碼、數量、價格、止損等)
    4. 最後確認對話框確認是否提交
    """
    try:
        from modules.ibkr_client import is_ibkr_connected, get_status
        
        # 檢查 IBKR 連接狀態
        if not is_ibkr_connected():
            status = get_status()
            error_msg = status.get("last_error", "未知錯誤")
            send_message(
                f"❌ <b>IBKR 帳戶未連接</b>\n\n"
                f"連接狀態: <code>{status.get('state', 'DISCONNECTED')}</code>\n"
                f"錯誤信息: {error_msg}\n\n"
                f"請先連接 IBKR 帳戶後再進行交易",
                chat_id=chat_id,
                parse_mode="HTML"
            )
            logger.warning(f"用戶 {chat_id} 嘗試交易但 IBKR 未連接")
            return
        
        # 獲取帳戶詳細信息
        status = get_status()
        account = status.get("account", "Unknown")
        
        # 購買力：優先使用 BuyingPower，備選 cash，最後備選 nav
        buying_power = status.get("buying_power", 0)
        if buying_power == 0:
            buying_power = status.get("cash", 0)
        if buying_power == 0:
            buying_power = status.get("nav", 0)
        
        nav = status.get("nav", 0)
        
        # 初始化狀態
        with _user_states_lock:
            _user_states[chat_id] = {
                "state": "waiting_for_trade_action",
                "data": {
                    "account_id": account,
                    "buying_power": buying_power,
                    "nav": nav
                }
            }
        
        # 構造按鈕消息
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "📈 買入", "callback_data": "trade_buy"},
                    {"text": "📉 賣出", "callback_data": "trade_sell"}
                ]
            ]
        }
        
        trade_msg = (
            f"✅ <b>IBKR 已連接</b>\n\n"
            f"帳戶: <code>{account}</code>\n"
            f"淨值: ${nav:,.2f}\n"
            f"可用資金: ${buying_power:,.2f}\n\n"
            f"💼 <b>新建交易</b> — 請選擇交易方向:"
        )
        
        send_message(
            trade_msg,
            chat_id=chat_id,
            parse_mode="HTML",
            reply_markup=reply_markup
        )
        
        logger.info(f"✅ 用戶 {chat_id} 開始交易流程 (帳戶: {account}, 可用資金: ${buying_power:,.2f})")
        
    except Exception as e:
        logger.error(f"_handle_trade_command 失敗: {e}", exc_info=True)
        send_message(
            f"❌ 初始化交易失敗:\n{str(e)[:100]}",
            chat_id=chat_id
        )


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
        user_chat_id = callback_query.get("from", {}).get("id")
        callback_query_id = callback_query.get("id")
        
        if not user_chat_id:
            return
        
        user_chat_id_str = str(user_chat_id)
        
        # ── 交易按鈕處理（無需管理員身份） ──────────────────────────────────────
        if callback_data == "trade_buy":
            with _user_states_lock:
                _user_states[user_chat_id_str] = {
                    "state": "waiting_for_trade_ticker",
                    "data": {"action": "buy"}
                }
            send_message("📈 請輸入要買入的股票代碼 (例如: NVDA)", chat_id=user_chat_id_str)
            _answer_callback_query(callback_query_id, "✅ 買入模式")
            return
        
        elif callback_data == "trade_sell":
            with _user_states_lock:
                _user_states[user_chat_id_str] = {
                    "state": "waiting_for_trade_ticker",
                    "data": {"action": "sell"}
                }
            send_message("📉 請輸入要賣出的股票代碼 (例如: NVDA)", chat_id=user_chat_id_str)
            _answer_callback_query(callback_query_id, "✅ 賣出模式")
            return
        
        # ── 訂單類型選擇 ──────────────────────────────────────────────────────────
        elif callback_data == "order_type_mkt":
            # 市價單 - 直接跳過價格欄，進入止損詢問
            with _user_states_lock:
                _user_states[user_chat_id_str]["data"]["order_type"] = "MKT"
                # 市價單使用當前價格作為成交價
                current_price = _user_states[user_chat_id_str]["data"].get("current_price", 0)
                _user_states[user_chat_id_str]["data"]["price"] = current_price
                _user_states[user_chat_id_str]["state"] = "waiting_for_trade_stoploss"
            
            ticker = _user_states.get(user_chat_id_str, {}).get("data", {}).get("ticker", "?")
            quantity = _user_states.get(user_chat_id_str, {}).get("data", {}).get("quantity", 0)
            current_price = _user_states.get(user_chat_id_str, {}).get("data", {}).get("current_price", 0)
            
            send_message(
                f"🔄 <b>市價單</b>\n\n"
                f"股票: <code>{ticker}</code>\n"
                f"數量: {quantity} 股\n"
                f"成交價: ${current_price:.2f}\n\n"
                f"🛑 <b>請輸入止損價格</b> (例如: {current_price*0.95:.2f})\n"
                f"<i>或輸入 0 跳過止損設定</i>",
                chat_id=user_chat_id_str,
                parse_mode="HTML"
            )
            _answer_callback_query(callback_query_id, "✅ 市價單")
            return
        
        elif callback_data == "order_type_lmt":
            # 限價單 - 需要詢問價格
            with _user_states_lock:
                _user_states[user_chat_id_str]["data"]["order_type"] = "LMT"
                _user_states[user_chat_id_str]["state"] = "waiting_for_trade_price"
            
            ticker = _user_states.get(user_chat_id_str, {}).get("data", {}).get("ticker", "?")
            current_price = _user_states.get(user_chat_id_str, {}).get("data", {}).get("current_price", 0)
            
            send_message(
                f"💰 <b>限價單</b>\n\n"
                f"股票: <code>{ticker}</code>\n"
                f"當前價: ${current_price:.2f}\n\n"
                f"請輸入限價 (例如: {current_price*0.99:.2f})",
                chat_id=user_chat_id_str,
                parse_mode="HTML"
            )
            _answer_callback_query(callback_query_id, "✅ 限價單")
            return
        
        # ── 交易確認按鈕處理 ──────────────────────────────────────────────────────
        elif callback_data == "cancel_trade":
            # 用戶取消交易
            with _user_states_lock:
                _user_states.pop(user_chat_id_str, None)
            send_message("❌ 交易已取消", chat_id=user_chat_id_str)
            _answer_callback_query(callback_query_id, "交易已取消")
            return
        
        elif callback_data.startswith("confirm_trade_"):
            # 用戶確認交易，提交到 IBKR
            # 格式: confirm_trade_BUY_NVDA_100_150.25_140.00
            try:
                parts = callback_data.split("_")
                if len(parts) < 6:
                    send_message("❌ 交易數據格式錯誤", chat_id=user_chat_id_str)
                    _answer_callback_query(callback_query_id, "❌ 錯誤")
                    return
                
                action = parts[2]  # BUY / SELL
                ticker = parts[3]  # NVDA
                try:
                    qty = int(parts[4])
                    entry_price = float(parts[5])
                    stop_loss = float(parts[6])
                except (ValueError, IndexError):
                    send_message("❌ 交易參數解析失敗", chat_id=user_chat_id_str)
                    _answer_callback_query(callback_query_id, "❌ 錯誤")
                    return
                
                # 獲取完整的交易數據
                with _user_states_lock:
                    user_data = _user_states.get(user_chat_id_str, {}).get("data", {})
                    account_id = user_data.get("account_id", "Unknown")
                    nav = user_data.get("nav", 0)
                
                # 提交交易到 IBKR（後台執行）
                def _submit_trade():
                    try:
                        from modules.ibkr_client import place_order
                        
                        # 使用市價訂單
                        result = place_order(
                            ticker=ticker,
                            action=action,
                            qty=qty,
                            order_type="MKT"
                        )
                        
                        if result.get("success"):
                            order_id = result.get("order_id")
                            msg = f"""✅ <b>交易已提交</b>

<b>帳戶:</b> <code>{account_id}</code>
<b>訂單 ID:</b> <code>{order_id}</code>

<b>交易信息:</b>
<b>  類型:</b> {'📈 買入' if action == 'BUY' else '📉 賣出'}
<b>  股票:</b> <code>{ticker}</code>
<b>  數量:</b> {qty} 股
<b>  類型:</b> 市價訂單

<b>輔助信息:</b>
<b>  計劃入場:</b> ${entry_price:.2f}
<b>  計劃止損:</b> ${stop_loss:.2f}

<i>⏳ 訂單已發送到 IBKR，請查看帳戶確認成交情況</i>"""
                            send_message(msg, chat_id=user_chat_id_str, parse_mode="HTML")
                            logger.info(f"✅ 交易已提交: {ticker} {action} {qty} (Order ID: {order_id})")
                        else:
                            error_msg = result.get("message", "未知錯誤")
                            send_message(
                                f"❌ <b>交易提交失敗</b>\n\n{error_msg}",
                                chat_id=user_chat_id_str,
                                parse_mode="HTML"
                            )
                            logger.error(f"❌ 交易提交失敗: {error_msg}")
                    
                    except Exception as e:
                        logger.error(f"_submit_trade 失敗: {e}", exc_info=True)
                        send_message(
                            f"❌ 提交交易時發生錯誤:\n{str(e)[:100]}",
                            chat_id=user_chat_id_str
                        )
                    
                    # 清空用戶狀態
                    with _user_states_lock:
                        _user_states.pop(user_chat_id_str, None)
                
                # 在後台線程中執行交易提交
                send_message("⏳ 正在提交交易到 IBKR，請稍候...", chat_id=user_chat_id_str)
                threading.Thread(target=_submit_trade, daemon=True).start()
                
                _answer_callback_query(callback_query_id, "✅ 交易提交中...")
                
            except Exception as e:
                logger.error(f"確認交易失敗: {e}", exc_info=True)
                send_message(f"❌ 處理交易確認時出錯:\n{str(e)[:100]}", chat_id=user_chat_id_str)
                _answer_callback_query(callback_query_id, "❌ 錯誤")
            return
        
        # ── 管理員按鈕處理 ───────────────────────────────────────────────────────
        # 確認只有管理員才能點擊批准/拒絕按鈕
        if not _is_admin(user_chat_id_str):
            if callback_data.startswith(("approve_", "deny_")):
                logger.warning(f"🔒 非管理員嘗試使用按鈕: {user_chat_id_str}")
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
操作者: <code>{user_chat_id_str}</code>"""
                    send_message(msg_text, chat_id=user_chat_id_str)
                    
                    logger.info(f"✅ Chat ID {target_chat_id} 已被批准 (by {user_chat_id_str})")
                    
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
操作者: <code>{user_chat_id_str}</code>"""
            send_message(msg_text, chat_id=user_chat_id_str)
            
            # 移除待審記錄
            _PENDING_REQUESTS.pop(target_chat_id, None)
            
            logger.info(f"❌ Chat ID {target_chat_id} 已被拒絕 (by {user_chat_id_str})")
        
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
        
        # 檢查用戶是否處於等待輸入狀態（狀態機制）
        with _user_states_lock:
            user_state = _user_states.get(chat_id_str)
        
        if user_state:
            state_type = user_state.get("state")
            
            # 處理狀態機制中的輸入
            if state_type == "waiting_for_analyze_ticker":
                with _user_states_lock:
                    _user_states.pop(chat_id_str, None)
                _handle_analyze_command(chat_id_str, msg_text)
                return
            elif state_type == "waiting_for_qm_ticker":
                with _user_states_lock:
                    _user_states.pop(chat_id_str, None)
                _handle_qm_command(chat_id_str, msg_text)
                return
            elif state_type == "waiting_for_ml_ticker":
                with _user_states_lock:
                    _user_states.pop(chat_id_str, None)
                _handle_ml_command(chat_id_str, msg_text)
                return
            elif state_type == "waiting_for_trade_ticker":
                # 用戶輸入股票代碼，獲取快照報價並顯示
                ticker = msg_text.upper().strip()
                
                # 驗證 ticker 格式（簡單檢查）
                if not ticker or len(ticker) > 5:
                    send_message("❌ 無效的股票代碼格式，請輸入有效的 ticker (例如: NVDA)", chat_id=chat_id_str)
                    return
                
                # 獲取快照報價
                try:
                    import yfinance as yf
                    
                    tkr = yf.Ticker(ticker)
                    info = tkr.info or {}
                    
                    if not info:
                        raise ValueError("Unable to fetch ticker info")
                    
                    # 提取報價信息
                    price = info.get("currentPrice") or info.get("regularMarketPrice", 0)
                    prev_close = info.get("previousClose", 0)
                    change = price - prev_close if prev_close else 0
                    change_pct = (change / prev_close * 100) if prev_close else 0
                    high_52w = info.get("fiftyTwoWeekHigh", 0)
                    low_52w = info.get("fiftyTwoWeekLow", 0)
                    volume = info.get("volume", 0)
                    avg_volume = info.get("averageVolume", 0)
                    market_cap = info.get("marketCap", 0)
                    pe_ratio = info.get("trailingPE", 0)
                    
                    # 取得現在時間作為快照時間
                    now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                    
                    # 計算 52 週位置百分比
                    week_52_pct = 0
                    if high_52w and low_52w:
                        week_52_pct = (price - low_52w) / (high_52w - low_52w) * 100
                    
                    # 格式化變動指示
                    change_emoji = "📈" if change >= 0 else "📉"
                    change_str = f"+{change:.2f}" if change >= 0 else f"{change:.2f}"
                    change_pct_str = f"+{change_pct:.2f}%" if change_pct >= 0 else f"{change_pct:.2f}%"
                    
                    # 格式化其他信息
                    volume_str = f"{volume/1e6:.1f}M" if volume else "N/A"
                    avg_volume_str = f"{avg_volume/1e6:.1f}M" if avg_volume else "N/A"
                    market_cap_str = ""
                    if market_cap:
                        if market_cap >= 1e12:
                            market_cap_str = f"${market_cap/1e12:.1f}T"
                        elif market_cap >= 1e9:
                            market_cap_str = f"${market_cap/1e9:.1f}B"
                        else:
                            market_cap_str = f"${market_cap/1e6:.1f}M"
                    
                    pe_str = f"{pe_ratio:.1f}" if pe_ratio else "N/A"
                    
                    # 構造快照報價消息
                    snapshot_msg = f"""<b>📸 {ticker} 快照報價</b>

<b>現價:</b> <code>${price:.2f}</code> {change_emoji} {change_str} ({change_pct_str})
<b>前收:</b> ${prev_close:.2f}

<b>52 週範圍:</b> ${low_52w:.2f} - ${high_52w:.2f}
<b>目前位置:</b> {week_52_pct:.1f}% (52W 區間)

<b>成交量:</b> {volume_str} (平均: {avg_volume_str})
<b>市值:</b> {market_cap_str}
<b>P/E 比:</b> {pe_str}

<i>📊 快照時間: {now}</i>

� <b>請輸入交易數量</b> (例如: 100)"""
                    
                    # 存儲 ticker 並更新狀態
                    with _user_states_lock:
                        _user_states[chat_id_str]["data"]["ticker"] = ticker
                        _user_states[chat_id_str]["data"]["current_price"] = price
                        _user_states[chat_id_str]["state"] = "waiting_for_trade_quantity"
                    
                    send_message(snapshot_msg, chat_id=chat_id_str, parse_mode="HTML")
                    logger.info(f"✅ 快照報價取得: {ticker} @ ${price:.2f}")
                    
                except Exception as e:
                    logger.error(f"獲取快照報價失敗 [{ticker}]: {e}", exc_info=False)
                    send_message(
                        f"⚠️ 無法取得 {ticker} 的實時報價，請繼續輸入交易數量\n"
                        f"📊 請輸入交易數量 (例如: 100)",
                        chat_id=chat_id_str, parse_mode="HTML"
                    )
                    # 即使報價失敗，也繼續進行交易流程
                    with _user_states_lock:
                        _user_states[chat_id_str]["data"]["ticker"] = ticker
                        _user_states[chat_id_str]["state"] = "waiting_for_trade_quantity"
                
                return
            elif state_type == "waiting_for_trade_quantity":
                # 用戶輸入數量，驗證購買力並詢問訂單類型
                try:
                    quantity = int(msg_text)
                    if quantity <= 0:
                        send_message("❌ 數量必須大於 0", chat_id=chat_id_str)
                        return
                    
                    # 重新獲取最新的購買力（解決競態條件）
                    try:
                        status = get_status()
                        buying_power = status.get("buying_power", 0)
                        if buying_power == 0:
                            buying_power = status.get("cash", 0)
                        if buying_power == 0:
                            buying_power = status.get("nav", 0)
                        account_id = status.get("account", "")
                    except Exception as e:
                        logger.warning(f"重新獲取購買力失敗: {e}")
                        buying_power = 0
                        account_id = ""
                    
                    ticker = _user_states[chat_id_str]["data"].get("ticker", "?")
                    current_price = _user_states[chat_id_str]["data"].get("current_price", 0)
                    action = _user_states[chat_id_str]["data"].get("action", "BUY")
                    
                    # 計算所需資金
                    position_value = current_price * quantity
                    
                    # 驗證購買力是否足夠（只在買入時檢查）
                    if action == "BUY" and position_value > buying_power:
                        send_message(
                            f"❌ 購買力不足\n\n"
                            f"股票: <code>{ticker}</code>\n"
                            f"數量: {quantity} 股\n"
                            f"當前價: ${current_price:.2f}\n"
                            f"所需資金: ${position_value:,.2f}\n"
                            f"可用資金: ${buying_power:,.2f}\n\n"
                            f"請輸入較少的數量或選擇其他股票",
                            chat_id=chat_id_str,
                            parse_mode="HTML"
                        )
                        return
                    
                    # 保存購買力和賬戶信息到狀態
                    with _user_states_lock:
                        _user_states[chat_id_str]["data"]["quantity"] = quantity
                        _user_states[chat_id_str]["data"]["buying_power"] = buying_power
                        _user_states[chat_id_str]["data"]["account_id"] = account_id
                        _user_states[chat_id_str]["state"] = "waiting_for_order_type"
                    
                    # 構造訂單類型按鈕
                    reply_markup = {
                        "inline_keyboard": [
                            [
                                {"text": "🔄 市價單", "callback_data": "order_type_mkt"},
                                {"text": "💰 限價單", "callback_data": "order_type_lmt"}
                            ]
                        ]
                    }
                    
                    send_message(
                        f"📊 <b>訂單類型</b>\n\n"
                        f"股票: <code>{ticker}</code>\n"
                        f"數量: {quantity} 股\n"
                        f"當前價: ${current_price:.2f}\n"
                        f"預計成本: ${position_value:,.2f}\n"
                        f"可用資金: ${buying_power:,.2f}\n\n"
                        f"請選擇訂單類型:",
                        chat_id=chat_id_str,
                        parse_mode="HTML",
                        reply_markup=reply_markup
                    )
                    
                except ValueError:
                    send_message("❌ 無效的數量格式，請輸入整數 (例如: 100)", chat_id=chat_id_str)
                return
            elif state_type == "waiting_for_trade_price":
                # 用戶輸入限價，然後進入止損詢問
                try:
                    price = float(msg_text)
                    if price <= 0:
                        send_message("❌ 價格必須大於 0", chat_id=chat_id_str)
                        return
                    
                    with _user_states_lock:
                        _user_states[chat_id_str]["data"]["price"] = price
                        _user_states[chat_id_str]["state"] = "waiting_for_trade_stoploss"
                    
                    ticker = _user_states.get(chat_id_str, {}).get("data", {}).get("ticker", "?")
                    quantity = _user_states.get(chat_id_str, {}).get("data", {}).get("quantity", 0)
                    
                    send_message(
                        f"💰 <b>限價單確認</b>\n\n"
                        f"股票: <code>{ticker}</code>\n"
                        f"數量: {quantity} 股\n"
                        f"限價: ${price:.2f}\n\n"
                        f"🛑 <b>請輸入止損價格</b> (例如: {price*0.95:.2f})\n"
                        f"<i>或輸入 0 跳過止損設定</i>",
                        chat_id=chat_id_str,
                        parse_mode="HTML"
                    )
                    
                except ValueError:
                    send_message("❌ 無效的價格格式，請輸入數字 (例如: 150.25)", chat_id=chat_id_str)
                return
            elif state_type == "waiting_for_trade_stoploss":
                # 用戶輸入止損，計算風險回報並顯示確認對話框
                try:
                    stoploss = float(msg_text)
                    
                    with _user_states_lock:
                        data = _user_states[chat_id_str]["data"]
                        ticker = data.get("ticker", "?")
                        action = data.get("action", "BUY")  # 從狀態追蹤中獲取
                        price = data.get("price", 0)
                        quantity = data.get("quantity", 0)
                        account_id = data.get("account_id", "")
                        buying_power = data.get("buying_power", 0)
                    
                    # 再次重新獲取最新的購買力（最後驗證）
                    try:
                        status = get_status()
                        fresh_buying_power = status.get("buying_power", 0)
                        if fresh_buying_power == 0:
                            fresh_buying_power = status.get("cash", 0)
                        if fresh_buying_power == 0:
                            fresh_buying_power = status.get("nav", 0)
                        # 使用最新的購買力
                        buying_power = fresh_buying_power
                    except Exception as e:
                        logger.warning(f"最後驗證：重新獲取購買力失敗: {e}")
                        # 使用之前保存的值或者 0
                    
                    # 如果 stoploss = 0，表示跳過止損設定
                    if stoploss == 0:
                        stoploss = None
                    
                    # 驗證止損價格合理性（如果有設定）
                    if stoploss is not None:
                        if action == "BUY" and stoploss >= price:
                            send_message("❌ 買入時止損價應該低於入場價", chat_id=chat_id_str)
                            return
                        elif action == "SELL" and stoploss <= price:
                            send_message("❌ 賣出時止損價應該高於入場價", chat_id=chat_id_str)
                            return
                    
                    # 計算風險回報比
                    entry_price = price
                    stop_loss = stoploss if stoploss else 0
                    
                    if stop_loss:
                        target_price = entry_price + (entry_price - stop_loss) * 2  # 假設 R:R 2:1
                        risk_amount = abs(entry_price - stop_loss) * quantity
                        reward_amount = abs(target_price - entry_price) * quantity
                        rr_ratio = reward_amount / risk_amount if risk_amount > 0 else 0
                    else:
                        target_price = 0
                        risk_amount = 0
                        reward_amount = 0
                        rr_ratio = 0
                    
                    account_size = C.ACCOUNT_SIZE
                    position_val = price * quantity
                    position_pct = (position_val / account_size * 100) if account_size else 0
                    risk_pct = (risk_amount / account_size * 100) if account_size else 0
                    
                    # 檢查購買力是否足夠
                    if position_val > buying_power:
                        send_message(
                            f"❌ 購買力不足\n\n"
                            f"需要: ${position_val:,.2f}\n"
                            f"可用: ${buying_power:,.2f}",
                            chat_id=chat_id_str,
                            parse_mode="HTML"
                        )
                        return
                    
                    # 構造確認消息
                    action_display = "📈 買入" if action == "BUY" else "📉 賣出"
                    
                    stoploss_info = f"<b>  止損價:</b> ${stop_loss:.2f}" if stop_loss else "<b>  止損價:</b> <i>未設定</i>"
                    target_info = f"<b>  目標價:</b> ${target_price:.2f}" if target_price else "<b>  目標價:</b> <i>未計算</i>"
                    rr_info = f"<b>  R:R 比:</b> 1:{rr_ratio:.1f}" if rr_ratio else "<b>  R:R 比:</b> <i>未設定</i>"
                    
                    confirm_msg = f"""<b>{action_display} 交易確認</b>

<b>帳戶:</b> <code>{account_id}</code>

<b>交易詳情:</b>
<b>  股票:</b> <code>{ticker}</code>
<b>  數量:</b> {quantity} 股
<b>  入場價:</b> ${entry_price:.2f}
{stoploss_info}
{target_info}

<b>風險回報:</b>
<b>  風險金額:</b> ${risk_amount:,.2f}
<b>  獲利金額:</b> ${reward_amount:,.2f}
{rr_info}

<b>帳戶影響:</b>
<b>  倉位值:</b> ${position_val:,.2f} ({position_pct:.1f}%)
<b>  風險比:</b> {risk_pct:.2f}%
<b>  購買力:</b> ${buying_power:,.2f}

<i>⚠️ 點擊「確認」即刻提交交易</i>"""
                    
                    # 構造確認/取消按鈕
                    reply_markup = {
                        "inline_keyboard": [
                            [
                                {"text": "✅ 確認交易", "callback_data": f"confirm_trade_{action}_{ticker}_{quantity}_{entry_price:.2f}_{stop_loss:.2f}"},
                                {"text": "❌ 取消", "callback_data": "cancel_trade"}
                            ]
                        ]
                    }
                    
                    # 保存完整交易數據
                    with _user_states_lock:
                        _user_states[chat_id_str]["state"] = "waiting_for_trade_confirmation"
                        _user_states[chat_id_str]["data"].update({
                            "stoploss": stoploss,
                            "target_price": target_price,
                            "risk_amount": risk_amount,
                            "reward_amount": reward_amount,
                            "rr_ratio": rr_ratio
                        })
                    
                    send_message(
                        confirm_msg,
                        chat_id=chat_id_str,
                        parse_mode="HTML",
                        reply_markup=reply_markup
                    )
                    
                    logger.info(f"📋 交易確認對話: {ticker} {action} {quantity} @ ${entry_price:.2f}")
                    
                except ValueError:
                    send_message("❌ 無效的止損價格格式，請輸入數字 (例如: 140.00)", chat_id=chat_id_str)
                return
        
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
        
        # ── Admin-Only 掃描指令 ──────────────────────────────────────────────
        if cmd == "/scan":
            if not is_admin:
                send_message("❌ /scan 指令僅管理員可用", chat_id=chat_id_str)
                return
            _handle_scan_command(chat_id_str, "SEPA")
            return
        elif cmd == "/qm_scan":
            if not is_admin:
                send_message("❌ /qm_scan 指令僅管理員可用", chat_id=chat_id_str)
                return
            _handle_scan_command(chat_id_str, "QM")
            return
        elif cmd == "/ml_scan":
            if not is_admin:
                send_message("❌ /ml_scan 指令僅管理員可用", chat_id=chat_id_str)
                return
            _handle_scan_command(chat_id_str, "ML")
            return
        
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
        elif cmd == "/trade":
            _handle_trade_command(chat_id_str)
            return

        # ── 同步指令（即時回覆) ───────────────────────────────────────────────
        if cmd == "/market":
            reply = _handle_market_command(chat_id_str)
        elif cmd == "/dashboard":
            reply = _handle_dashboard_command(chat_id_str)
        elif cmd == "/watchlist":
            reply = _handle_watchlist_command(chat_id_str)
        elif cmd == "/positions":
            reply = _handle_positions_command(chat_id_str)
        elif cmd == "/position":
            reply = _handle_position_command(chat_id_str)
        elif cmd == "/account":
            reply = _handle_account_command(chat_id_str)
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
