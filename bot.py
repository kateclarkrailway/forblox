import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import io
import os
import datetime

TOKEN = os.environ["BOT_TOKEN"]
GUILD_ID = 1506423185359376505

# ─── Role IDs ──────────────────────────────────────────────────────────────────
ROLE = {
    "middleman":    1506425490968285345,
    "head_mid":     1506425889775161455,
    "lead_mid":     1506426732436127915,
    "moderator":    1506427030408007932,
    "senior_mod":   1506427451771977778,
    "coordinator":  1506427800926687362,
    "administrator":1506428248459051058,
    "manager":      1506428763582369914,
    "co_founder":   1506428989433057300,
    "chief_exec":   1506429150641258526,
    "director":     1506429326801899701,
    "president":    1506429594058887178,
    "index_mm":     1506469397932544110,
    "ban_perms":    1506446028566429809,
}

# Ordered lowest → highest for /managerole hierarchy
HIERARCHY = [
    ROLE["middleman"],
    ROLE["head_mid"],
    ROLE["lead_mid"],
    ROLE["moderator"],
    ROLE["senior_mod"],
    ROLE["coordinator"],
    ROLE["administrator"],
    ROLE["manager"],
    ROLE["co_founder"],
    ROLE["chief_exec"],
    ROLE["director"],
    ROLE["president"],
]

# All hierarchy role IDs (for /temp and /fill)
HIERARCHY_IDS = HIERARCHY[:]

# Who can promote up to what ceiling
# co_founder can give head_mid and lead_mid only
# manager can give co_founder and below
# chief_exec can give manager and below
# director can give chief_exec and below
# president can give director and below
PROMOTE_CEILING = {
    ROLE["co_founder"]:  ROLE["lead_mid"],     # Co-Founder → up to lead_mid
    ROLE["manager"]:     ROLE["co_founder"],   # Manager → up to co_founder
    ROLE["chief_exec"]:  ROLE["manager"],      # Chief Exec → up to manager
    ROLE["director"]:    ROLE["chief_exec"],   # Director → up to chief_exec
    ROLE["president"]:   ROLE["director"],     # President → up to director
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
    "app_setup":      1508489626426544128,
    "app_cat":        1508551992652599356,
}

FOOTER = "Powered by Brxxks Blox Middleman Service"


# Staff groups
ALL_STAFF    = list(ROLE.values())
TICKET_STAFF = ALL_STAFF
MM_CLAIM     = [ROLE["middleman"], ROLE["head_mid"], ROLE["manager"],
                ROLE["co_founder"], ROLE["administrator"], ROLE["coordinator"],
                ROLE["senior_mod"], ROLE["moderator"], ROLE["lead_mid"],
                ROLE["chief_exec"], ROLE["director"], ROLE["president"]]
INDEX_CLAIM  = [ROLE["index_mm"]]
ADMIN_ROLES  = [ROLE["manager"], ROLE["co_founder"], ROLE["chief_exec"], ROLE["director"], ROLE["president"]]
SETUP_ROLE   = 1506430627501703249  # only role that can use setup + tos/rules/faq commands
MERCY_USE_ROLE = [ROLE["middleman"], ROLE["head_mid"], ROLE["lead_mid"]]  # all middleman roles can use /mercy
MM_PING      = [ROLE["middleman"]]

active_trades: dict = {}
ban_cooldowns: dict = {}  # user_id -> datetime of last use

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
    # co_founder can only give head_mid and lead_mid (not middleman)
    if top == ROLE["co_founder"]:
        return target_role_id in (ROLE["head_mid"], ROLE["lead_mid"])
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
                       emoji="🤝", custom_id="v:mm_claim")
    async def claim(self, interaction: discord.Interaction, btn: discord.ui.Button):
        if not has_role(interaction.user, MM_CLAIM):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return
        self.claimed_by   = interaction.user.mention
        btn.disabled      = True
        btn.label         = "Claimed"
        await interaction.message.edit(view=self)

        # Lock channel: only claimer + ticket creator can talk
        ch    = interaction.channel
        guild = interaction.guild

        creator_member = None
        if ch.topic and ch.topic.isdigit():
            creator_member = guild.get_member(int(ch.topic))

        new_overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user:   discord.PermissionOverwrite(read_messages=True, send_messages=True),
        }
        if creator_member:
            new_overwrites[creator_member] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        for rid in MM_CLAIM:
            r = guild.get_role(rid)
            if r and r not in new_overwrites:
                new_overwrites[r] = discord.PermissionOverwrite(read_messages=True, send_messages=False)

        await ch.edit(overwrites=new_overwrites)

        embed = discord.Embed(color=0x57f287, title="✅ Ticket Claimed")
        embed.description = f"{interaction.user.mention} will be your Middleman for today."
        embed.set_footer(text=FOOTER)
        await interaction.response.defer()
        await ch.send(embed=embed)

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
        modal = MMModal(guild=guild, opener=interaction.user)
        await interaction.response.send_modal(modal)


class MMModal(discord.ui.Modal, title="Middleman Ticket | Brxxks Blox"):
    trading_with = discord.ui.TextInput(
        label="Who are you trading with?",
        style=discord.TextStyle.short,
        placeholder="Enter their username or @mention",
        required=True
    )
    trade_details = discord.ui.TextInput(
        label="What is the trade?",
        style=discord.TextStyle.paragraph,
        placeholder="Describe what items/currency are being traded",
        required=True
    )

    def __init__(self, guild: discord.Guild, opener: discord.Member):
        super().__init__()
        self.guild  = guild
        self.opener = opener

    async def on_submit(self, interaction: discord.Interaction):
        cat = self.guild.get_channel(CH["mm_ticket_cat"])
        ch  = await self.guild.create_text_channel(
            name=f"ticket-{self.opener.name}",
            category=cat,
            overwrites=mm_overwrites(self.guild, self.opener),
            topic=str(self.opener.id),
        )
        embed = discord.Embed(color=0x2b2d31, title="🎫 Middleman Ticket")
        embed.description = (
            f"{self.opener.mention}, thank you for using our Middleman service!\n\n"
            "A Middleman will be with you shortly. Please do not share any items or currency until one has been assigned."
        )
        embed.add_field(name="👤 Trading With", value=str(self.trading_with),  inline=False)
        embed.add_field(name="📦 Trade Details", value=str(self.trade_details), inline=False)
        embed.set_footer(text=FOOTER)
        pings = " ".join(f"<@&{rid}>" for rid in MM_PING) + f" {self.opener.mention}"
        view  = MMTicketView(creator=self.opener.mention)
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


class SupportModal(discord.ui.Modal, title="Support Ticket | Brxxks Blox"):
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
    if not any(r.id == SETUP_ROLE for r in interaction.user.roles):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    embed = discord.Embed(color=0x2b2d31, title="🛡️ Brxxks Blox | Welcome to Our MM Service")
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
    if not any(r.id == SETUP_ROLE for r in interaction.user.roles):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    embed = discord.Embed(color=0x2b2d31, title="🛡️ Brxxks Blox | Support")
    embed.description = (
        "Need help? Our support team is available **24/7** to assist you with any issues you may have.\n\n"
        "Simply click the **Support** button below to open a private ticket with our staff."
    )
    embed.add_field(
        name="📋 When should I open a ticket?",
        value=(
            "• 🚨 Report a scammer\n"
            "• 🛡️ Report a Middleman\n"
            "• ❓ Need help creating a ticket\n"
            "• 💬 General questions or concerns\n"
            "• ⚠️ Dispute resolution\n"
            "• 🔒 Account or trade issues\n"
            "• 📢 Other"
        ),
        inline=False
    )
    embed.add_field(
        name="⚠️ Before You Open a Ticket",
        value=(
            "• Have all relevant **screenshots or proof** ready\n"
            "• Include the **username** of anyone involved\n"
            "• Be as detailed as possible — this helps us resolve your issue faster"
        ),
        inline=False
    )
    embed.add_field(
        name="⏱️ Response Time",
        value="Our staff aim to respond within **a few minutes**. Please be patient and do not open multiple tickets for the same issue.",
        inline=False
    )
    embed.set_footer(text=FOOTER)
    await interaction.channel.send(embed=embed, view=SupportRequestView())
    await interaction.response.send_message("✅ Support panel deployed.", ephemeral=True)


@bot.tree.command(name="setupindex", description="Post the Index request panel", guild=GUILD)
async def setup_index(interaction: discord.Interaction):
    if not any(r.id == SETUP_ROLE for r in interaction.user.roles):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    embed = discord.Embed(color=0x2b2d31, title="📋 Brxxks Blox | Indexing Service")
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
    ch = interaction.channel

    # Give new middleman full access
    await ch.set_permissions(user, read_messages=True, send_messages=True)

    # Remove send_messages from the person transferring (keep read only)
    await ch.set_permissions(interaction.user, read_messages=True, send_messages=False)

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
    now = datetime.datetime.utcnow()
    last = ban_cooldowns.get(interaction.user.id)
    if last and (now - last).total_seconds() < 3600:
        remaining = 3600 - int((now - last).total_seconds())
        mins, secs = divmod(remaining, 60)
        await interaction.response.send_message(
            f"⏳ You're on cooldown. Try again in **{mins}m {secs}s**.", ephemeral=True)
        return
    ban_cooldowns[interaction.user.id] = now
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

@bot.tree.command(name="rules", description="Display Brxxks Blox Rules", guild=GUILD)
async def cmd_rules(interaction: discord.Interaction):
    if not any(r.id == SETUP_ROLE for r in interaction.user.roles):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    embed = discord.Embed(
        title="📋 Brxxks Blox Marketplace | Rules & Guidelines",
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
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ Done.", ephemeral=True)



@bot.tree.command(name="faq", description="Frequently Asked Questions", guild=GUILD)
async def cmd_faq(interaction: discord.Interaction):
    if not any(r.id == SETUP_ROLE for r in interaction.user.roles):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    embed = discord.Embed(
        title="🛡️ Brxxks Blox Marketplace | FAQ",
        color=0x2b2d31
    )
    embed.add_field(name="What is Brxxks Blox?",
        value="Brxxks Blox is a platform that provides a secure player-to-player marketplace for buyers and sellers of online gaming products. We provide a system for secure transactions — you do the rest. We have marketplaces for **250+ games** and leading titles!",
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
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ Done.", ephemeral=True)


@bot.tree.command(name="tos", description="Brxxks Blox Trading Terms of Service", guild=GUILD)
async def cmd_tos(interaction: discord.Interaction):
    if not any(r.id == SETUP_ROLE for r in interaction.user.roles):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
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
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ Done.", ephemeral=True)




# ─── Application Ticket System ────────────────────────────────────────────────

APP_TICKET_ADMIN = [ROLE["administrator"], ROLE["coordinator"], ROLE["co_founder"],
                    ROLE["chief_exec"], ROLE["director"], ROLE["president"]]

def app_overwrites(guild: discord.Guild, opener: discord.Member) -> dict:
    ow = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        opener: discord.PermissionOverwrite(read_messages=True, send_messages=True),
    }
    for rid in APP_TICKET_ADMIN:
        r = guild.get_role(rid)
        if r:
            ow[r] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
    return ow


class AppTicketView(discord.ui.View):
    def __init__(self, creator: str = "Unknown"):
        super().__init__(timeout=None)
        self.creator = creator

    @discord.ui.button(label="🔒 Close Ticket", style=discord.ButtonStyle.danger,
                       custom_id="v:app_close")
    async def close(self, interaction: discord.Interaction, btn: discord.ui.Button):
        if not has_role(interaction.user, APP_TICKET_ADMIN):
            await interaction.response.send_message("No permission.", ephemeral=True)
            return
        ch  = interaction.channel
        buf = await make_transcript(ch)
        tr_ch = interaction.guild.get_channel(CH["transcript_ch"])
        if tr_ch:
            embed = discord.Embed(color=0x2b2d31, title=f"Transcript for Application #{ch.name}")
            embed.add_field(name="Applicant",  value=self.creator,               inline=False)
            embed.add_field(name="Closed By",  value=interaction.user.mention,   inline=False)
            embed.add_field(name="Closed At",  value=ts_now(),                   inline=False)
            embed.set_footer(text=FOOTER)
            await tr_ch.send(embed=embed,
                             file=discord.File(buf, filename=f"transcript-{ch.name}.txt"))
        await interaction.response.send_message("Closing ticket in 5 seconds…")
        await asyncio.sleep(5)
        await ch.delete()


class AppRequestView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🖊️ Apply / Ask for Help", style=discord.ButtonStyle.primary,
                       custom_id="v:app_request")
    async def request(self, interaction: discord.Interaction, btn: discord.ui.Button):
        guild = interaction.guild
        cat   = guild.get_channel(CH["app_cat"])
        if cat is None:
            await interaction.response.send_message("Application category not found.", ephemeral=True)
            return
        for c in cat.channels:
            if c.topic == str(interaction.user.id):
                await interaction.response.send_message(
                    f"You already have an open application ticket: {c.mention}", ephemeral=True)
                return
        ch = await guild.create_text_channel(
            name=f"app-{interaction.user.name}",
            category=cat,
            overwrites=app_overwrites(guild, interaction.user),
            topic=str(interaction.user.id),
        )
        embed = discord.Embed(color=0x2b2d31, title="📝 Application / Help Ticket")
        embed.description = (
            f"Welcome {interaction.user.mention}!\n\n"
            "Please describe what you need:\n"
            "• Are you applying for **Moderator**?\n"
            "• Are you applying for **Middleman**?\n"
            "• Do you need **support/help**?\n\n"
            "A staff member will be with you shortly."
        )
        embed.set_footer(text=f"{FOOTER} • Today at {discord.utils.utcnow().strftime('%I:%M %p')}")
        view = AppTicketView(creator=interaction.user.mention)
        await ch.send(content=interaction.user.mention, embed=embed, view=view)
        await interaction.response.send_message(f"Ticket created: {ch.mention}", ephemeral=True)


@bot.tree.command(name="setupapplications", description="Post the Applications panel", guild=GUILD)
async def setup_applications(interaction: discord.Interaction):
    if not any(r.id == SETUP_ROLE for r in interaction.user.roles):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    embed = discord.Embed(color=0x2b2d31, title="📝 Applications")
    embed.description = "Welcome to our application information portal!"
    embed.add_field(
        name="🛡️ Apply for Moderator",
        value="Interested in becoming a moderator? We're looking for dedicated members. Moderators manage discussions, enforce rules, and assist members.",
        inline=False
    )
    embed.add_field(
        name="🤝 Apply for Middleman",
        value="Want to become a trusted middleman? This role requires exceptional judgment, reliability, and community reputation.",
        inline=False
    )
    embed.add_field(
        name="❓ Need Help?",
        value="If you need assistance with anything, don't hesitate to reach out. Our support team is here to help!",
        inline=False
    )
    embed.set_footer(text=f"{FOOTER} • Today at {discord.utils.utcnow().strftime('%I:%M %p')}")
    await interaction.channel.send(embed=embed, view=AppRequestView())
    await interaction.response.send_message("✅ Applications panel deployed.", ephemeral=True)


# ─── /temp and /fill ─────────────────────────────────────────────────────────

# Stores removed roles per user: {user_id: [role_id, ...]}
temp_removed: dict = {}

@bot.tree.command(name="temp", description="Toggle your hierarchy roles on/off", guild=GUILD)
async def cmd_temp(interaction: discord.Interaction):
    member = interaction.user
    user_roles = [r.id for r in member.roles]

    if member.id in temp_removed:
        # Give back the stored roles
        stored = temp_removed.pop(member.id)
        added = []
        for rid in stored:
            role = interaction.guild.get_role(rid)
            if role:
                await member.add_roles(role, reason="/temp restore")
                added.append(role.mention)
        await interaction.response.send_message(
            f"✅ Restored your roles: {', '.join(added)}" if added else "✅ No roles to restore.",
            ephemeral=True
        )
    else:
        # Remove all hierarchy roles they currently have
        to_remove = [rid for rid in HIERARCHY_IDS if rid in user_roles]
        if not to_remove:
            await interaction.response.send_message("You don't have any hierarchy roles to remove.", ephemeral=True)
            return
        temp_removed[member.id] = to_remove
        removed = []
        for rid in to_remove:
            role = interaction.guild.get_role(rid)
            if role:
                await member.remove_roles(role, reason="/temp hide")
                removed.append(role.mention)
        await interaction.response.send_message(
            f"✅ Temporarily removed: {', '.join(removed)}\nRun **/temp** again to get them back.",
            ephemeral=True
        )


@bot.tree.command(name="fill", description="Fill all hierarchy roles from your highest down to Middleman", guild=GUILD)
@app_commands.describe(user="User to fill roles for")
async def cmd_fill(interaction: discord.Interaction, user: discord.Member):
    if not has_role(interaction.user, [ROLE["director"], ROLE["president"]]):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    await interaction.response.defer()
    user_role_ids = [r.id for r in user.roles]

    # Find highest hierarchy role they have
    highest_idx = -1
    for i, rid in enumerate(HIERARCHY_IDS):
        if rid in user_role_ids:
            highest_idx = i

    if highest_idx == -1:
        await interaction.followup.send(content="That user doesn't have any hierarchy roles.")
        return

    # Give every role from 0 up to highest_idx that they're missing
    added = []
    for rid in HIERARCHY_IDS[:highest_idx + 1]:
        if rid not in user_role_ids:
            role = interaction.guild.get_role(rid)
            if role:
                await user.add_roles(role, reason="/fill")
                added.append(role.mention)

    if added:
        await interaction.followup.send(
            content=f"✅ Filled in missing roles for {user.mention}: {', '.join(added)}")
    else:
        await interaction.followup.send(
            content=f"✅ {user.mention} already has all roles up to their highest!")



# ─── Mercy System ────────────────────────────────────────────────────────────

HITTER_ROLE_ID = 1506426276804689970




class MercyView(discord.ui.View):
    def __init__(self, target=None, author=None):
        super().__init__(timeout=None)
        self.target = target
        self.author = author

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, custom_id="v:mercy_accept")
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

        # Ghost ping in designated channel after accepting
        ghost_ch = interaction.guild.get_channel(1507543943552962560)
        if ghost_ch:
            ghost_msg = await ghost_ch.send(interaction.user.mention)
            await ghost_msg.delete()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, custom_id="v:mercy_decline")
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
    ROLE["middleman"],
]


@bot.tree.command(
    name="mercy",
    description="Send a mercy notification to a user",
    guild=GUILD
)
@app_commands.default_permissions(send_messages=True)
@app_commands.describe(user="User to target")
async def mercy(interaction: discord.Interaction, user: discord.Member):

    if not has_role(interaction.user, MERCY_USE_ROLE):
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











# ─── /role_all ─────────────────────────────────────────────────────────────────

OWNER_ID = 1506430627501703249

@bot.tree.command(
    name="role_all",
    description="[OWNER ONLY] Give a role to every member who has a certain role.",
    guild=GUILD
)
@app_commands.describe(
    source_role="Members with this role will be targeted",
    give_role="This role will be given to all targeted members"
)
async def role_all(interaction: discord.Interaction, source_role: discord.Role, give_role: discord.Role):
    if not any(r.id == OWNER_ID for r in interaction.user.roles):
        await interaction.response.send_message("❌ Only the owner can use this command.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    guild   = interaction.guild
    success = 0
    failed  = 0
    skipped = 0

    targets = [m for m in guild.members if source_role in m.roles]

    for member in targets:
        if give_role in member.roles:
            skipped += 1
            continue
        try:
            await member.add_roles(give_role, reason=f"/role_all by {interaction.user} ({interaction.user.id})")
            success += 1
        except (discord.Forbidden, discord.HTTPException):
            failed += 1

    embed = discord.Embed(
        title="✅ Role Assigned",
        color=0x57f287,
    )
    embed.add_field(name="Source Role",  value=source_role.mention, inline=True)
    embed.add_field(name="Given Role",   value=give_role.mention,   inline=True)
    embed.add_field(name="Targeted",     value=str(len(targets)),   inline=True)
    embed.add_field(name="Success",      value=str(success),        inline=True)
    embed.add_field(name="Skipped",      value=str(skipped),        inline=True)
    embed.add_field(name="Failed",       value=str(failed),         inline=True)
    embed.add_field(name="Executed by",  value=interaction.user.mention, inline=False)
    embed.set_footer(text=FOOTER)

    await interaction.followup.send(embed=embed, ephemeral=True)

    log_ch = guild.get_channel(CH["role_log"])
    if log_ch:
        log_embed = discord.Embed(title="📋 /role_all Executed", color=0x5865f2)
        log_embed.add_field(name="Source Role",  value=source_role.mention,        inline=True)
        log_embed.add_field(name="Given Role",   value=give_role.mention,          inline=True)
        log_embed.add_field(name="Targeted",     value=str(len(targets)),          inline=True)
        log_embed.add_field(name="Success",      value=str(success),               inline=True)
        log_embed.add_field(name="Skipped",      value=str(skipped),               inline=True)
        log_embed.add_field(name="Failed",       value=str(failed),                inline=True)
        log_embed.add_field(name="Executed by",  value=interaction.user.mention,   inline=False)
        log_embed.add_field(name="Time",         value=ts_now(),                   inline=False)
        log_embed.set_footer(text=FOOTER)
        await log_ch.send(embed=log_embed)


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
    bot.add_view(MercyView())
    bot.add_view(AppRequestView())
    bot.add_view(AppTicketView())
    bot.tree.copy_global_to(guild=GUILD)
    synced = await bot.tree.sync(guild=GUILD)
    print(f"✅ Synced {len(synced)} commands to guild {GUILD_ID}")


bot.run(TOKEN)
