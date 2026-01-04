
import os
import discord
import random
import aiosqlite
from discord.ext import commands
from discord.ui import View, Button
from datetime import datetime, timedelta

TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = "!"
DB = "casino.db"

intents = discord.Intents.all()
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

# ================= DATABASE =================
async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            balance INTEGER DEFAULT 1000,
            last_daily TEXT
        )
        """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            guild_id INTEGER PRIMARY KEY,
            casino_enabled INTEGER DEFAULT 1,
            channel_id INTEGER
        )
        """)
        await db.commit()

@bot.event
async def setup_hook():
    await init_db()

# ================= HELPERS =================
async def get_user(uid):
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT balance, last_daily FROM users WHERE user_id=?", (uid,))
        row = await cur.fetchone()
        if not row:
            await db.execute("INSERT INTO users (user_id) VALUES (?)", (uid,))
            await db.commit()
            return 1000, None
        return row

async def update_balance(uid, bal):
    async with aiosqlite.connect(DB) as db:
        await db.execute("UPDATE users SET balance=? WHERE user_id=?", (bal, uid))
        await db.commit()

async def casino_allowed(ctx):
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT casino_enabled, channel_id FROM settings WHERE guild_id=?", (ctx.guild.id,))
        row = await cur.fetchone()
        if not row:
            await db.execute("INSERT INTO settings (guild_id) VALUES (?)", (ctx.guild.id,))
            await db.commit()
            return True
        enabled, channel = row
        if not enabled:
            return False
        if channel and ctx.channel.id != channel:
            return False
        return True

def admin():
    async def predicate(ctx):
        return ctx.author.guild_permissions.administrator
    return commands.check(predicate)

# ================= ECONOMY =================
@bot.command()
async def balance(ctx):
    bal, _ = await get_user(ctx.author.id)
    await ctx.send(f"💰 **{ctx.author.name}** has `{bal}` chips")

@bot.command()
async def daily(ctx):
    bal, last = await get_user(ctx.author.id)
    now = datetime.utcnow()
    if last:
        last = datetime.fromisoformat(last)
        if now - last < timedelta(hours=24):
            return await ctx.send("⏳ Daily already claimed")
    bal += 500
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "UPDATE users SET balance=?, last_daily=? WHERE user_id=?",
            (bal, now.isoformat(), ctx.author.id)
        )
        await db.commit()
    await ctx.send("🎁 You received **500 chips**")

# ================= GAMES =================
@bot.command()
async def coinflip(ctx, choice: str, bet: int):
    if not await casino_allowed(ctx): return
    bal, _ = await get_user(ctx.author.id)
    if bet <= 0 or bet > bal:
        return await ctx.send("❌ Invalid bet")
    result = random.choice(["heads", "tails"])
    if choice.lower() == result:
        bal += bet
        msg = "✅ You won!"
    else:
        bal -= bet
        msg = "❌ You lost!"
    await update_balance(ctx.author.id, bal)
    await ctx.send(f"🪙 **{result.upper()}** — {msg} (`{bal}` chips)")

@bot.command()
async def dice(ctx, number: int, bet: int):
    if not await casino_allowed(ctx): return
    if number < 1 or number > 6:
        return await ctx.send("🎲 Pick 1–6")
    bal, _ = await get_user(ctx.author.id)
    if bet <= 0 or bet > bal:
        return await ctx.send("❌ Invalid bet")
    roll = random.randint(1, 6)
    if roll == number:
        bal += bet * 5
        msg = "🔥 JACKPOT!"
    else:
        bal -= bet
        msg = "💀 Lost"
    await update_balance(ctx.author.id, bal)
    await ctx.send(f"🎲 Rolled **{roll}** — {msg} (`{bal}` chips)")

@bot.command()
async def slots(ctx, bet: int):
    if not await casino_allowed(ctx): return
    bal, _ = await get_user(ctx.author.id)
    if bet <= 0 or bet > bal:
        return await ctx.send("❌ Invalid bet")
    symbols = ["🍒", "🍋", "🔔", "💎"]
    roll = [random.choice(symbols) for _ in range(3)]
    if len(set(roll)) == 1:
        bal += bet * 3
        msg = "🎉 WIN!"
    else:
        bal -= bet
        msg = "💀 LOSE"
    await update_balance(ctx.author.id, bal)
    await ctx.send(f"{' '.join(roll)} — {msg} (`{bal}` chips)")

@bot.command()
async def roulette(ctx, color: str, bet: int):
    if not await casino_allowed(ctx): return
    bal, _ = await get_user(ctx.author.id)
    if bet <= 0 or bet > bal:
        return await ctx.send("❌ Invalid bet")
    result = random.choice(["red", "black", "green"])
    if color.lower() == result:
        bal += bet * 2
        msg = "🎯 WIN"
    else:
        bal -= bet
        msg = "💀 LOST"
    await update_balance(ctx.author.id, bal)
    await ctx.send(f"🎡 **{result.upper()}** — {msg} (`{bal}` chips)")

@bot.command()
async def allin(ctx):
    if not await casino_allowed(ctx): return
    bal, _ = await get_user(ctx.author.id)
    if bal <= 0:
        return await ctx.send("❌ No chips")
    if random.random() < 0.45:
        bal *= 2
        msg = "🔥 DOUBLED!"
    else:
        bal = 0
        msg = "💀 LOST EVERYTHING"
    await update_balance(ctx.author.id, bal)
    await ctx.send(f"{msg} (`{bal}` chips)")

# ================= PvP WITH BUTTONS =================
class PvPView(View):
    def __init__(self, challenger, opponent, bet):
        super().__init__(timeout=30)
        self.challenger = challenger
        self.opponent = opponent
        self.bet = bet
        self.done = False

    async def interaction_check(self, interaction: discord.Interaction):
        return interaction.user.id == self.opponent.id

    @discord.ui.button(label="Accept ⚔️", style=discord.ButtonStyle.green)
    async def accept(self, interaction: discord.Interaction, button: Button):
        if self.done:
            return
        self.done = True

        winner = random.choice([self.challenger, self.opponent])
        loser = self.opponent if winner == self.challenger else self.challenger

        w_bal, _ = await get_user(winner.id)
        l_bal, _ = await get_user(loser.id)

        w_bal += self.bet
        l_bal -= self.bet

        await update_balance(winner.id, w_bal)
        await update_balance(loser.id, l_bal)

        await interaction.response.edit_message(
            content=f"⚔️ **PvP Result**\n🏆 Winner: {winner.mention}\n💀 Loser: {loser.mention}\n💰 `{self.bet}` chips transferred",
            view=None
        )

    @discord.ui.button(label="Decline ❌", style=discord.ButtonStyle.red)
    async def decline(self, interaction: discord.Interaction, button: Button):
        self.done = True
        await interaction.response.edit_message(
            content="❌ PvP challenge declined.",
            view=None
        )

@bot.command()
async def pvp(ctx, opponent: discord.Member, bet: int):
    if opponent.bot or opponent == ctx.author:
        return await ctx.send("❌ Invalid opponent")
    if not await casino_allowed(ctx):
        return
    bal1, _ = await get_user(ctx.author.id)
    bal2, _ = await get_user(opponent.id)
    if bet <= 0 or bet > bal1 or bet > bal2:
        return await ctx.send("❌ Invalid bet or insufficient balance")
    view = PvPView(ctx.author, opponent, bet)
    await ctx.send(
        f"⚔️ **PvP Challenge**\n{ctx.author.mention} vs {opponent.mention}\n💰 Bet: `{bet}` chips",
        view=view
    )

# ================= ADMIN =================
@bot.command()
@admin()
async def addchips(ctx, member: discord.Member, amount: int):
    bal, _ = await get_user(member.id)
    bal += amount
    await update_balance(member.id, bal)
    await ctx.send(f"✅ Added `{amount}` chips to {member.name}")

@bot.command()
@admin()
async def removechips(ctx, member: discord.Member, amount: int):
    bal, _ = await get_user(member.id)
    bal = max(0, bal - amount)
    await update_balance(member.id, bal)
    await ctx.send(f"❌ Removed chips from {member.name}")

@bot.command()
@admin()
async def setchannel(ctx):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (guild_id, channel_id) VALUES (?,?)",
            (ctx.guild.id, ctx.channel.id)
        )
        await db.commit()
    await ctx.send("📌 Casino channel set")

@bot.command()
@admin()
async def casino(ctx, mode: str):
    value = 1 if mode.lower() == "on" else 0
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (guild_id, casino_enabled) VALUES (?,?)",
            (ctx.guild.id, value)
        )
        await db.commit()
    await ctx.send(f"🎰 Casino {'ENABLED' if value else 'DISABLED'}")

# ================= READY =================
@bot.event
async def on_ready():
    print(f"✅ {bot.user} ONLINE")

bot.run(TOKEN)
