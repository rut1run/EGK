"""EGK LAST CHANCE SPLIT: public rankings -> one persistent Discord panel."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

LOG = logging.getLogger(__name__)
PAGE = "https://stats.lynx-est.com/last-chance-split/"
API = "https://stats.lynx-est.com/api/lcs/rankings"
TEAM_ID = "egk-esports"
JST = timezone(timedelta(hours=9))
HIDE_AT = datetime(2026, 11, 14, 22, tzinfo=JST)
REVEAL_AT = datetime(2026, 11, 15, 12, tzinfo=JST)


def parse_time(value):
    if not isinstance(value, str):
        raise ValueError("missing timestamp")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp has no timezone")
    return dt


def make_embed(data=None, *, now=None, failed=False):
    now = now or datetime.now(JST)
    if data is not None and not isinstance(data, dict):
        raise ValueError("invalid ranking response")
    e = discord.Embed(title="EGK E-Sports｜投票状況", url=PAGE, color=0xF2D335)
    e.set_thumbnail(url="https://stats.lynx-est.com/assets/lcs-teams/x-20261109/egk-esports.jpg")
    hidden = HIDE_AT <= now < REVEAL_AT or (data or {}).get("rankingVisibility") == "hidden"
    if hidden:
        e.description = "ランキング非公開\n結果発表：11月15日 12:00（日本時間）"
    elif failed:
        # Never present an old snapshot as the current ranking.
        e.description = "投票状況を取得できませんでした。30秒ごとに再試行します。"
    elif not isinstance(data, dict) or data.get("ok") is not True:
        raise ValueError("invalid ranking response")
    elif data.get("available") is not True:
        e.description = "順位・票数は現在公開されていません。"
    else:
        updated = parse_time(data.get("updatedAt"))
        if now >= REVEAL_AT and updated < REVEAL_AT:
            e.description = "結果発表待ちです。公式の更新を確認しています。"
        else:
            candidates = data.get("candidates")
            if not isinstance(candidates, list):
                raise ValueError("invalid candidates")
            team = next((c for c in candidates if isinstance(c, dict) and c.get("id") == TEAM_ID), None)
            if team is None:
                raise ValueError("EGK is missing from rankings")
            rank, votes = team.get("rank"), team.get("votes")
            if type(rank) is not int or rank < 1 or type(votes) is not int or votes < 0:
                raise ValueError("invalid rank or votes")
            final = now >= REVEAL_AT
            e.description = "公式発表結果" if final else "LAST CHANCE SPLIT｜暫定順位"
            e.add_field(name="順位", value=f"**{rank}位**" + ("（同順位・確定待ち）" if team.get("tiePending") else ""))
            e.add_field(name="獲得票数", value=f"**{votes:,}票**")
            e.add_field(name="公式集計日時", value=updated.astimezone(JST).strftime("%m/%d %H:%M:%S JST"), inline=False)
    e.set_footer(text="出典：PRIDE LAST CHANCE SPLIT｜30秒ごとに確認")
    return e


def link_view():
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="投票ページを開く", url=PAGE))
    return view


class VotePanel(commands.Cog):
    def __init__(self, bot, db):
        self.bot, self.db = bot, db
        self.session = None
        self.lock = asyncio.Lock()
        self.db.execute("""CREATE TABLE IF NOT EXISTS vote_panels (
            guild_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL)""")
        self.db.commit()

    async def cog_load(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15), trust_env=True)
        self.refresh.start()

    async def cog_unload(self):
        self.refresh.cancel()
        task = self.refresh.get_task()
        if task:
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self.session:
            await self.session.close()

    async def fetch_embed(self):
        try:
            async with self.session.get(API, headers={"Cache-Control": "no-cache"}) as response:
                response.raise_for_status()
                return make_embed(await response.json())
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, TypeError):
            LOG.warning("投票順位の取得に失敗しました", exc_info=True)
            return make_embed(failed=True)

    async def update_message(self, row, embed):
        channel = self.bot.get_channel(row["channel_id"]) or await self.bot.fetch_channel(row["channel_id"])
        message = await channel.fetch_message(row["message_id"])
        if not message.embeds or message.embeds[0].to_dict() != embed.to_dict():
            await message.edit(embed=embed, view=link_view(), allowed_mentions=discord.AllowedMentions.none())

    @tasks.loop(seconds=30)
    async def refresh(self):
        async with self.lock:
            rows = self.db.execute("SELECT * FROM vote_panels").fetchall()
            if not rows:
                return
            embed = await self.fetch_embed()
            for row in rows:
                try:
                    await self.update_message(row, embed)
                except discord.NotFound:
                    # An intentionally deleted panel must not be reposted every 30s.
                    self.db.execute("DELETE FROM vote_panels WHERE guild_id=?", (row["guild_id"],))
                    self.db.commit()
                    LOG.warning("削除された投票パネルの自動更新を停止しました")
                except discord.HTTPException:
                    LOG.warning("投票パネルを更新できません。チャンネル権限を確認してください", exc_info=True)

    @refresh.before_loop
    async def before_refresh(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="vote_panel", description="EGK投票順位の自動更新パネルを設置します")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(channel="パネルを表示するチャンネル（省略すると現在のチャンネル）")
    async def install(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        await interaction.response.defer(ephemeral=True)
        channel = channel or interaction.channel
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != interaction.guild_id:
            return await interaction.followup.send("サーバー内のテキストチャンネルを指定してください。", ephemeral=True)
        perms = channel.permissions_for(interaction.guild.me)
        if not all((perms.view_channel, perms.send_messages, perms.embed_links, perms.read_message_history)):
            return await interaction.followup.send("Botに「チャンネルを見る・メッセージを送信・埋め込みリンク・メッセージ履歴を読む」を許可してください。", ephemeral=True)
        async with self.lock:
            row = self.db.execute("SELECT * FROM vote_panels WHERE guild_id=?", (interaction.guild_id,)).fetchone()
            embed = await self.fetch_embed()
            try:
                if row and row["channel_id"] == channel.id:
                    try:
                        await self.update_message(row, embed)
                        return await interaction.followup.send(f"既存のパネルを更新しました。{channel.mention}", ephemeral=True)
                    except discord.NotFound:
                        pass
                # Delete the old panel first to avoid leaving a stale ranking behind.
                if row and row["channel_id"] != channel.id:
                    try:
                        old = self.bot.get_channel(row["channel_id"]) or await self.bot.fetch_channel(row["channel_id"])
                        await old.get_partial_message(row["message_id"]).delete()
                    except discord.NotFound:
                        pass
                msg = await channel.send(embed=embed, view=link_view(), allowed_mentions=discord.AllowedMentions.none())
                self.db.execute("INSERT OR REPLACE INTO vote_panels VALUES(?,?,?)", (interaction.guild_id, channel.id, msg.id))
                self.db.commit()
            except discord.HTTPException:
                LOG.warning("投票パネルの設置に失敗しました", exc_info=True)
                return await interaction.followup.send("パネルを設置できませんでした。Botのチャンネル権限を確認してください。", ephemeral=True)
        await interaction.followup.send(f"{channel.mention} にパネルを設置しました。30秒ごとに確認します。", ephemeral=True)

    @app_commands.command(name="vote_panel_stop", description="EGK投票パネルを削除して自動更新を停止します")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def stop(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        async with self.lock:
            row = self.db.execute("SELECT * FROM vote_panels WHERE guild_id=?", (interaction.guild_id,)).fetchone()
            if not row:
                return await interaction.followup.send("このサーバーにはパネルがありません。", ephemeral=True)
            try:
                channel = self.bot.get_channel(row["channel_id"]) or await self.bot.fetch_channel(row["channel_id"])
                await channel.get_partial_message(row["message_id"]).delete()
            except discord.NotFound:
                pass
            except discord.HTTPException:
                return await interaction.followup.send("パネルを削除できませんでした。Botの権限を確認して再実行してください。", ephemeral=True)
            self.db.execute("DELETE FROM vote_panels WHERE guild_id=?", (interaction.guild_id,))
            self.db.commit()
        await interaction.followup.send("パネルを削除し、自動更新を停止しました。", ephemeral=True)
