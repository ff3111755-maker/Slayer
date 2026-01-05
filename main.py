import os
import discord
import random
import aiosqlite
import asyncio
from discord.ext import commands
from discord.ui import View, Button
from datetime import datetime, timedelta

TOKEN = os.getenv("DISCORD_TOKEN")
PREFIX = "."
DB = "casino.db"

intents = discord.Intents.all()
bot = commands.Bot(command_prefix=PREFIX, intents=intents)

invite_cache = {}
cooldowns = {}

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
    ALTER TABLE users ADD COLUMN last_weekly TEXT
    """)
        await db.execute("""
        CREATE TABLE IF NOT EXISTS rewards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            chance REAL
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
def admin():
    async def predicate(ctx):
        return ctx.author.guild_permissions.administrator
    return commands.check(predicate)

def anti_spam(uid, seconds=3):
    now = datetime.utcnow()
    if uid in cooldowns and now < cooldowns[uid]:
        return False
    cooldowns[uid] = now + timedelta(seconds=seconds)
    return True

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
    # admins can bypass channel restriction
    if ctx.author.guild_permissions.administrator:
        return True

    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT casino_enabled, channel_id FROM settings WHERE guild_id=?",
            (ctx.guild.id,)
        )
        row = await cur.fetchone()

        if not row:
            return False  # no channel set yet

        enabled, channel_id = row

        if not enabled:
            return False

        if channel_id is None:
            return False

        if ctx.channel.id != channel_id:
            await ctx.send(
                f"❌ Casino commands only work in <#{channel_id}>"
            )
            return False

        return True

# ================= READY & INVITES =================
@bot.event
async def on_ready():
    for guild in bot.guilds:
        invites = await guild.invites()
        invite_cache[guild.id] = {i.code: i.uses for i in invites}
    print(f"✅ {bot.user} ONLINE")

@bot.event
async def on_member_join(member):
    invites = await member.guild.invites()
    for invite in invites:
        old = invite_cache[member.guild.id].get(invite.code, 0)
        if invite.uses > old:
            inviter = invite.inviter
            bal, _ = await get_user(inviter.id)
            bal += 50
            await update_balance(inviter.id, bal)
            invite_cache[member.guild.id][invite.code] = invite.uses
            try:
                await inviter.send("🎉 Invite used! You earned **50 chips**")
            except:
                pass
            break

# ================= ECONOMY =================
@bot.command()
async def balance(ctx):
    bal, _ = await get_user(ctx.author.id)
    await ctx.send(f"💰 **{ctx.author.name}** has `{bal}` chips")
@bot.command()
async def weekly(ctx):
    bal, _, last_weekly = await get_user(ctx.author.id)
    now = datetime.utcnow()

    if last_weekly:
        last_weekly_time = datetime.fromisoformat(last_weekly)
        if now - last_weekly_time < timedelta(days=7):
            remaining = timedelta(days=7) - (now - last_weekly_time)
            days = remaining.days
            hrs, rem = divmod(remaining.seconds, 3600)
            mins, secs = divmod(rem, 60)
            return await ctx.send(
                f"⏳ You already claimed your weekly reward! Try again in {days}d {hrs}h {mins}m"
            )

    reward_amount = 10000  # You can adjust this

    bal += reward_amount

    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "UPDATE users SET balance=?, last_weekly=? WHERE user_id=?",
            (bal, now.isoformat(), ctx.author.id)
        )
        await db.commit()

    await ctx.send(f"🎁 You received your weekly reward: **{reward_amount} chips**!")
@bot.command()
async def daily(ctx):
    bal, last = await get_user(ctx.author.id)
    now = datetime.utcnow()
    if last and now - datetime.fromisoformat(last) < timedelta(hours=24):
        return await ctx.send("⏳ Daily already claimed")
    bal += 500
    async with aiosqlite.connect(DB) as db:
        await db.execute("UPDATE users SET balance=?, last_daily=? WHERE user_id=?",
                         (bal, now.isoformat(), ctx.author.id))
        await db.commit()
    await ctx.send("🎁 You received **500 chips**")

# ================= LEADERBOARD =================
@bot.command()
async def leaderboard(ctx):
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "SELECT user_id, balance FROM users ORDER BY balance DESC LIMIT 10"
        )
        rows = await cur.fetchall()

    text = "🏆 **TOP 10 LEADERBOARD**\n\n"
    for i, (uid, bal) in enumerate(rows, 1):
        user = bot.get_user(uid)
        name = user.name if user else f"User {uid}"
        text += f"`#{i}` **{name}** — `{bal}` chips\n"

    await ctx.send(text)

# ================= GAMES =================
@bot.command()
async def spin(ctx):
    COST = 2500

    if not await casino_allowed(ctx):
        return

    bal, _ = await get_user(ctx.author.id)
    if bal < COST:
        return await ctx.send("❌ You need **2500 chips** to spin")

    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT name, chance FROM rewards")
        rewards = await cur.fetchall()

    if not rewards:
        return await ctx.send("❌ No rewards configured")

    # take spin cost
    bal -= COST
    await update_balance(ctx.author.id, bal)

    await ctx.send("🎡 **Spinning...**")
    await asyncio.sleep(2)

    roll = random.uniform(0, 100)
    current = 0
    reward = "Nothing"

    for name, chance in rewards:
        current += chance
        if roll <= current:
            reward = name
            break

    # payout logic
    if reward.isdigit():
        bal += int(reward)
        msg = f"💰 You won `{reward}` chips!"
    elif reward.lower() == "jackpot":
        bal += 25000
        msg = "💎 **JACKPOT! +25,000 chips**"
    elif reward.lower() == "lose":
        msg = "💀 You won nothing"
    else:
        msg = f"🎁 You won **{reward}**"

    await update_balance(ctx.author.id, bal)

    await ctx.send(
        f"🎡 **SPIN RESULT**\n"
        f"➡️ **{reward}**\n"
        f"{msg}\n"
        f"💰 `{bal}` chips"
    )
@bot.command()
async def coinflip(ctx, choice: str, bet: int):
    if not anti_spam(ctx.author.id): return
    if not await casino_allowed(ctx): return
    bal, _ = await get_user(ctx.author.id)
    if bet <= 0 or bet > bal:
        return await ctx.send("❌ Invalid bet")
    result = random.choice(["heads", "tails"])
    await ctx.send("🪙 Flipping...")
    await asyncio.sleep(1.5)
    if choice.lower() == result:
        bal += bet
        msg = "✅ WON"
    else:
        bal -= bet
        msg = "💀 LOST"
    await update_balance(ctx.author.id, bal)
    await ctx.send(f"🪙 **{result.upper()}** — {msg} (`{bal}` chips)")

# ================= BLACKJACK WITH BUTTONS =================
class BlackjackView(View):
    def __init__(self, ctx, bet):
        super().__init__(timeout=30)
        self.ctx = ctx
        self.bet = bet
        self.author_id = ctx.author.id
        self.player = [random.randint(1, 11), random.randint(1, 11)]
        self.dealer = [random.randint(1, 11), random.randint(1, 11)]

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "❌ This is **not your Blackjack game**.",
                ephemeral=True
            )
            return False
        return True

    def total(self, hand):
        return sum(hand)

    async def end(self, interaction):
        bal, _ = await get_user(self.author_id)
        p, d = self.total(self.player), self.total(self.dealer)

        while d < 17:
            self.dealer.append(random.randint(1, 11))
            d = self.total(self.dealer)

        if p > 21:
            bal -= self.bet
            result = "💀 BUST"
        elif d > 21 or p > d:
            bal += self.bet
            result = "🎉 WIN"
        elif p < d:
            bal -= self.bet
            result = "💀 LOSE"
        else:
            result = "➖ PUSH"

        await update_balance(self.author_id, bal)

        await interaction.response.edit_message(
            content=(
                f"🃏 **BLACKJACK**\n"
                f"Your hand: {self.player} ({p})\n"
                f"Dealer hand: {self.dealer} ({d})\n"
                f"{result}\n💰 `{bal}` chips"
            ),
            view=None
        )

    @discord.ui.button(label="Hit 🃏", style=discord.ButtonStyle.green)
    async def hit(self, interaction: discord.Interaction, button: Button):
        self.player.append(random.randint(1, 11))
        if self.total(self.player) >= 21:
            await self.end(interaction)
        else:
            await interaction.response.edit_message(
                content=f"🃏 Your hand: {self.player} ({self.total(self.player)})",
                view=self
            )

    @discord.ui.button(label="Stand ✋", style=discord.ButtonStyle.red)
    async def stand(self, interaction: discord.Interaction, button: Button):
        await self.end(interaction)


@bot.command()
async def blackjack(ctx, bet: int):
    bal, _ = await get_user(ctx.author.id)
    if bet <= 0 or bet > bal:
        return await ctx.send("❌ Invalid bet")
    view = BlackjackView(ctx, bet)
    await ctx.send(
        f"🃏 **BLACKJACK**\nYour hand: {view.player}\nDealer: [{view.dealer[0]}, ?]",
        view=view
    )

# ================= ADMIN =================
@bot.group()
@admin()
async def reward(ctx):
    if ctx.invoked_subcommand is None:
        await ctx.send(
            "**Reward Commands**\n"
            "`.reward add <name> <chance>`\n"
            "`.reward remove <id>`\n"
            "`.reward list`"
        )


@reward.command(name="add")
async def reward_add(ctx, name: str, chance: float):
    if chance <= 0 or chance > 100:
        return await ctx.send("❌ Chance must be between 0–100")

    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT SUM(chance) FROM rewards")
        total = (await cur.fetchone())[0] or 0

        if total + chance > 100:
            return await ctx.send(
                f"❌ Total chance exceeds 100% (current {total}%)"
            )

        await db.execute(
            "INSERT INTO rewards (name, chance) VALUES (?, ?)",
            (name, chance)
        )
        await db.commit()

    await ctx.send(f"✅ Reward **{name}** added ({chance}%)")


@reward.command(name="remove")
async def reward_remove(ctx, reward_id: int):
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM rewards WHERE id=?", (reward_id,))
        await db.commit()

    await ctx.send(f"🗑️ Reward `{reward_id}` removed")


@reward.command(name="list")
async def reward_list(ctx):
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute("SELECT id, name, chance FROM rewards")
        rows = await cur.fetchall()

    if not rows:
        return await ctx.send("❌ No rewards set")

    text = "🎁 **SPIN REWARDS**\n\n"
    for r_id, name, chance in rows:
        text += f"`{r_id}` • **{name}** — `{chance}%`\n"

    await ctx.send(text)
@bot.command()
@admin()
async def addchips(ctx, member: discord.Member, amount: int):
    if amount <= 0:
        return await ctx.send("❌ Amount must be positive")

    bal, _ = await get_user(member.id)
    bal += amount
    await update_balance(member.id, bal)

    await ctx.send(
        f"✅ Added **{amount} chips** to {member.mention}\n"
        f"💰 New balance: `{bal}`"
    )


@bot.command()
@admin()
async def removechips(ctx, member: discord.Member, amount: int):
    if amount <= 0:
        return await ctx.send("❌ Amount must be positive")

    bal, _ = await get_user(member.id)
    bal = max(0, bal - amount)
    await update_balance(member.id, bal)

    await ctx.send(
        f"❌ Removed **{amount} chips** from {member.mention}\n"
        f"💰 New balance: `{bal}`"
    )
@bot.command()
@admin()
async def wipe(ctx):
    async with aiosqlite.connect(DB) as db:
        await db.execute("DELETE FROM users")
        await db.commit()
    await ctx.send("🔥 **ECONOMY WIPED**")

@bot.command()
@admin()
async def reset(ctx, member: discord.Member):
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO users (user_id, balance) VALUES (?, 1000)",
            (member.id,)
        )
        await db.commit()
    await ctx.send(f"♻️ Reset economy for {member.mention}")

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

bot.run(TOKEN)
