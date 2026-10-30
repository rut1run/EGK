"""
チーム継続確認Bot（DM送信版 + ローカル管理画面）
- 運営が管理画面でユーザーIDと名前を入力してDMの送信先を登録
- 管理者はブラウザ(http://localhost:8080)で文章・ボタンを入力して一斉DM送信
- 回答はシーズンごとに一覧表示
"""
import asyncio
import csv
import io
import json
import os
import sqlite3
import time
import webbrowser

import discord
from aiohttp import web
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
    override=True,
)
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))
AUTO_OPEN = os.getenv("AUTO_OPEN", "1") == "1"
BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE, "keizoku_dm.db"))

# ───────────── DB ─────────────
db = sqlite3.connect(DB_PATH)
db.row_factory = sqlite3.Row
db.executescript(
    """
    CREATE TABLE IF NOT EXISTS members (
        user_id INTEGER PRIMARY KEY, name TEXT NOT NULL, registered_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS polls (
        id INTEGER PRIMARY KEY AUTOINCREMENT, season TEXT NOT NULL, heading TEXT NOT NULL,
        body TEXT NOT NULL, options TEXT NOT NULL, closed INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS deliveries (
        poll_id INTEGER NOT NULL, user_id INTEGER NOT NULL, channel_id INTEGER,
        message_id INTEGER, status TEXT NOT NULL, PRIMARY KEY (poll_id, user_id));
    CREATE TABLE IF NOT EXISTS responses (
        poll_id INTEGER NOT NULL, user_id INTEGER NOT NULL, choice INTEGER NOT NULL,
        updated_at INTEGER NOT NULL, PRIMARY KEY (poll_id, user_id));
    """
)
db.commit()


def get_poll(pid):
    return db.execute("SELECT * FROM polls WHERE id=?", (pid,)).fetchone()


def opt_text(o):
    return f"{o['emoji']} {o['label']}" if o["emoji"] else o["label"]


# ───────────── DMのメッセージとボタン ─────────────
def build_embed(poll, choice=None):
    opts = json.loads(poll["options"])
    e = discord.Embed(
        title=f"📋 {poll['heading']}",
        description=poll["body"],
        color=discord.Color.light_grey() if poll["closed"] else discord.Color.blurple(),
    )
    if choice is not None:
        e.add_field(name="あなたの回答", value=opt_text(opts[choice]))
    state = "🔒 受付終了" if poll["closed"] else "🟢 受付中（押し直すと変更できます）"
    e.set_footer(text=f"シーズン: {poll['season']} ｜ {state}")
    return e


class PollView(discord.ui.View):
    def __init__(self, poll_id, options, disabled=False):
        super().__init__(timeout=None)
        self.poll_id = poll_id
        styles = [discord.ButtonStyle.success, discord.ButtonStyle.danger]
        for i, o in enumerate(options):
            b = discord.ui.Button(
                label=o["label"], emoji=o["emoji"], disabled=disabled,
                style=styles[i] if i < 2 else discord.ButtonStyle.secondary,
                custom_id=f"keizoku:{poll_id}:{i}",
            )
            b.callback = self._cb(i)
            self.add_item(b)

    def _cb(self, idx):
        async def callback(interaction: discord.Interaction):
            poll = get_poll(self.poll_id)
            ok = db.execute(
                "SELECT 1 FROM deliveries WHERE poll_id=? AND user_id=?",
                (self.poll_id, interaction.user.id),
            ).fetchone()
            if poll is None or not ok:
                return await interaction.response.send_message("対象外です。", ephemeral=True)
            if poll["closed"]:
                return await interaction.response.send_message("受付は終了しました。", ephemeral=True)
            db.execute(
                """INSERT INTO responses VALUES(?,?,?,?) ON CONFLICT(poll_id,user_id)
                   DO UPDATE SET choice=excluded.choice, updated_at=excluded.updated_at""",
                (self.poll_id, interaction.user.id, idx, int(time.time())),
            )
            db.commit()
            await interaction.response.edit_message(embed=build_embed(poll, idx))

        return callback


# ───────────── 送信・締切 ─────────────
async def send_poll(pid):
    poll = get_poll(pid)
    opts = json.loads(poll["options"])
    sent, failed = 0, []
    for m in db.execute("SELECT * FROM members").fetchall():
        status, reason, ch, mid = "sent", None, None, None
        try:
            user = bot.get_user(m["user_id"]) or await bot.fetch_user(m["user_id"])
            msg = await user.send(embed=build_embed(poll), view=PollView(pid, opts))
            ch, mid = msg.channel.id, msg.id
            sent += 1
        except discord.HTTPException as e:
            status = "failed"
            reason = "DM拒否" if isinstance(e, discord.Forbidden) else str(e)[:80]
            failed.append({"name": m["name"], "reason": reason})
        db.execute(
            "INSERT OR REPLACE INTO deliveries VALUES(?,?,?,?,?)",
            (pid, m["user_id"], ch, mid, status),
        )
        db.commit()
        await asyncio.sleep(0.5)  # レート制限対策
    return {"sent": sent, "failed": failed}


async def close_poll(pid):
    db.execute("UPDATE polls SET closed=1 WHERE id=?", (pid,))
    db.commit()
    poll = get_poll(pid)
    opts = json.loads(poll["options"])
    for d in db.execute("SELECT * FROM deliveries WHERE poll_id=? AND status='sent'", (pid,)).fetchall():
        r = db.execute(
            "SELECT choice FROM responses WHERE poll_id=? AND user_id=?", (pid, d["user_id"])
        ).fetchone()
        try:
            pm = bot.get_partial_messageable(d["channel_id"], type=discord.ChannelType.private)
            await pm.get_partial_message(d["message_id"]).edit(
                embed=build_embed(poll, r["choice"] if r else None),
                view=PollView(pid, opts, disabled=True),
            )
        except discord.HTTPException:
            pass
        await asyncio.sleep(0.3)


# ───────────── Bot ─────────────
class KeizokuBot(commands.Bot):
    async def setup_hook(self):
        for p in db.execute("SELECT * FROM polls WHERE closed=0"):
            self.add_view(PollView(p["id"], json.loads(p["options"])))  # 再起動後もボタン有効
        if GUILD_ID:
            g = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=g)
            await self.tree.sync(guild=g)
        else:
            await self.tree.sync()
        await start_web()


bot = KeizokuBot(command_prefix="!", intents=discord.Intents.default())


# ───────────── ローカル管理画面 (aiohttp) ─────────────
def summarize(p):
    opts = json.loads(p["options"])
    resp = {r["user_id"]: r for r in db.execute("SELECT * FROM responses WHERE poll_id=?", (p["id"],))}
    people, counts, pending, failed = [], {}, 0, 0
    rows = db.execute(
        """SELECT d.user_id, d.status, COALESCE(m.name, CAST(d.user_id AS TEXT)) name
           FROM deliveries d LEFT JOIN members m ON m.user_id=d.user_id WHERE d.poll_id=?""",
        (p["id"],),
    ).fetchall()
    for d in rows:
        r = resp.get(d["user_id"])
        if d["status"] == "failed":
            failed += 1
        elif r is None:
            pending += 1
        else:
            counts[r["choice"]] = counts.get(r["choice"], 0) + 1
        people.append({"name": d["name"], "status": d["status"],
                       "choice": r["choice"] if r else None, "at": r["updated_at"] if r else None})
    return {"id": p["id"], "season": p["season"], "heading": p["heading"], "closed": bool(p["closed"]),
            "created_at": p["created_at"], "options": opts, "counts": counts,
            "pending": pending, "failed": failed, "people": people}


async def add_member(uid_raw, name):
    uid_raw = str(uid_raw).strip()
    if not uid_raw.isdigit() or not 15 <= len(uid_raw) <= 20:
        raise ValueError("ユーザーIDは数字だけ(17〜19桁)で入力してください")
    uid = int(uid_raw)
    try:
        user = bot.get_user(uid) or await bot.fetch_user(uid)
    except discord.NotFound:
        raise ValueError("そのIDのユーザーが見つかりません")
    except discord.HTTPException:
        raise ValueError("Discordで確認できませんでした")
    if user.bot:
        raise ValueError("Botは登録できません")
    name = (name or "").strip() or user.display_name  # 名前が空ならDiscord上の名前
    db.execute(
        "INSERT INTO members VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET name=excluded.name",
        (uid, name[:40], int(time.time())),
    )
    db.commit()


def err(msg, status=400):
    return web.json_response({"error": msg}, status=status)


async def h_index(request):
    return web.FileResponse(os.path.join(BASE, "index.html"))


async def h_polls(request):
    seasons = {}
    for p in db.execute("SELECT * FROM polls ORDER BY id DESC"):
        seasons.setdefault(p["season"], []).append(summarize(p))
    return web.json_response({"seasons": [{"season": k, "polls": v} for k, v in seasons.items()]})


async def h_create(request):
    d = await request.json()
    season, heading, body = (str(d.get(k) or "").strip() for k in ("season", "heading", "body"))
    opts = [{"emoji": str(o.get("emoji") or "").strip() or None, "label": str(o.get("label") or "").strip()[:40]}
            for o in d.get("options", [])]
    if not (season and heading and body) or not 2 <= len(opts) <= 5 or not all(o["label"] for o in opts):
        return err("シーズン・タイトル・本文・選択肢(2〜5個)を入力してください。")
    if db.execute("SELECT COUNT(*) FROM members").fetchone()[0] == 0:
        return err("登録メンバーがいません。メンバー画面でユーザーIDと名前を追加してください。")
    cur = db.execute(
        "INSERT INTO polls(season,heading,body,options,created_at) VALUES(?,?,?,?,?)",
        (season, heading, body[:3000], json.dumps(opts, ensure_ascii=False), int(time.time())),
    )
    db.commit()
    pid = cur.lastrowid
    bot.add_view(PollView(pid, opts))
    return web.json_response({"id": pid, **await send_poll(pid)})


async def h_close(request):
    pid = int(request.match_info["id"])
    if get_poll(pid) is None:
        return err("見つかりません", 404)
    await close_poll(pid)
    return web.json_response({"ok": True})


async def h_members(request):
    rows = db.execute("SELECT * FROM members ORDER BY registered_at").fetchall()
    return web.json_response([{"id": str(r["user_id"]), "name": r["name"]} for r in rows])


async def h_member_add(request):
    entries = (await request.json()).get("entries", [])[:200]
    added, errors = 0, []
    for e in entries:
        try:
            await add_member(e.get("id", ""), str(e.get("name") or ""))
            added += 1
        except ValueError as ex:
            errors.append({"line": f"{e.get('id', '')} {e.get('name', '')}".strip(), "reason": str(ex)})
    return web.json_response({"added": added, "errors": errors})


async def h_member_del(request):
    db.execute("DELETE FROM members WHERE user_id=?", (int(request.match_info["id"]),))
    db.commit()
    return web.json_response({"ok": True})


async def h_csv(request):
    season = request.query.get("season")
    polls = db.execute(
        "SELECT * FROM polls" + (" WHERE season=?" if season else "") + " ORDER BY season, id",
        (season,) if season else (),
    ).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["シーズン", "ID", "タイトル", "名前", "回答", "回答日時"])
    for p in polls:
        s = summarize(p)
        for x in s["people"]:
            if x["status"] == "failed":
                ans = "送信失敗"
            elif x["choice"] is None:
                ans = "未回答"
            else:
                ans = opt_text(s["options"][x["choice"]])
            at = time.strftime("%Y-%m-%d %H:%M", time.localtime(x["at"])) if x["at"] else ""
            w.writerow([p["season"], p["id"], p["heading"], x["name"], ans, at])
    return web.Response(
        body=buf.getvalue().encode("utf-8-sig"), content_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="keizoku.csv"'},
    )


@web.middleware
async def local_only(request, handler):
    if request.host.split(":")[0] not in ("127.0.0.1", "localhost"):
        raise web.HTTPForbidden()
    return await handler(request)


async def start_web():
    app = web.Application(middlewares=[local_only])
    app.add_routes([
        web.get("/", h_index), web.get("/api/polls", h_polls), web.post("/api/polls", h_create),
        web.post("/api/polls/{id}/close", h_close), web.get("/api/members", h_members),
        web.post("/api/members", h_member_add), web.delete("/api/members/{id}", h_member_del), web.get("/export.csv", h_csv),
    ])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", WEB_PORT).start()  # 自分のPCからのみアクセス可
    url = f"http://localhost:{WEB_PORT}"
    print(f"管理画面: {url}")
    if AUTO_OPEN:
        webbrowser.open(url)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN が設定されていません (.env を確認)")
    bot.run(TOKEN)
