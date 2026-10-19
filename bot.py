import os
import html
import logging
from datetime import datetime, timedelta, timezone

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("football-bot")

BOT_TOKEN = os.environ["BOT_TOKEN"]

SITE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
STANDINGS = "https://site.api.espn.com/apis/v2/sports/soccer"

LEAGUES = {
    "eng.1": "🏴 Premier League",
    "esp.1": "🇪🇸 La Liga",
    "ita.1": "🇮🇹 Serie A",
    "ger.1": "🇩🇪 Bundesliga",
    "fra.1": "🇫🇷 Ligue 1",
    "uefa.champions": "🏆 Champions League",
}


async def fetch(url: str) -> dict:
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.json()


def league_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(n, callback_data=f"lg:{c}")] for c, n in LEAGUES.items()]
    )


def action_menu(code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 Table", callback_data=f"act:{code}:table"),
                InlineKeyboardButton("🔴 Today", callback_data=f"act:{code}:today"),
            ],
            [
                InlineKeyboardButton("📅 Upcoming", callback_data=f"act:{code}:upcoming"),
                InlineKeyboardButton("✅ Results", callback_data=f"act:{code}:results"),
            ],
            [InlineKeyboardButton("⬅️ Leagues", callback_data="home")],
        ]
    )


def stat(stats: list, name: str, default=0):
    for s in stats:
        if s.get("name") == name:
            v = s.get("value")
            return int(v) if isinstance(v, (int, float)) else default
    return default


async def get_table(code: str) -> str:
    data = await fetch(f"{STANDINGS}/{code}/standings")
    entries = data["children"][0]["standings"]["entries"]
    rows = []
    for e in entries:
        st = e.get("stats", [])
        rows.append(
            (
                stat(st, "rank", 99),
                (e["team"].get("abbreviation") or e["team"]["displayName"][:3]).upper(),
                stat(st, "gamesPlayed"),
                stat(st, "wins"),
                stat(st, "ties"),
                stat(st, "losses"),
                stat(st, "points"),
            )
        )
    rows.sort(key=lambda r: r[0])
    lines = ["#  Team  P  W  D  L Pts"]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i:>2} {r[1]:<4}{r[2]:>2} {r[3]:>2} {r[4]:>2} {r[5]:>2} {r[6]:>3}")
    return f"<b>{LEAGUES[code]} – Table</b>\n<pre>{html.escape(chr(10).join(lines))}</pre>"


def ymd(d: datetime) -> str:
    return d.strftime("%Y%m%d")


async def get_matches(code: str, start: datetime, end: datetime, title: str, newest_first=False) -> str:
    url = f"{SITE}/{code}/scoreboard?dates={ymd(start)}-{ymd(end)}&limit=100"
    data = await fetch(url)
    events = data.get("events", [])
    events.sort(key=lambda e: e["date"], reverse=newest_first)
    if not events:
        return f"<b>{LEAGUES[code]} – {title}</b>\n\nNo matches found."
    lines = []
    for e in events[:12]:
        comp = e["competitions"][0]
        home = next(c for c in comp["competitors"] if c["homeAway"] == "home")
        away = next(c for c in comp["competitors"] if c["homeAway"] == "away")
        state = e["status"]["type"]["state"]  # pre / in / post
        when = datetime.fromisoformat(e["date"].replace("Z", "+00:00")).strftime("%d %b %H:%M UTC")
        hn = html.escape(home["team"]["displayName"])
        an = html.escape(away["team"]["displayName"])
        if state == "pre":
            lines.append(f"{when}\n  {hn} vs {an}")
        else:
            tag = "🔴 " + html.escape(e["status"]["type"].get("shortDetail", "")) if state == "in" else when
            lines.append(f"{tag}\n  {hn} {home.get('score', '-')}–{away.get('score', '-')} {an}")
    return f"<b>{LEAGUES[code]} – {title}</b>\n\n" + "\n".join(lines)


async def get_today(code: str) -> str:
    now = datetime.now(timezone.utc)
    return await get_matches(code, now, now, "Today")


async def get_upcoming(code: str) -> str:
    now = datetime.now(timezone.utc)
    return await get_matches(code, now + timedelta(days=1), now + timedelta(days=7), "Upcoming")


async def get_results(code: str) -> str:
    now = datetime.now(timezone.utc)
    return await get_matches(code, now - timedelta(days=7), now - timedelta(days=1), "Results", newest_first=True)


ACTIONS = {
    "table": get_table,
    "today": get_today,
    "upcoming": get_upcoming,
    "results": get_results,
}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = html.escape(update.effective_user.first_name or "there")
    await update.message.reply_text(
        f"👋 Hi {name}! Welcome to <b>Football Stats</b>.\nPick a league:",
        parse_mode=ParseMode.HTML,
        reply_markup=league_menu(),
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Use /start to open the league menu.")


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    parts = q.data.split(":")

    if parts[0] == "home":
        await q.edit_message_text("Pick a league:", reply_markup=league_menu())
    elif parts[0] == "lg":
        code = parts[1]
        await q.edit_message_text(
            f"<b>{LEAGUES[code]}</b>\nWhat do you want to see?",
            parse_mode=ParseMode.HTML,
            reply_markup=action_menu(code),
        )
    elif parts[0] == "act":
        _, code, action = parts
        try:
            text = await ACTIONS[action](code)
        except Exception:
            log.exception("Fetch failed")
            text = "⚠️ Couldn't fetch data right now. Try again in a moment."
        await q.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=action_menu(code)
        )


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CallbackQueryHandler(on_button))
    log.info("Bot running...")
    app.run_polling()


if __name__ == "__main__":
    main()
