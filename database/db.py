"""
SQLite database layer for Pokemon Deal Scanner
Stores all deals, price history, and scan logs
"""
import sqlite3
import json
import os
from datetime import datetime
from typing import List, Optional, Dict, Any
from pathlib import Path

DB_PATH = os.getenv("DB_PATH", "pokemon_deals.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize database tables"""
    conn = get_connection()
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS deals (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            url TEXT NOT NULL,
            platform TEXT NOT NULL,
            asking_price REAL NOT NULL,
            card_count INTEGER,
            card_categories TEXT,  -- JSON array
            tier TEXT NOT NULL,
            score REAL NOT NULL,
            price_per_card REAL,
            estimated_market_value REAL,
            estimated_profit REAL,
            profit_margin_pct REAL,
            net_profit_after_fees REAL,
            resale_rate TEXT,
            market_volatility TEXT,
            seller_info TEXT,
            images TEXT,  -- JSON array
            tags TEXT,    -- JSON array
            is_active INTEGER DEFAULT 1,
            notes TEXT,
            posted_at TEXT,
            found_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            price_per_card REAL NOT NULL,
            market_value_sample REAL,
            platform TEXT,
            recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS scan_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            posts_scanned INTEGER DEFAULT 0,
            deals_found INTEGER DEFAULT 0,
            errors TEXT,
            duration_seconds REAL,
            scanned_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS platform_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            date TEXT NOT NULL,
            deals_found INTEGER DEFAULT 0,
            avg_score REAL DEFAULT 0,
            avg_profit REAL DEFAULT 0,
            best_deal_id TEXT,
            UNIQUE(platform, date)
        );

        CREATE INDEX IF NOT EXISTS idx_deals_tier ON deals(tier);
        CREATE INDEX IF NOT EXISTS idx_deals_platform ON deals(platform);
        CREATE INDEX IF NOT EXISTS idx_deals_found_at ON deals(found_at);
        CREATE INDEX IF NOT EXISTS idx_price_history_category ON price_history(category);
    """)

    conn.commit()
    conn.close()
    print(f"✅ Database initialized at {DB_PATH}")


def save_deal(deal_dict: Dict) -> bool:
    """Save a deal to the database. Returns True if new, False if already exists."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT OR IGNORE INTO deals (
                id, title, description, url, platform, asking_price,
                card_count, card_categories, tier, score, price_per_card,
                estimated_market_value, estimated_profit, profit_margin_pct,
                net_profit_after_fees, resale_rate, market_volatility,
                seller_info, images, tags, is_active, notes, posted_at, found_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            deal_dict["id"],
            deal_dict["title"],
            deal_dict.get("description", ""),
            deal_dict["url"],
            deal_dict["platform"],
            deal_dict["asking_price"],
            deal_dict.get("card_count"),
            json.dumps(deal_dict.get("card_categories", [])),
            deal_dict["tier"],
            deal_dict["score"],
            deal_dict.get("price_per_card"),
            deal_dict.get("estimated_market_value"),
            deal_dict.get("estimated_profit"),
            deal_dict.get("profit_margin_pct"),
            deal_dict.get("net_profit_after_fees"),
            deal_dict.get("resale_rate", "unknown"),
            deal_dict.get("market_volatility", "unknown"),
            deal_dict.get("seller_info", ""),
            json.dumps(deal_dict.get("images", [])),
            json.dumps(deal_dict.get("tags", [])),
            1 if deal_dict.get("is_active", True) else 0,
            deal_dict.get("notes", ""),
            deal_dict.get("posted_at"),
            deal_dict.get("found_at")
        ))
        new = cur.rowcount > 0
        conn.commit()
        return new
    finally:
        conn.close()


def get_deals(
    limit: int = 50,
    offset: int = 0,
    tier: Optional[str] = None,
    platform: Optional[str] = None,
    category: Optional[str] = None,
    min_score: float = 0,
    active_only: bool = True
) -> List[Dict]:
    conn = get_connection()
    cur = conn.cursor()

    conditions = ["score >= ?"]
    params: List[Any] = [min_score]

    if active_only:
        conditions.append("is_active = 1")
    if tier:
        conditions.append("tier = ?")
        params.append(tier)
    if platform:
        conditions.append("platform = ?")
        params.append(platform)
    if category:
        conditions.append("card_categories LIKE ?")
        params.append(f'%{category}%')

    where = " AND ".join(conditions)
    params.extend([limit, offset])

    cur.execute(f"""
        SELECT * FROM deals
        WHERE {where}
        ORDER BY found_at DESC, score DESC
        LIMIT ? OFFSET ?
    """, params)

    rows = cur.fetchall()
    conn.close()

    result = []
    for row in rows:
        d = dict(row)
        d["card_categories"] = json.loads(d.get("card_categories") or "[]")
        d["images"] = json.loads(d.get("images") or "[]")
        d["tags"] = json.loads(d.get("tags") or "[]")
        result.append(d)
    return result


def get_deal_stats() -> Dict:
    """Get aggregate stats for the dashboard"""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            COUNT(*) as total_deals,
            SUM(CASE WHEN tier='great' THEN 1 ELSE 0 END) as great_deals,
            SUM(CASE WHEN tier='good' THEN 1 ELSE 0 END) as good_deals,
            SUM(CASE WHEN tier='decent' THEN 1 ELSE 0 END) as decent_deals,
            AVG(score) as avg_score,
            AVG(estimated_profit) as avg_profit,
            MAX(estimated_profit) as max_profit,
            SUM(estimated_profit) as total_profit_potential
        FROM deals WHERE is_active = 1
    """)
    stats = dict(cur.fetchone())

    cur.execute("""
        SELECT platform, COUNT(*) as count, AVG(score) as avg_score, AVG(estimated_profit) as avg_profit
        FROM deals WHERE is_active = 1
        GROUP BY platform ORDER BY count DESC
    """)
    stats["by_platform"] = [dict(r) for r in cur.fetchall()]

    cur.execute("""
        SELECT tier, COUNT(*) as count
        FROM deals WHERE date(found_at) = date('now')
        GROUP BY tier
    """)
    stats["today"] = [dict(r) for r in cur.fetchall()]

    cur.execute("""
        SELECT date(found_at) as date, COUNT(*) as count, AVG(score) as avg_score
        FROM deals
        WHERE found_at >= datetime('now', '-7 days')
        GROUP BY date(found_at)
        ORDER BY date ASC
    """)
    stats["last_7_days"] = [dict(r) for r in cur.fetchall()]

    conn.close()
    return stats


def log_scan(platform: str, posts_scanned: int, deals_found: int, errors: str = "", duration: float = 0):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO scan_logs (platform, posts_scanned, deals_found, errors, duration_seconds)
        VALUES (?, ?, ?, ?, ?)
    """, (platform, posts_scanned, deals_found, errors, duration))
    conn.commit()
    conn.close()


def record_price_history(category: str, price: float, market_value: float = None, platform: str = "market"):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO price_history (category, price_per_card, market_value_sample, platform)
        VALUES (?, ?, ?, ?)
    """, (category, price, market_value, platform))
    conn.commit()
    conn.close()


def get_price_history(category: str, days: int = 30) -> List[Dict]:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT date(recorded_at) as date, AVG(price_per_card) as avg_price, COUNT(*) as samples
        FROM price_history
        WHERE category = ? AND recorded_at >= datetime('now', ? || ' days')
        GROUP BY date(recorded_at)
        ORDER BY date ASC
    """, (category, f"-{days}"))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows
