import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import io
import os

TOKEN = os.environ["BOT_TOKEN"]
GUILD_ID = 1506423185359376505

# ─── Role IDs ──────────────────────────────────────────────────────────────────
ROLE = {
    "middleman":    1506425490968285345,
    "senior_mid":   1506425889775161455,
    "mid_manager":  1506428763582369914,
    "co_founder":   1506428989433057300,
    "admin":        1506428248459051058,
    "head_admin":   1506427800926687362,
    "lead_coord":   1506427451771977778,
    "moderator":    1506427030408007932,
    "head_mod":     1506426732436127915,
    "chief_exec":   1506429150641258526,
    "director":     1506429326801899701,
    "president":    1506429594058887178,
    "index_mm":     1506469397932544110,
    "ban_perms":    1506446028566429809,
}

# Ordered lowest → highest for /managerole hierarchy
HIERARCHY = [
    ROLE["middleman"],
    ROLE["senior_mid"],
    ROLE["mid_manager"],
    ROLE["head_mod"],
    ROLE["moderator"],
    ROLE["lead_coord"],
    ROLE["head_admin"],
    ROLE["admin"],
    ROLE["co_founder"],
    ROLE["chief_exec"],
    ROLE["director"],
    ROLE["president"],
]

# Who can promote up to what ceiling
PROMOTE_CEILING = {
    ROLE["mid_manager"]:  ROLE["middleman"],    # Manager can give Middleman
    ROLE["co_founder"]:   ROLE["senior_mid"],   # Co-Founder can give Senior MM and below
    ROLE["chief_exec"]:   ROLE["co_founder"],   # Chief Ex can give Co-Founder and below
    ROLE["director"]:     ROLE["chief_exec"],   # Director can give Chief Ex and below
    ROLE["president"]:    ROLE["director"],     # President can give all
}

# ─── Channel IDs ───────────────────────────────────────────────────────────────
CH = {
    "mm_setup":       1506432615765250128,
    "mm_ticket_cat":  1506470200654958693,
    "support_setup":  1506431713226788984,
    "support_cat":    1506470281483517962,
    "index_setup":    1506435422887215104,
    "index_cat":      1506470391911157822,
    "transcript_ch":  1506450229438972058,
    "ban_log":        1506450482237931520,
    "role_log":       1506450406505582693,
}

FOOTER = "Powered by BloxExchange Middleman Service"

# Staff groups
ALL_STAFF    = list(ROLE.values())
TICKET_STAFF = ALL_STAFF
MM_CLAIM     = [ROLE["middleman"], ROLE["senior_mid"], ROLE["mid_manager"],
                ROLE["co_founder"], ROLE["admin"], ROLE["head_admin"],
                ROLE["lead_coord"], ROLE["moderator"], ROLE["head_mod"],
                ROLE["chief_exec"], ROLE["director"], ROLE["president"]]
INDEX_CLAIM  = [ROLE["index_mm"]]
ADMIN_ROLES  = [ROLE["co_founder"], ROLE["chief_exec"], ROLE["director"], ROLE["president"]]
MM_PING      = [ROLE["middleman"]]

active_trades: dict = {}

# ─── Helpers ───────────────────────────────────────────────────────────────────

def has_role(member: discord.Member, role_ids: list) -> bool:
    return any(r.id in role_ids for r in member.roles)

def top_role_id(member: discord.Member):
    for rid in reversed(HIERARCHY):
        if any(r.id == rid for r in member.roles):
            return rid
    return None

def can_manage_role(executor: discord.Member, target_role_id: int) -> bool:
    top = top_role_id(executor)
    if top not in PROMOTE_CEILING:
        return False
    ceiling     = PROMOTE_CEILING[top]
    ceiling_idx = HIERARCHY.index(ceiling)
    try:
        target_idx = HIERARCHY.index(target_role_id)
    except ValueError:
        return False
    return 0 <= target_idx <= ceiling_idx

async def make_transcript(channel: discord.TextChannel) -> io.BytesIO:
    lines = []
    async for msg in channel.history(limit=None, oldest_first=True):
        ts = msg.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        lines.append(f"[{ts}] {msg.author} ({msg.author.id}): {msg.content}")
        for e in msg.embeds:
            if e.title:       lines.append(f"  [EMBED TITLE] {e.title}")
            if e.description: lines.append(f"  [EMBED DESC]  {e.description}")
            for f in e.fields:
                lines.append(f"  [{f.name}] {f.value}")
    return io.BytesIO("\n".join(lines).encode())

def ts_now() -> str:
    return discord.utils.utcnow().strftime("%A, %B %d, %Y %I:%M %p")

def mm_overwrites(guild: discord.Guild, opener: discord.Member) -> dict:
    ow = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        opener: discord.PermissionOverwrite(read_messages=True, send_messages=True),
    }
    for rid in MM_CLAIM:
        r = guild.get_role(rid)
        if r:
            ow[r] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
    return ow

def support_overwrites(guild: discord.Guild, opener: discord.Member) -> dict:
    ow = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        opener: discord.PermissionOverwrite(read_messages=True, send_messages=True),
    }
    # All middlemen can see support tickets
    for rid in ALL_STAFF:
        r = guild.get_role(rid)
        if r:
            ow[r] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
    return ow

def index_overwrites(guild: discord.Guild, opener: discord.Member) -> dict:
    ow = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        opener: discord.PermissionOverwrite(read_messages=True, send_messages=True),
    }
    for rid in INDEX_CLAIM + ADMIN_ROLES:
        r = guild.get_role(rid)
        if r:
            ow[r] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
    return ow

async def do_close(interaction: discord.Interaction, claimed_by: str = None, ticket_creator: str = None):
    if not has_role(interaction.user, TICKET_STAFF):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    ch  = interaction.channel
    buf = await make_transcript(ch)
    tr_ch = interaction.guild.get_channel(CH["transcript_ch"])
    if tr_ch:
        embed = discord.Embed(color=0x2b2d31, title=f"Transcript for Ticket #{ch.name}")
        embed.add_field(name="Ticket Creator", value=ticket_creator or "Unknown", inline=False)
        embed.add_field(name="Claimed By",     value=claimed_by or "Unknown",     inline=False)
        embed.add_field(name="Closed By",      value=interaction.user.mention,    inline=False)
        embed.add_field(name="Closed At",      value=ts_now(),                    inline=False)
        embed.set_footer(text=FOOTER)
        await tr_ch.send(embed=embed,
                         file=discord.File(buf, filename=f"transcript-{ch.name}.txt"))
    await interaction.response.send_message("Closing ticket in 5 seconds…")
    await asyncio.sleep(5)
    await ch.delete()

# ─── Bot Setup ─────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
GUILD = discord.Object(id=GUILD_ID)

# ─── Ticket Views ──────────────────────────────────────────────────────────────

class MMTicketView(discord.ui.View):
    def __init__(self, creator: str = "Unknown"):
        super().__init__(timeout=None)
        self.claimed_by = None
        self.creator    = creator

    @discord.ui.button(label="Claimed", style=discord.ButtonStyle.success,
                       emoji="✅", custom_id="v:mm_claim")
    async def claim(self, interaction: discord.Interaction, btn: discord.ui.Button):
        if not has_role(interaction.user, MM_CLAIM):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return
        self.claimed_by   = interaction.user.mention
        btn.disabled      = True
        btn.label         = "Claimed"
        await interaction.message.edit(view=self)
        embed = discord.Embed(color=0x57f287, title="✅ Ticket Claimed")
        embed.description = f"{interaction.user.mention} will be your Middleman for today."
        embed.set_footer(text=FOOTER)
        await interaction.response.send_message(embed=embed)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger,
                       emoji="🔒", custom_id="v:mm_close")
    async def close(self, interaction: discord.Interaction, btn: discord.ui.Button):
        await do_close(interaction, claimed_by=self.claimed_by, ticket_creator=self.creator)


class IndexTicketView(discord.ui.View):
    def __init__(self, creator: str = "Unknown"):
        super().__init__(timeout=None)
        self.claimed_by = None
        self.creator    = creator

    @discord.ui.button(label="Claimed", style=discord.ButtonStyle.success,
                       emoji="✅", custom_id="v:index_claim")
    async def claim(self, interaction: discord.Interaction, btn: discord.ui.Button):
        if not has_role(interaction.user, INDEX_CLAIM + ADMIN_ROLES):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return
        self.claimed_by = interaction.user.mention
        btn.disabled    = True
        btn.label       = "Claimed"
        await interaction.message.edit(view=self)
        embed = discord.Embed(color=0x57f287, title="✅ Index Ticket Claimed")
        embed.description = f"{interaction.user.mention} will be your Indexer for today."
        embed.set_footer(text=FOOTER)
        await interaction.response.send_message(embed=embed)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger,
                       emoji="🔒", custom_id="v:index_close")
    async def close(self, interaction: discord.Interaction, btn: discord.ui.Button):
        await do_close(interaction, claimed_by=self.claimed_by, ticket_creator=self.creator)


class SupportTicketView(discord.ui.View):
    def __init__(self, creator: str = "Unknown"):
        super().__init__(timeout=None)
        self.creator = creator

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger,
                       emoji="🔒", custom_id="v:support_close")
    async def close(self, interaction: discord.Interaction, btn: discord.ui.Button):
        await do_close(interaction, ticket_creator=self.creator)


# ─── Panel Views (buttons that open tickets) ───────────────────────────────────

class MMRequestView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Request Middleman", style=discord.ButtonStyle.primary,
                       custom_id="v:mm_request")
    async def request(self, interaction: discord.Interaction, btn: discord.ui.Button):
        guild = interaction.guild
        cat   = guild.get_channel(CH["mm_ticket_cat"])
        if cat is None:
            await interaction.response.send_message("Ticket category not found.", ephemeral=True)
            return
        for c in cat.channels:
            if c.topic == str(interaction.user.id):
                await interaction.response.send_message(
                    f"You already have an open ticket: {c.mention}", ephemeral=True)
                return
        ch = await guild.create_text_channel(
            name=f"ticket-{interaction.user.name}",
            category=cat,
            overwrites=mm_overwrites(guild, interaction.user),
            topic=str(interaction.user.id),
        )
        embed = discord.Embed(color=0x2b2d31, title="🎫 Middleman Ticket")
        embed.description = (
            f"{interaction.user.mention}, Thank you for using our middleman services.\n\n"
            "Please wait for a middleman to assist you.\n\n"
            "If you have any questions, please let a staff member know."
        )
        embed.set_footer(text=FOOTER)
        pings = " ".join(f"<@&{rid}>" for rid in MM_PING) + f" {interaction.user.mention}"
        view  = MMTicketView(creator=interaction.user.mention)
        await ch.send(content=pings, embed=embed, view=view)
        await interaction.response.send_message(f"Ticket created: {ch.mention}", ephemeral=True)


class SupportRequestView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Support", style=discord.ButtonStyle.danger,
                       emoji="🎫", custom_id="v:support_request")
    async def support(self, interaction: discord.Interaction, btn: discord.ui.Button):
        guild = interaction.guild
        cat   = guild.get_channel(CH["support_cat"])
        if cat is None:
            await interaction.response.send_message("Support category not found.", ephemeral=True)
            return
        for c in cat.channels:
            if c.topic == str(interaction.user.id):
                await interaction.response.send_message(
                    f"You already have an open ticket: {c.mention}", ephemeral=True)
                return

        # Modal to collect info
        modal = SupportModal(guild=guild, opener=interaction.user)
        await interaction.response.send_modal(modal)


class SupportModal(discord.ui.Modal, title="Support Ticket | BloxExchange"):
    what  = discord.ui.TextInput(label="What would you like help with?",
                                  style=discord.TextStyle.paragraph, required=True)
    urgency = discord.ui.TextInput(label="How urgent is this? (1-10)",
                                    style=discord.TextStyle.short, required=True, max_length=2)

    def __init__(self, guild: discord.Guild, opener: discord.Member):
        super().__init__()
        self.guild  = guild
        self.opener = opener

    async def on_submit(self, interaction: discord.Interaction):
        cat = self.guild.get_channel(CH["support_cat"])
        ch  = await self.guild.create_text_channel(
            name=f"support-{self.opener.name}",
            category=cat,
            overwrites=support_overwrites(self.guild, self.opener),
            topic=str(self.opener.id),
        )
        embed = discord.Embed(color=0x2b2d31, title="🎫 Support Ticket")
        embed.description = (
            f"{self.opener.mention}, a staff member will be with you shortly.\n\n"
            "**Create a ticket if you need support for:**\n"
            "• Report a scammer\n"
            "• Report a middleman\n"
            "• Need help creating a ticket\n"
            "• Other"
        )
        embed.add_field(name="Issue",   value=str(self.what),    inline=False)
        embed.add_field(name="Urgency", value=str(self.urgency), inline=False)
        embed.set_footer(text=FOOTER)
        view = SupportTicketView(creator=self.opener.mention)
        await ch.send(content=self.opener.mention, embed=embed, view=view)
        await interaction.response.send_message(f"Support ticket created: {ch.mention}", ephemeral=True)


class IndexBaseSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Diamond Base",    description="5+ Garamas or $20",  emoji="💎"),
            discord.SelectOption(label="Rainbow Base",    description="5+ Garamas or $20",  emoji="🌈"),
            discord.SelectOption(label="Candy Base",      description="3+ Garamas or $8",   emoji="🍬"),
            discord.SelectOption(label="Lava Base",       description="4+ Garamas or $10",  emoji="🌋"),
            discord.SelectOption(label="Galaxy Base",     description="4+ Garamas or $10",  emoji="🌌"),
            discord.SelectOption(label="Gold Base",       description="4+ Garamas or $10",  emoji="⭐"),
            discord.SelectOption(label="Yin Yang Base",   description="5+ Garamas or $15",  emoji="☯️"),
            discord.SelectOption(label="Radioactive Base",description="5+ Garamas or $17",  emoji="☢️"),
            discord.SelectOption(label="Cursed Base",     description="5+ Garamas or $17",  emoji="💀"),
            discord.SelectOption(label="Divine Base",     description="8+ Garamas or $25",  emoji="✨"),
            discord.SelectOption(label="Halloween Base",  description="$4 or 1-2 Garamas",  emoji="🎃"),
            discord.SelectOption(label="Christmas Base",  description="$4 or 1-2 Garamas",  emoji="🎄"),
            discord.SelectOption(label="Aquatic Base",    description="$4 or 1-2 Garamas",  emoji="🌊"),
            discord.SelectOption(label="Easter Base",     description="$4 or 1-2 Garamas",  emoji="🐣"),
        ]
        super().__init__(placeholder="Select a base to request an index...",
                         options=options, custom_id="v:index_select")

    async def callback(self, interaction: discord.Interaction):
        base  = self.values[0]
        guild = interaction.guild
        cat   = guild.get_channel(CH["index_cat"])
        if cat is None:
            await interaction.response.send_message("Index category not found.", ephemeral=True)
            return
        for c in cat.channels:
            if c.topic == str(interaction.user.id):
                await interaction.response.send_message(
                    f"You already have an open index ticket: {c.mention}", ephemeral=True)
                return
        ch = await guild.create_text_channel(
            name=f"index-{interaction.user.name}",
            category=cat,
            overwrites=index_overwrites(guild, interaction.user),
            topic=str(interaction.user.id),
        )
        embed = discord.Embed(color=0x2b2d31, title="📋 Index Ticket")
        embed.description = (
            f"{interaction.user.mention}, thank you for requesting an index!\n\n"
            f"**Selected Base:** {base}\n\n"
            "One of our professional indexers will assist you shortly."
        )
        embed.set_footer(text=FOOTER)
        pings = " ".join(f"<@&{rid}>" for rid in INDEX_CLAIM) + f" {interaction.user.mention}"
        view  = IndexTicketView(creator=interaction.user.mention)
        await ch.send(content=pings, embed=embed, view=view)
        await interaction.response.send_message(f"Index ticket created: {ch.mention}", ephemeral=True)


class IndexRequestView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(IndexBaseSelect())


# ─── Trade Confirmation View ───────────────────────────────────────────────────

class TradeView(discord.ui.View):
    def __init__(self, t1: int, t2: int, mm: int):
        super().__init__(timeout=None)
        self.t1        = t1
        self.t2        = t2
        self.mm        = mm
        self.confirmed: set = set()
        b1 = discord.ui.Button(label="✅ Confirm Trade (Trader 1)",
                                style=discord.ButtonStyle.success,
                                custom_id=f"trade_t1_{t1}_{t2}")
        b2 = discord.ui.Button(label="✅ Confirm Trade (Trader 2)",
                                style=discord.ButtonStyle.success,
                                custom_id=f"trade_t2_{t1}_{t2}")
        b1.callback = self._confirm_t1
        b2.callback = self._confirm_t2
        self.add_item(b1)
        self.add_item(b2)

    async def _confirm_t1(self, interaction: discord.Interaction):
        if interaction.user.id != self.t1:
            await interaction.response.send_message("You are not Trader 1.", ephemeral=True)
            return
        self.confirmed.add(self.t1)
        await self._refresh(interaction)

    async def _confirm_t2(self, interaction: discord.Interaction):
        if interaction.user.id != self.t2:
            await interaction.response.send_message("You are not Trader 2.", ephemeral=True)
            return
        self.confirmed.add(self.t2)
        await self._refresh(interaction)

    async def _refresh(self, interaction: discord.Interaction):
        guild = interaction.guild
        m1  = guild.get_member(self.t1)
        m2  = guild.get_member(self.t2)
        mm  = guild.get_member(self.mm)
        t1c = self.t1 in self.confirmed
        t2c = self.t2 in self.confirmed
        old = interaction.message.embeds[0]
        details = old.fields[0].value if old.fields else "—"

        if t1c and t2c:
            embed = discord.Embed(color=0x57f287, title="✅ Trade Confirmed")
            embed.description = "Both traders have confirmed. Please proceed with the rest of the trade."
            embed.add_field(name="🔵 Trader 1",  value=m1.mention if m1 else str(self.t1), inline=True)
            embed.add_field(name="🔵 Trader 2",  value=m2.mention if m2 else str(self.t2), inline=True)
            embed.add_field(name="🛡️ Middleman", value=mm.mention if mm else str(self.mm), inline=False)
            embed.add_field(name="✅ Status",     value="Both traders confirmed", inline=False)
            embed.set_footer(text=FOOTER)
            for item in self.children:
                item.disabled = True
                item.label    = "Trade Confirmed"
            active_trades.pop(interaction.message.id, None)
        else:
            t1d = "🟢" if t1c else "🔴"
            t2d = "🟢" if t2c else "🔴"
            embed = discord.Embed(color=0x2b2d31, title="✅ Trade Confirmation")
            embed.description = "In order to continue this trade, both traders should confirm the trade."
            embed.add_field(name="📊 Trade Information", value=details, inline=False)
            embed.add_field(name="🔵 Trader 1",  value=m1.mention if m1 else str(self.t1), inline=True)
            embed.add_field(name="🔵 Trader 2",  value=m2.mention if m2 else str(self.t2), inline=True)
            embed.add_field(name="🛡️ Middleman", value=mm.mention if mm else str(self.mm), inline=False)
            embed.add_field(name="⏳ Awaiting Confirmation",
                            value=f"{t1d} {m1.mention if m1 else str(self.t1)}\n"
                                  f"{t2d} {m2.mention if m2 else str(self.t2)}",
                            inline=False)
            embed.set_footer(text=FOOTER)
            for item in self.children:
                if "t1" in item.custom_id and t1c:
                    item.label, item.disabled = "Confirmed (Trader 1)", True
                if "t2" in item.custom_id and t2c:
                    item.label, item.disabled = "Confirmed (Trader 2)", True

        await interaction.message.edit(embed=embed, view=self)
        await interaction.response.defer()


# ─── Slash Commands ────────────────────────────────────────────────────────────

@bot.tree.command(name="setupmiddleman", description="Post the MM request panel", guild=GUILD)
async def setup_mm(interaction: discord.Interaction):
    if not has_role(interaction.user, ADMIN_ROLES):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    embed = discord.Embed(color=0x2b2d31, title="🛡️ BloxExchange | Welcome to Our MM Service")
    embed.add_field(
        name="• Request Middleman",
        value="Read our mm-tos first, then tap **Request Middleman** and fill out the form.",
        inline=False)
    embed.add_field(
        name="• Vouch Required",
        value="You must vouch your middleman after the trade in #vouches. Failing to do so within 24 hours results in a **Blacklist** from our MM Service.",
        inline=False)
    embed.add_field(
        name="• Troll Tickets",
        value="Creating any form of troll tickets will result in a **Middleman ban**.",
        inline=False)
    embed.add_field(
        name="• Disclaimer",
        value="We are **NOT** responsible for anything that happens after the trade is done.",
        inline=False)
    embed.set_footer(text=FOOTER)
    await interaction.channel.send(embed=embed, view=MMRequestView())
    await interaction.response.send_message("✅ MM panel deployed.", ephemeral=True)


@bot.tree.command(name="setupsupport", description="Post the Support request panel", guild=GUILD)
async def setup_support(interaction: discord.Interaction):
    if not has_role(interaction.user, ADMIN_ROLES):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    embed = discord.Embed(color=0x2b2d31, title="🛡️ BloxExchange | Support")
    embed.add_field(
        name="• Create a ticket if you need support for:",
        value="• Report a scammer\n• Report a middleman\n• Need help creating a ticket\n• Other",
        inline=False)
    embed.set_footer(text=FOOTER)
    await interaction.channel.send(embed=embed, view=SupportRequestView())
    await interaction.response.send_message("✅ Support panel deployed.", ephemeral=True)


@bot.tree.command(name="setupindex", description="Post the Index request panel", guild=GUILD)
async def setup_index(interaction: discord.Interaction):
    if not has_role(interaction.user, ADMIN_ROLES):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    embed = discord.Embed(color=0x2b2d31, title="📋 BloxExchange | Indexing Service")
    embed.description = (
        "Request an indexing service by selecting one of the available bases.\n"
        "One of our professional indexers will assist you in completing it!"
    )
    embed.add_field(name="Available Bases & Prices", value=(
        "💎 Diamond Base — 5+ Garamas or $20\n"
        "🌈 Rainbow Base — 5+ Garamas or $20\n"
        "🍬 Candy Base — 3+ Garamas or $8\n"
        "🌋 Lava Base — 4+ Garamas or $10\n"
        "🌌 Galaxy Base — 4+ Garamas or $10\n"
        "⭐ Gold Base — 4+ Garamas or $10\n"
        "☯️ Yin Yang Base — 5+ Garamas or $15\n"
        "☢️ Radioactive Base — 5+ Garamas or $17\n"
        "💀 Cursed Base — 5+ Garamas or $17\n"
        "✨ Divine Base — 8+ Garamas or $25\n"
        "🎃 Halloween Base — $4 or 1-2 Garamas\n"
        "🎄 Christmas Base — $4 or 1-2 Garamas\n"
        "🌊 Aquatic Base — $4 or 1-2 Garamas\n"
        "🐣 Easter Base — $4 or 1-2 Garamas"
    ), inline=False)
    embed.add_field(name="Note", value="Collateral may be required, the price is negotiable.", inline=False)
    embed.set_footer(text=FOOTER)
    await interaction.channel.send(embed=embed, view=IndexRequestView())
    await interaction.response.send_message("✅ Index panel deployed.", ephemeral=True)


@bot.tree.command(name="add", description="Add a user to this ticket", guild=GUILD)
@app_commands.describe(user="User to add")
async def cmd_add(interaction: discord.Interaction, user: discord.Member):
    if not has_role(interaction.user, TICKET_STAFF):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    await interaction.channel.set_permissions(user, read_messages=True, send_messages=True)
    embed = discord.Embed(color=0x57f287, title="✅ User Added to Ticket")
    embed.description = f"{user.mention} has been added to this ticket by {interaction.user.mention}"
    embed.set_footer(text=FOOTER)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="close", description="Close this ticket", guild=GUILD)
async def cmd_close(interaction: discord.Interaction):
    await do_close(interaction)


@bot.tree.command(name="transfer", description="Transfer this ticket to another middleman", guild=GUILD)
@app_commands.describe(user="Middleman to transfer to")
async def cmd_transfer(interaction: discord.Interaction, user: discord.Member):
    if not has_role(interaction.user, TICKET_STAFF):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    await interaction.channel.set_permissions(user, read_messages=True, send_messages=True)
    embed = discord.Embed(color=0x2b2d31, title="🔄 Ticket Transferred")
    embed.description = f"This ticket has been transferred to {user.mention}"
    embed.set_footer(text=FOOTER)
    await interaction.response.send_message(content=user.mention, embed=embed)


@bot.tree.command(name="confirm", description="Start a trade confirmation", guild=GUILD)
@app_commands.describe(trader1="First trader", trader2="Second trader", details="Trade details")
async def cmd_confirm(interaction: discord.Interaction,
                      trader1: discord.Member, trader2: discord.Member, details: str):
    if not has_role(interaction.user, TICKET_STAFF):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    view  = TradeView(t1=trader1.id, t2=trader2.id, mm=interaction.user.id)
    embed = discord.Embed(color=0x2b2d31, title="✅ Trade Confirmation")
    embed.description = "In order to continue this trade, both traders should confirm the trade."
    embed.add_field(name="📊 Trade Information", value=details, inline=False)
    embed.add_field(name="🔵 Trader 1",  value=trader1.mention, inline=True)
    embed.add_field(name="🔵 Trader 2",  value=trader2.mention, inline=True)
    embed.add_field(name="🛡️ Middleman", value=interaction.user.mention, inline=False)
    embed.add_field(name="⏳ Awaiting Confirmation",
                    value=f"🔴 {trader1.mention}\n🔴 {trader2.mention}", inline=False)
    embed.set_footer(text=FOOTER)
    await interaction.response.send_message(
        content=f"{trader1.mention} {trader2.mention}", embed=embed, view=view)
    msg = await interaction.original_response()
    active_trades[msg.id] = view


@bot.tree.command(name="managerole", description="Promote or demote a user", guild=GUILD)
@app_commands.describe(action="add or remove", user="Target user", role="Role", reason="Reason")
@app_commands.choices(action=[
    app_commands.Choice(name="add",    value="add"),
    app_commands.Choice(name="remove", value="remove"),
])
async def cmd_managerole(interaction: discord.Interaction, action: str,
                         user: discord.Member, role: discord.Role, reason: str):
    if not can_manage_role(interaction.user, role.id):
        await interaction.response.send_message(
            "You don't have permission to manage that role.", ephemeral=True)
        return
    if action == "add":
        await user.add_roles(role, reason=reason)
        title, color = "Role Given ✅", 0x57f287
    else:
        await user.remove_roles(role, reason=reason)
        title, color = "Role Removed ❌", 0xed4245
    embed = discord.Embed(color=color, title=title)
    embed.add_field(name="Actioned By", value=f"{interaction.user} ({interaction.user.id})", inline=False)
    embed.add_field(name="Target User", value=f"{user} ({user.id})",                         inline=False)
    embed.add_field(name="Role",        value=role.name,                                      inline=False)
    embed.add_field(name="Reason",      value=reason,                                         inline=False)
    embed.add_field(name="Time",        value=ts_now(),                                       inline=False)
    embed.set_footer(text=FOOTER)
    await interaction.response.send_message(embed=embed)
    log_ch = interaction.guild.get_channel(CH["role_log"])
    if log_ch:
        await log_ch.send(embed=embed)


@bot.tree.command(name="manageban", description="Ban or unban a user", guild=GUILD)
@app_commands.describe(action="ban or unban", user="Target user", reason="Reason")
@app_commands.choices(action=[
    app_commands.Choice(name="ban",   value="ban"),
    app_commands.Choice(name="unban", value="unban"),
])
async def cmd_manageban(interaction: discord.Interaction, action: str,
                        user: discord.Member, reason: str):
    if not has_role(interaction.user, [ROLE["ban_perms"]]):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    roles_owned = [r.name for r in user.roles if r.name != "@everyone"]
    if action == "ban":
        await user.ban(reason=reason)
        title, color = "User Banned 🚫", 0xed4245
    else:
        await interaction.guild.unban(discord.Object(id=user.id), reason=reason)
        title, color = "User Unbanned ✅", 0x57f287
    embed = discord.Embed(color=color, title=title)
    embed.add_field(name="Actioned By",  value=f"{interaction.user} ({interaction.user.id})", inline=False)
    embed.add_field(name="Target User",  value=f"{user} ({user.id})",                          inline=False)
    embed.add_field(name="Roles Owned",  value=", ".join(roles_owned) if roles_owned else "None", inline=False)
    embed.add_field(name="Reason",       value=reason,                                          inline=False)
    embed.add_field(name="Time",         value=ts_now(),                                        inline=False)
    embed.set_footer(text=FOOTER)
    await interaction.response.send_message(embed=embed)
    log_ch = interaction.guild.get_channel(CH["ban_log"])
    if log_ch:
        await log_ch.send(embed=embed)


# ─── Info Commands ─────────────────────────────────────────────────────────────

@bot.tree.command(name="rules", description="Display BloxExchange Rules", guild=GUILD)
async def cmd_rules(interaction: discord.Interaction):
    embed = discord.Embed(
        title="📋 BloxExchange Marketplace | Rules & Guidelines",
        color=0x2b2d31
    )
    embed.add_field(name="1. 📜 Follow Discord ToS and Guidelines",
        value="We're on Discord's platform, therefore we'll automatically follow their regulations. Make sure you don't violate their terms and guidelines.",
        inline=False)
    embed.add_field(name="2. 🔒 Personal Information",
        value="Do not post personal information about anyone without their consent. Any impersonation within MMs or other members are also not allowed.",
        inline=False)
    embed.add_field(name="3. ✅ Content Appropriate",
        value="All content should be safe for work and appropriate for the server's community. No NSFW, graphic, or disturbing content.",
        inline=False)
    embed.add_field(name="4. 📌 Use the Correct Channels",
        value="Post messages, images, and discussions in the appropriate channels. Read channel descriptions and rules to avoid clutter.",
        inline=False)
    embed.add_field(name="5. 🚫 No Illegal Activities",
        value="Sharing, discussing, or promoting illegal activities is strictly prohibited. This includes piracy, hacking, and any other form of illegal behavior.",
        inline=False)
    embed.add_field(name="6. 🔐 Respect Privacy",
        value="Do not share personal information (yours or others') without consent. Respect everyone's privacy.",
        inline=False)
    embed.add_field(name="7. 🎭 No Impersonation",
        value="Do not impersonate other members, including server staff, celebrities, or other users.",
        inline=False)
    embed.add_field(name="8. 💬 Follow Discord's Terms of Service",
        value="All members must adhere to Discord's Terms of Service and Community Guidelines. https://discord.com/terms",
        inline=False)
    embed.add_field(name="9. 👂 Listen to Staff",
        value="Staff decisions are final. If you have issues or concerns, contact a staff member privately and respectfully.",
        inline=False)
    embed.add_field(name="10. ⚠️ We Are NOT Responsible",
        value="We are not responsible when one of our server ad buyers scams you and when one of our server ad buyers scams you, this means that going first in one of theirs trades is **YOUR OWN RISK!** If you get scammed by one of them DM an owner and we will take the ad down as fast as possible.",
        inline=False)
    embed.add_field(name="11. 📢 Server Ads",
        value="We do **NOT** refund purchased server ads, breaking the server's rules and getting banned will lead to an ad remove with no refunds.",
        inline=False)
    embed.set_footer(text=FOOTER)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="about", description="About BloxExchange", guild=GUILD)
async def cmd_about(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛡️ BloxExchange — About Us",
        color=0x2b2d31
    )
    embed.description = (
        "BloxExchange is a leading platform in the online gaming marketplace, providing a secure player-to-player trading "
        "experience for buyers and sellers of online gaming products. We connect traders across 250+ games and leading titles "
        "— all backed by our trusted Middleman service.\n\n"
        "With thousands of completed transactions and an **Excellent** rating on Trustpilot, we've built our reputation on "
        "security, speed, and trust."
    )
    embed.add_field(name="🔧 What We Do", value=(
        "• In-game items, accounts, currencies, and digital goods across **250+ games**\n"
        "• Instant secure trades with verified, vetted Middlemen\n"
        "• Peer-to-peer marketplace with scam protection\n"
        "• 24/7 Middleman availability for every transaction\n"
        "• Full dispute resolution and buyer/seller protection"
    ), inline=False)
    embed.add_field(name="🛡️ Middleman Services", value=(
        "Our professional Middleman service is available around the clock to ensure every transaction is **safe, fast, and scam-free.**\n"
        "Whether you're trading in-game items, accounts, or digital goods, our escrow system protects both parties throughout the entire process.\n"
        "Every Middleman is thoroughly vetted and every trade is monitored."
    ), inline=False)
    embed.add_field(name="💛 Our Promise", value=(
        "Security, speed, and trust. That's what we stand for.\n"
        "Every trade is monitored, every Middleman is vetted, and every customer matters."
    ), inline=False)
    embed.set_footer(text=FOOTER)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="faq", description="Frequently Asked Questions", guild=GUILD)
async def cmd_faq(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛡️ BloxExchange Marketplace | FAQ",
        color=0x2b2d31
    )
    embed.add_field(name="What is BloxExchange?",
        value="BloxExchange is a platform that provides a secure player-to-player marketplace for buyers and sellers of online gaming products. We provide a system for secure transactions — you do the rest. We have marketplaces for **250+ games** and leading titles!",
        inline=False)
    embed.add_field(name="How does the Middleman service work?",
        value="Our verified Middlemen act as trusted third parties to hold and transfer items/funds during a trade. This ensures both parties are protected throughout the entire deal.",
        inline=False)
    embed.add_field(name="Is it free to use?",
        value="Yes! Our Middleman service is completely free for standard trades. Simply open a ticket and request a Middleman.",
        inline=False)
    embed.add_field(name="How long does a trade take?",
        value="Most trades are completed within minutes. Our Middlemen are available 24/7 to assist you as quickly as possible.",
        inline=False)
    embed.add_field(name="What if something goes wrong?",
        value="Our team monitors every trade. If any issues arise, open a support ticket and our staff will investigate and resolve the matter promptly.",
        inline=False)
    embed.add_field(name="Where can I report a scammer?",
        value="Open a support ticket and provide all relevant proof. Our team will handle the report and take appropriate action.",
        inline=False)
    embed.set_footer(text=FOOTER)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="tos", description="BloxExchange Trading Terms of Service", guild=GUILD)
async def cmd_tos(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🚀 Trading Terms of Service",
        color=0x2b2d31
    )
    embed.add_field(name="1. Cross-Trading",
        value="Cross-trading is only allowed with server-approved middlemen. Violations will result in a warning. (3 warnings = mute)",
        inline=False)
    embed.add_field(name="2. Prohibited Statements",
        value="Statements like 'mm of my choice' or 'ngl' are not allowed during cross-trading and will result in warnings. (3 warnings = mute, further violations = ban)",
        inline=False)
    embed.add_field(name="3. Trading Locations",
        value="Cross-trading is only permitted in the #marketplace and #verified-market channels.",
        inline=False)
    embed.add_field(name="4. Middleman Violations",
        value="Suggesting a scam middleman or refusing to use trusted middlemen will result in an **instant ban**. If you find someone suggesting a scam server, please report them immediately in #support.",
        inline=False)
    embed.add_field(name="5. Illegal Trading",
        value="Trading illegal items is strictly prohibited and will result in an instant ban. This includes trading Discord Nitro, accounts, selling scripts or cheats for games, or anything else that violates Discord's Terms of Service.",
        inline=False)
    embed.add_field(name="6. Middleman Usage",
        value="Always use a middleman when you do a cross-trade, and ensure you follow our middleman TOS. Failure to comply may result in penalties. To use middle man go to the channel #request-mm.",
        inline=False)
    embed.add_field(name="7. Respectful Trading",
        value="Be kind and respectful towards all traders, especially new ones. Rude or toxic behavior may lead to warnings or bans.",
        inline=False)
    embed.set_footer(text=FOOTER)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="scamawareness", description="Scam Awareness Guide", guild=GUILD)
async def cmd_scam(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🛡️ BloxExchange | Scam Awareness Guide",
        color=0x2b2d31
    )
    embed.description = (
        "**Common Scams in Roblox and Discord Communities (2025 Guide)**\n\n"
        "This document outlines the most prevalent scams targeting Roblox players and Discord users. "
        "It aims to educate community members, moderators, and developers about identifying and preventing fraudulent activity."
    )
    embed.add_field(name="1. Free Robux and Giveaway Scams", value=(
        "**Purpose:** To steal Roblox account credentials or personal information.\n"
        "**Description:** Scammers promote 'Free Robux', 'limiteds', or 'Thaidless giveaways' through messages, Discord servers, or fake websites.\n\n"
        "**Warning Signs:**\n"
        "• External links that do not end with 'roblox.com'\n"
        "• Promises of large Robux rewards\n"
        "• Urgent or secretive messages"
    ), inline=False)
    embed.add_field(name="2. Impersonation of Roblox Staff or Developers", value=(
        "**Purpose:** To gain trust and extract sensitive information.\n"
        "**Description:** Individuals pretend to be Roblox employees, moderators, or official developers.\n\n"
        "**Warning Signs:**\n"
        "• Claims of being 'Roblox Support' or 'QA Tester'\n"
        "• Requests for login, verification, or cookie data\n"
        "• Use of staff-like usernames or fake verification badges"
    ), inline=False)
    embed.add_field(name="3. Limited Item and Robux Trading Scams", value=(
        "**Purpose:** To obtain items or currency through deceit.\n"
        "**Description:** Scammers propose trading limiteds or Robux outside Roblox's official systems.\n\n"
        "**Warning Signs:**\n"
        "• Offers of discounted limiteds or 'cheap headless' items\n"
        "• Use of 'trusted middleman'\n"
        "• Requests to trade through Discord rather than the Roblox trade system"
    ), inline=False)
    embed.set_footer(text=FOOTER)
    await interaction.response.send_message(embed=embed)




# ─── Mercy System ────────────────────────────────────────────────────────────

HITTER_ROLE_ID = 1506426276804689970

class MercyView(discord.ui.View):
    def __init__(self, target=None, author=None):
        super().__init__(timeout=60)
        self.target = target
        self.author = author

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):

        if self.target and interaction.user.id != self.target.id:
            return await interaction.response.send_message(
                "❌ Only the targeted user can respond.",
                ephemeral=True
            )

        role = interaction.guild.get_role(HITTER_ROLE_ID)

        if role:
            await interaction.user.add_roles(role)

        embed = discord.Embed(
            title="✅ Opportunity Accepted",
            description=f"{interaction.user.mention} has accepted the opportunity and has been verified.",
            color=discord.Color.green()
        )

        embed.set_footer(
            text=f"{FOOTER} • Today at {discord.utils.utcnow().strftime('%I:%M %p')}"
        )

        for child in self.children:
            child.disabled = True

            if child.label == "Accept":
                child.label = "Accepted"

        await interaction.response.edit_message(embed=embed, view=self)

        dm_embed = discord.Embed(
            title="💫 Hitting Tutorial",
            color=0x2b2d31
        )

        dm_embed.description = (
            "You're a hitter now. A hitter is someone that got scammed by us, "
            "and goes out to scam others. In other words, you're now a scammer."
        )

        dm_embed.add_field(
            name="❓ What should I do?",
            value=(
                "You need to go and advertise trades on other servers. "
                "Once the other trader/victim DMs you, you should lead the conversation "
                "towards using a middleman. Once they agree, you'll send them our server "
                "and create a ticket in <#1506432615765250128>. "
                "Once you create the ticket, a random middleman will come assist you."
            ),
            inline=False
        )

        dm_embed.add_field(
            name="💰 How do I get profit?",
            value=(
                "After you hit/scam for an item, you and the Middleman will split the item 50/50."
            ),
            inline=False
        )

        dm_embed.add_field(
            name="🤔 Can I become a middleman?",
            value=(
                "Absolutely, you can become a Middleman but it does not come free. "
                "Check <#1506442071110713424> to know the requirements to rank up."
            ),
            inline=False
        )

        dm_embed.add_field(
            name="📊 Keep in mind",
            value=(
                "Hits need to be posted in <#1506435849426833459> or else they will not count."
            ),
            inline=False
        )

        dm_embed.add_field(
            name="📖 Any guide for hitting?",
            value=(
                "We have a tutorial in <#1506436097779961927> "
                "to help with hitting."
            ),
            inline=False
        )

        dm_embed.add_field(
            name="ℹ️ Other info?",
            value=(
                "Check <#1506437071378841660> to make sure you're not breaking any rules."
            ),
            inline=False
        )

        dm_embed.set_footer(text=FOOTER)

        try:
            await interaction.user.send(embed=dm_embed)
        except discord.Forbidden:
            pass

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):

        if self.target and interaction.user.id != self.target.id:
            return await interaction.response.send_message(
                "❌ Only the targeted user can respond.",
                ephemeral=True
            )

        embed = discord.Embed(
            title="❌ Opportunity Declined",
            description=f"{interaction.user.mention} has declined the opportunity.",
            color=discord.Color.red()
        )

        embed.set_footer(
            text=f"{FOOTER} • Today at {discord.utils.utcnow().strftime('%I:%M %p')}"
        )

        for child in self.children:
            child.disabled = True

            if child.label == "Decline":
                child.label = "Declined"

        await interaction.response.edit_message(embed=embed, view=self)


MERCY_ROLES = [
    ROLE["moderator"],
    ROLE["head_mod"],
    ROLE["lead_coord"],
    ROLE["admin"],
    ROLE["head_admin"],
    ROLE["co_founder"],
    ROLE["chief_exec"],
    ROLE["director"],
    ROLE["president"],
]


@bot.tree.command(
    name="mercy",
    description="Send a mercy notification to a user",
    guild=GUILD
)
@app_commands.describe(user="User to target")
async def mercy(interaction: discord.Interaction, user: discord.Member):

    if not has_role(interaction.user, MERCY_ROLES):
        await interaction.response.send_message(
            "No permission.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    now_str = discord.utils.utcnow().strftime("%I:%M %p")

    scam_embed = discord.Embed(
        title="⚠️ Scam Notification",
        description=(
            "If you're seeing this, you've likely just been scammed — but this doesn't end how you think.\n\n"
            "Most people in this server started out the same way. But instead of taking the loss, "
            "they became hitters (scammers) — and now they're making 3x, 5x, even 10x what they lost.\n\n"
            "This is your chance to turn a setback into serious profit.\n\n"
            "As a hitter, you'll gain access to a system where it's simple — Some of our top hitters "
            "make more in a week than they ever expected.\n\n"
            "You now have access to the staff chat and other hitter channels. Head to the main guide channel to learn how to start.\n\n"
            "🔥 Every minute you wait is profit missed.\n\n"
            "Need help getting started? Ask in the support system channel.\n\n"
            "You've already been pulled in — now it's time to flip the script and come out ahead."
        ),
        color=0xed4245
    )

    scam_embed.set_footer(text=f"{FOOTER} • Today at {now_str}")

    await interaction.channel.send(
        content=user.mention,
        embed=scam_embed
    )

    offer_embed = discord.Embed(
        description=(
            f"{user.mention}, do you want to accept this opportunity and become a hitter?\n\n"
            "⏳ **You have 1 minute to respond. The decision is yours. Make it count.**"
        ),
        color=0xed4245
    )

    offer_embed.set_footer(text=f"{FOOTER} • Today at {now_str}")

    view = MercyView(
        target=user,
        author=interaction.user
    )

    await interaction.channel.send(
        embed=offer_embed,
        view=view
    )

    await interaction.followup.send(
        "✅ Mercy sent.",
        ephemeral=True
    )


# ─── On Ready ──────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} ({bot.user.id})")
    bot.add_view(MMRequestView())
    bot.add_view(SupportRequestView())
    bot.add_view(IndexRequestView())
    bot.add_view(MMTicketView())
    bot.add_view(IndexTicketView())
    bot.add_view(SupportTicketView())
    bot.tree.copy_global_to(guild=GUILD)
    synced = await bot.tree.sync(guild=GUILD)
    print(f"✅ Synced {len(synced)} commands to guild {GUILD_ID}")


bot.run(TOKEN)
