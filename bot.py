import discord
from discord.ext import commands, tasks
from discord.ui import Select, View, Button, Modal, TextInput
import asyncio
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
import asyncpg
from typing import List, Optional
import random
import string
import requests
import io

# Load environment variables
load_dotenv()

pool = None

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Configuration
DATABASE_URL = os.getenv('DATABASE_URL')
STAFF_ROLES = ["Ticket response team", "CREW", "LEAD CREW", "DEVELOPER", "MANAGEMENT", "COMMUNITY MANAGER", "OVERWATCHER"]
TICKET_CATEGORIES = {
    "reportPlayer": {"name": "Report a Player", "emoji": "⚠️"},
    "reportBug": {"name": "Report Bug", "emoji": "🐛"},
    "buyBusiness": {"name": "Buy a Business", "emoji": "💼"},
    "buyEDM": {"name": "Buy EDMs", "emoji": "🏎️"},
    "bookAuction": {"name": "Book an Auction Ticket", "emoji": "🎫"},
    "other": {"name": "Other Issues", "emoji": "📝"}
}
INACTIVE_CLOSE_DAYS = 3  # Close tickets after 3 days of inactivity
ARCHIVE_DELETE_DAYS = 10  # Delete archived tickets after 10 days


def generate_ticket_id():
    """Generate a random ticket ID"""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

class TicketTypeSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label=TICKET_CATEGORIES[category]["name"],
                value=category,
                emoji=TICKET_CATEGORIES[category]["emoji"]
            ) for category in TICKET_CATEGORIES
        ]
        super().__init__(
            placeholder="Select the type of ticket you want to create...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="ticket_type_select"
        )
    
    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await create_ticket(interaction, self.values[0])

class TicketView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketTypeSelect())

class TicketControlView(View):
    def __init__(self, is_staff: bool = False):
        super().__init__(timeout=None)
        self.add_item(Button(label="Close Ticket", style=discord.ButtonStyle.red, custom_id="close_ticket"))
        if is_staff:
            self.add_item(Button(label="Claim Ticket", style=discord.ButtonStyle.blurple, custom_id="claim_ticket"))
            self.add_item(Button(label="Add User", style=discord.ButtonStyle.green, custom_id="add_user"))
            self.add_item(Button(label="Remove User", style=discord.ButtonStyle.gray, custom_id="remove_user"))

class CloseReasonModal(Modal):
    def __init__(self):
        super().__init__(title="Ticket Closure Reason")
        self.reason = TextInput(
            label="Reason for closing this ticket",
            placeholder="Enter the reason for closing...",
            style=discord.TextStyle.long,
            required=True
        )
        self.add_item(self.reason)
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.stop()

class AddUserModal(Modal):
    def __init__(self):
        super().__init__(title="Add User to Ticket")
        self.user_id = TextInput(
            label="User ID or Mention",
            placeholder="Enter user ID or @mention",
            required=True
        )
        self.add_item(self.user_id)
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_input = self.user_id.value
        try:
            # Try to parse user ID
            user_id = int(user_input.strip("<@!>"))
            user = interaction.guild.get_member(user_id)
            if not user:
                raise ValueError("User not found")
        except ValueError:
            await interaction.followup.send("Invalid user ID or mention.", ephemeral=True)
            return
        
        await add_user_to_ticket(interaction, user)

async def create_db_pool():
    global pool
    try:
        pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
        print("Database pool created successfully")
    except Exception as e:
        print(f"Error creating database pool: {e}")
        raise

async def init_db():
    """Initialize PostgreSQL database"""
    if pool is None:
        raise RuntimeError("Database pool not initialized")
    
    try:
        async with pool.acquire() as conn:
            await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                display_name TEXT NOT NULL,
                last_seen TIMESTAMP DEFAULT NOW()
            );
            
            CREATE TABLE IF NOT EXISTS tickets (
                channel_id BIGINT PRIMARY KEY,
                ticket_id TEXT NOT NULL UNIQUE,
                user_id BIGINT NOT NULL REFERENCES users(user_id),
                ticket_type TEXT NOT NULL,
                claimed_by BIGINT REFERENCES users(user_id),
                closed BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW(),
                last_activity TIMESTAMP DEFAULT NOW(),
                additional_users BIGINT[] DEFAULT '{}'
            );
            
            CREATE TABLE IF NOT EXISTS transcripts (
                channel_id BIGINT PRIMARY KEY REFERENCES tickets(channel_id),
                paste_url TEXT,
                closed_at TIMESTAMP DEFAULT NOW(),
                closed_by BIGINT
            );
            
            CREATE TABLE IF NOT EXISTS ticket_stats (
                date DATE PRIMARY KEY,
                opened INTEGER DEFAULT 0,
                closed INTEGER DEFAULT 0,
                claimed INTEGER DEFAULT 0
            );
            
            CREATE TABLE IF NOT EXISTS ticket_setups (
                channel_id BIGINT PRIMARY KEY,
                message_id BIGINT NOT NULL
            );
            
            CREATE TABLE IF NOT EXISTS archived_tickets (
                channel_id BIGINT PRIMARY KEY,
                ticket_id TEXT NOT NULL,
                delete_at TIMESTAMP NOT NULL
            );
            """)
        print("Database initialized successfully")
    except Exception as e:
        print(f"Error initializing database: {e}")
        raise

async def startup():
    """Initialize database connection and tables"""
    await create_db_pool()
    await init_db()

async def upload_to_pastebin(content: str) -> Optional[str]:
    """Upload transcript to Pastebin and return URL"""
    try:
        data = {
            'api_dev_key': os.getenv('PASTEBIN_API_KEY'),
            'api_option': 'paste',
            'api_paste_code': content,
            'api_paste_name': f'Ticket Transcript {datetime.now()}',
            'api_paste_private': 1,  # Unlisted
            'api_paste_expire_date': '1M'  # 1 month expiration
        }
        
        response = requests.post(
            "https://pastebin.com/api/api_post.php",
            data=data,
            timeout=10
        )
        
        if response.status_code == 200 and response.text.startswith('http'):
            return response.text
        return None
    except Exception as e:
        print(f"Error uploading to Pastebin: {e}")
        return None

async def create_transcript(channel: discord.TextChannel, closer: discord.Member) -> Optional[str]:
    """Create and upload a transcript of the ticket"""
    transcript_content = []
    
    # Add header information
    async with pool.acquire() as conn:
        ticket_info = await conn.fetchrow("""
        SELECT ticket_id, user_id, ticket_type, created_at 
        FROM tickets WHERE channel_id = $1
        """, channel.id)
    
    transcript_content.append(f"=== TICKET TRANSCRIPT ===")
    transcript_content.append(f"Ticket ID: {ticket_info['ticket_id']}")
    transcript_content.append(f"Created by: {channel.guild.get_member(ticket_info['user_id']) or ticket_info['user_id']}")
    transcript_content.append(f"Type: {ticket_info['ticket_type']}")
    transcript_content.append(f"Created at: {ticket_info['created_at']}")
    transcript_content.append(f"Closed at: {datetime.now()}")
    transcript_content.append(f"Closed by: {closer}")
    transcript_content.append("\n=== MESSAGES ===\n")
    
    # Fetch and format all messages
    async for message in channel.history(limit=None, oldest_first=True):
        timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
        author = message.author.display_name
        content = message.clean_content.replace('\n', ' ')
        
        # Handle attachments
        attachments = ""
        if message.attachments:
            attachments = " [Attachments: " + ", ".join(a.filename for a in message.attachments) + "]"
        
        transcript_content.append(f"[{timestamp}] {author}: {content}{attachments}")
    
    # Upload to Pastebin
    paste_url = await upload_to_pastebin("\n".join(transcript_content))
    
    # Store reference in database
    if paste_url:
        async with pool.acquire() as conn:
            await conn.execute("""
            INSERT INTO transcripts (channel_id, paste_url, closed_by)
            VALUES ($1, $2, $3)
            ON CONFLICT (channel_id) DO UPDATE SET
                paste_url = EXCLUDED.paste_url,
                closed_at = NOW(),
                closed_by = EXCLUDED.closed_by
            """, channel.id, paste_url, closer.id)
    
    return paste_url

async def track_user(member: discord.Member):
    """Update user information in database"""
    async with pool.acquire() as conn:
        await conn.execute("""
        INSERT INTO users (user_id, display_name, last_seen)
        VALUES ($1, $2, NOW())
        ON CONFLICT (user_id) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            last_seen = EXCLUDED.last_seen
        """, member.id, member.display_name)

async def log_ticket_stat(action: str):
    """Log ticket statistics"""
    today = datetime.now().date()
    async with pool.acquire() as conn:
        await conn.execute("""
        INSERT INTO ticket_stats (date, opened, closed, claimed)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (date) DO UPDATE SET
            opened = ticket_stats.opened + EXCLUDED.opened,
            closed = ticket_stats.closed + EXCLUDED.closed,
            claimed = ticket_stats.claimed + EXCLUDED.claimed
        """, today, 
           1 if action == "opened" else 0,
           1 if action == "closed" else 0,
           1 if action == "claimed" else 0)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    print('------')
    try:
        await startup()
        print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    except Exception as e:
        print(f"Failed to initialize: {e}")
        
    await bot.tree.sync()
    auto_close_tickets.start()
    delete_archived_tickets.start()
    await restore_ticket_views()

def is_staff(member: discord.Member):
    return any(role.name in STAFF_ROLES for role in member.roles)

@bot.hybrid_command()
@commands.has_permissions(manage_guild=True)
async def stats(ctx, timeframe: str = "day"):
    """View ticket statistics (day/week/month/all)"""
    timeframes = {
        "day": 1,
        "week": 7,
        "month": 30,
        "all": 0
    }
    
    if timeframe not in timeframes:
        await ctx.send("Invalid timeframe. Use day/week/month/all", ephemeral=True)
        return
    
    days = timeframes[timeframe]
    date_filter = "AND date >= CURRENT_DATE - INTERVAL '%s days'" % days if days > 0 else ""
    
    async with pool.acquire() as conn:
        # Get overall stats
        stats = await conn.fetchrow(f"""
        SELECT 
            SUM(opened) as total_opened,
            SUM(closed) as total_closed,
            SUM(claimed) as total_claimed,
            AVG(closed::float/NULLIF(opened, 0)) as close_rate
        FROM ticket_stats
        WHERE 1=1 {date_filter}
        """)
        
        # Get recent activity
        recent = await conn.fetch(f"""
        SELECT date, opened, closed, claimed 
        FROM ticket_stats 
        WHERE 1=1 {date_filter}
        ORDER BY date DESC
        LIMIT 30
        """)
        
        # Get staff claims
        staff_claims = await conn.fetch("""
        SELECT 
            t.claimed_by,
            COUNT(*) as claims,
            u.display_name
        FROM tickets t
        JOIN users u ON t.claimed_by = u.user_id
        WHERE t.claimed_by IS NOT NULL
        GROUP BY t.claimed_by, u.display_name
        ORDER BY claims DESC
        LIMIT 5
        """)
    
    # Create embed
    embed = discord.Embed(
        title=f"Ticket Statistics ({timeframe})",
        color=discord.Color.blue()
    )
    
    # Add overall stats
    embed.add_field(
        name="📊 Overview",
        value=f"**Opened:** {stats['total_opened'] or 0}\n"
              f"**Closed:** {stats['total_closed'] or 0}\n"
              f"**Claimed:** {stats['total_claimed'] or 0}\n"
              f"**Close Rate:** {stats['close_rate']*100:.1f}%",
        inline=True
    )
    
    # Add recent activity if available
    if recent:
        recent_days = min(5, len(recent))
        recent_text = "\n".join(
            f"{row['date'].strftime('%b %d')}: +{row['opened']} / -{row['closed']}"
            for row in recent[:recent_days]
        )
        embed.add_field(
            name="📅 Recent Activity",
            value=recent_text,
            inline=True
        )
    
    # Add top staff if available
    if staff_claims:
        staff_text = "\n".join(
            f"{row['display_name']}: {row['claims']}"
            for row in staff_claims
        )
        embed.add_field(
            name="🏆 Top Staff",
            value=staff_text,
            inline=True
        )
    
    await ctx.send(embed=embed)

@bot.hybrid_command()
@commands.has_permissions(manage_guild=True)
async def userstats(ctx, user: discord.Member):
    """View ticket statistics for a specific user"""
    async with pool.acquire() as conn:
        # As ticket creator
        created = await conn.fetchrow("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN closed = TRUE THEN 1 ELSE 0 END) as closed
        FROM tickets
        WHERE user_id = $1
        """, user.id)
        
        # As staff member
        staff = await conn.fetchrow("""
        SELECT 
            COUNT(*) as claimed,
            AVG(EXTRACT(EPOCH FROM (last_activity - created_at))/3600) as avg_hours
        FROM tickets
        WHERE claimed_by = $1 AND closed = TRUE
        """, user.id)
    
    embed = discord.Embed(
        title=f"Ticket Stats for {user.display_name}",
        color=discord.Color.blue()
    )
    
    # Created tickets
    embed.add_field(
        name="🎫 Created Tickets",
        value=f"**Total:** {created['total'] or 0}\n"
              f"**Closed:** {created['closed'] or 0}\n"
              f"**Open:** {created['total'] - created['closed']}",
        inline=True
    )
    
    # If user is staff, show their staff stats
    if is_staff(user):
        embed.add_field(
            name="🛠️ Staff Activity",
            value=f"**Claimed:** {staff['claimed'] or 0}\n"
                  f"**Avg Time:** {staff['avg_hours']:.1f} hours" if staff['avg_hours'] else "No data",
            inline=True
        )
    
    await ctx.send(embed=embed)

@bot.hybrid_command()
@commands.has_permissions(manage_guild=True)
async def setup(ctx):
    """Setup the ticket system in the current channel"""
    # Check if this is a slash command interaction
    if ctx.interaction:
        await ctx.interaction.response.defer(ephemeral=True)
    
    # Delete the command message if it exists (for prefix commands)
    try:
        if not ctx.interaction and ctx.message:
            await ctx.message.delete()
    except discord.NotFound:
        pass  # Message already deleted
    
    # Check if there's already a setup message in this channel
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT message_id FROM ticket_setups WHERE channel_id = $1",
            ctx.channel.id
        )
        
        if existing:
            try:
                # Try to delete the existing message
                old_msg = await ctx.channel.fetch_message(existing['message_id'])
                await old_msg.delete()
            except discord.NotFound:
                pass  # Message doesn't exist anymore
    
    # Create new embed
    embed = discord.Embed(
        title="Lunar City Official Support",
        description="To create a ticket, click the dropdown menu below and select the appropriate category.",
        color=discord.Color.blue()
    )
    
    # Send the new message
    message = await ctx.send(embed=embed, view=TicketView())
    
    # Store the setup message in database
    async with pool.acquire() as conn:
        await conn.execute("""
        INSERT INTO ticket_setups (channel_id, message_id)
        VALUES ($1, $2)
        ON CONFLICT (channel_id) DO UPDATE SET
            message_id = EXCLUDED.message_id
        """, ctx.channel.id, message.id)

async def restore_ticket_views():
    """Restore ticket setup views after bot restart"""
    async with pool.acquire() as conn:
        setups = await conn.fetch("SELECT channel_id, message_id FROM ticket_setups")
        
    for setup in setups:
        channel = bot.get_channel(setup['channel_id'])
        if channel:
            try:
                message = await channel.fetch_message(setup['message_id'])
                await message.edit(view=TicketView())
            except discord.NotFound:
                # Message was deleted, remove from database
                async with pool.acquire() as conn:
                    await conn.execute("DELETE FROM ticket_setups WHERE channel_id = $1", setup['channel_id'])
            except Exception as e:
                print(f"Error restoring ticket view in channel {setup['channel_id']}: {e}")

async def create_ticket(interaction: discord.Interaction, ticket_type: str):
    await track_user(interaction.user)
    guild = interaction.guild
    user = interaction.user
    
    try:
        # Check for existing open tickets
        async with pool.acquire() as conn:
            existing = await conn.fetchrow("""
            SELECT channel_id FROM tickets 
            WHERE user_id = $1 AND closed = FALSE
            """, user.id)
            
            if existing:
                channel = guild.get_channel(existing['channel_id'])
                if channel:
                    await interaction.followup.send(
                        f"You already have an open ticket: {channel.mention}", 
                        ephemeral=True
                    )
                    return
        
        # Generate ticket ID
        ticket_id = generate_ticket_id()
        
        # Get or create category for this ticket type
        category_name = TICKET_CATEGORIES[ticket_type]["name"]
        ticket_category = discord.utils.get(guild.categories, name=category_name)
        
        if not ticket_category:
            # Create new category with customized permissions
            ticket_category = await guild.create_category(category_name)
            
            # Set category permissions
            await ticket_category.set_permissions(guild.default_role, read_messages=False)
            
            # Add staff permissions
            for role in guild.roles:
                if role.name in STAFF_ROLES:
                    await ticket_category.set_permissions(role, read_messages=True, send_messages=True)
        
        # Create ticket channel with ticket ID
        ticket_channel = await guild.create_text_channel(
            name=f"ticket-{ticket_id}",
            category=ticket_category,
            topic=f"Ticket ID: {ticket_id} | Type: {TICKET_CATEGORIES[ticket_type]['name']}"
        )
        
        # Set channel permissions
        await ticket_channel.set_permissions(guild.default_role, read_messages=False)
        await ticket_channel.set_permissions(user, read_messages=True, send_messages=True)
        
        # Store ticket info
        async with pool.acquire() as conn:
            await conn.execute("""
            INSERT INTO tickets (channel_id, ticket_id, user_id, ticket_type)
            VALUES ($1, $2, $3, $4)
            """, ticket_channel.id, ticket_id, user.id, ticket_type)
        
        await log_ticket_stat("opened")
        
        # Send welcome message
        embed = discord.Embed(
            title=f"{TICKET_CATEGORIES[ticket_type]['emoji']} {TICKET_CATEGORIES[ticket_type]['name']} Ticket",
            description=f"Thank you for creating a ticket, {user.mention}!\n\n"
                       f"**Ticket ID:** {ticket_id}\n"
                       f"Support staff will be with you shortly.\n\n"
                       f"Please describe your issue in detail here.",
            color=discord.Color.green()
        )
        
        is_user_staff = is_staff(user)
        control_msg = await ticket_channel.send(
            content=f"{user.mention} | Support Team",
            embed=embed,
            view=TicketControlView(is_staff=is_user_staff)
        )
        
        # Pin the control message
        await control_msg.pin()
        
        await interaction.followup.send(
            f"Your {TICKET_CATEGORIES[ticket_type]['name']} ticket has been created: {ticket_channel.mention}\n"
            f"**Ticket ID:** {ticket_id}", 
            ephemeral=True
        )
    
    except Exception as e:
        print(f"Error creating ticket: {e}")
        if not interaction.response.is_done():
            await interaction.followup.send(
                "An error occurred while creating your ticket. Please try again.",
                ephemeral=True
            )

@tasks.loop(hours=24)
async def auto_close_tickets():
    """Automatically close inactive tickets"""
    cutoff = datetime.now() - timedelta(days=INACTIVE_CLOSE_DAYS)
    
    async with pool.acquire() as conn:
        inactive_tickets = await conn.fetch("""
        SELECT channel_id, user_id, ticket_id 
        FROM tickets 
        WHERE closed = FALSE AND last_activity < $1
        """, cutoff)
    
    for ticket in inactive_tickets:
        guild = bot.get_guild(bot.guilds[0].id)  # Get first guild - adjust as needed
        channel = guild.get_channel(ticket['channel_id'])
        user = guild.get_member(ticket['user_id'])
        
        if channel:
            # Create transcript
            paste_url = await create_transcript(channel, bot.user)
            
            # Send closure message
            reason = f"Automatically closed after {INACTIVE_CLOSE_DAYS} days of inactivity"
            embed = discord.Embed(
                title="Ticket Closed Due to Inactivity",
                description=f"This ticket has been {reason}\n\n"
                           f"**Ticket ID:** {ticket['ticket_id']}\n"
                           f"**Transcript:** {paste_url or 'Not available'}",
                color=discord.Color.orange()
            )
            await channel.send(embed=embed)
            
            # Send DM to ticket creator
            try:
                creator = guild.get_member(ticket['user_id'])
                if creator:
                    dm_embed = discord.Embed(
                        title="Your Ticket Has Been Closed",
                        description=f"Your ticket in {guild.name} has been {reason}\n\n"
                                   f"**Ticket ID:** {ticket['ticket_id']}\n"
                                   f"**Transcript:** {paste_url or 'Not available'}",
                        color=discord.Color.orange()
                    )
                    await creator.send(embed=dm_embed)
            except discord.Forbidden:
                print(f"Could not send DM to user {ticket['user_id']}")
            
            # Archive channel
            archive_category = discord.utils.get(guild.categories, name="Archived Tickets")
            if not archive_category:
                archive_category = await guild.create_category("Archived Tickets")
            
            await channel.edit(category=archive_category)
            await channel.set_permissions(
                guild.default_role,
                read_messages=False
            )
            
            # Update database
            async with pool.acquire() as conn:
                await conn.execute("""
                UPDATE tickets 
                SET closed = TRUE 
                WHERE channel_id = $1
                """, channel.id)
                
                # Schedule for deletion
                delete_at = datetime.now() + timedelta(days=ARCHIVE_DELETE_DAYS)
                await conn.execute("""
                INSERT INTO archived_tickets (channel_id, ticket_id, delete_at)
                VALUES ($1, $2, $3)
                """, channel.id, ticket['ticket_id'], delete_at)
            
            await log_ticket_stat("closed")

@tasks.loop(hours=6)
async def delete_archived_tickets():
    """Automatically delete archived tickets after configured days"""
    async with pool.acquire() as conn:
        tickets_to_delete = await conn.fetch("""
        SELECT channel_id, ticket_id FROM archived_tickets 
        WHERE delete_at <= NOW()
        """)
        
    for ticket in tickets_to_delete:
        channel = bot.get_channel(ticket['channel_id'])
        if channel:
            try:
                await channel.delete()
            except discord.NotFound:
                pass  # Channel already deleted
            except Exception as e:
                print(f"Error deleting archived ticket channel {ticket['channel_id']}: {e}")
        
        # Clean up database
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM archived_tickets WHERE channel_id = $1", ticket['channel_id'])
            await conn.execute("DELETE FROM tickets WHERE ticket_id = $1", ticket['ticket_id'])

async def restore_ticket_creation_view(guild: discord.Guild):
    """Restore the ticket creation view in setup channels"""
    async with pool.acquire() as conn:
        setups = await conn.fetch("SELECT channel_id, message_id FROM ticket_setups")
        
    for setup in setups:
        channel = guild.get_channel(setup['channel_id'])
        if channel:
            try:
                message = await channel.fetch_message(setup['message_id'])
                if not message.components:  # If view was lost
                    await message.edit(view=TicketView())
            except discord.NotFound:
                # Message was deleted, remove from database
                async with pool.acquire() as conn:
                    await conn.execute("DELETE FROM ticket_setups WHERE channel_id = $1", setup['channel_id'])
            except Exception as e:
                print(f"Error restoring ticket view in channel {setup['channel_id']}: {e}")

async def handle_close_ticket(interaction: discord.Interaction):
    channel = interaction.channel
    user = interaction.user
    
    try:
        # Get ticket info
        async with pool.acquire() as conn:
            ticket = await conn.fetchrow("""
            SELECT ticket_id, user_id, claimed_by 
            FROM tickets 
            WHERE channel_id = $1 AND closed = FALSE
            """, channel.id)
            
            if not ticket:
                await interaction.response.send_message("This is not an open ticket channel.", ephemeral=True)
                return
            
            if not (is_staff(user) or user.id == ticket['user_id']):
                await interaction.response.send_message("You don't have permission to close this ticket.", ephemeral=True)
                return

            # Ask for closure reason if staff is closing
            reason = "No reason provided"
            if is_staff(user):
                modal = CloseReasonModal()
                await interaction.response.send_modal(modal)
                await modal.wait()
                reason = modal.reason.value
            else:
                await interaction.response.defer()

            # Create transcript
            paste_url = await create_transcript(channel, user)
            
            # Create closure embed
            embed = discord.Embed(
                title="Ticket Closed",
                description=f"This ticket has been closed by {user.mention}\n\n"
                           f"**Ticket ID:** {ticket['ticket_id']}\n"
                           f"**Reason:** {reason}\n"
                           f"**Transcript:** {paste_url or 'Not available'}",
                color=discord.Color.red()
            )
            
            # Send to ticket channel
            await channel.send(embed=embed)
            
            # Send DM to ticket creator
            try:
                creator = interaction.guild.get_member(ticket['user_id'])
                if creator:
                    dm_embed = discord.Embed(
                        title="Your Ticket Has Been Closed",
                        description=f"Your ticket in {interaction.guild.name} has been closed\n\n"
                                   f"**Ticket ID:** {ticket['ticket_id']}\n"
                                   f"**Reason:** {reason}\n"
                                   f"**Transcript:** {paste_url or 'Not available'}",
                        color=discord.Color.red()
                    )
                    await creator.send(embed=dm_embed)
            except discord.Forbidden:
                print(f"Could not send DM to user {ticket['user_id']}")
            
            # Archive channel
            archive_category = discord.utils.get(interaction.guild.categories, name="Archived Tickets")
            if not archive_category:
                archive_category = await interaction.guild.create_category("Archived Tickets")
            
            await channel.edit(category=archive_category)
            await channel.set_permissions(
                interaction.guild.default_role,
                read_messages=False
            )
            
            # Update database
            await conn.execute("""
            UPDATE tickets 
            SET closed = TRUE 
            WHERE channel_id = $1
            """, channel.id)
            
            # Schedule for deletion
            delete_at = datetime.now() + timedelta(days=ARCHIVE_DELETE_DAYS)
            await conn.execute("""
            INSERT INTO archived_tickets (channel_id, ticket_id, delete_at)
            VALUES ($1, $2, $3)
            """, channel.id, ticket['ticket_id'], delete_at)
            
            await log_ticket_stat("closed")
            
            # Remove buttons from control message
            async for message in channel.history(limit=10):
                if message.components:
                    await message.edit(view=None)
                    break
            
            await restore_ticket_creation_view(interaction.guild)

    except Exception as e:
        print(f"Error closing ticket: {type(e).__name__}: {e}")
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "An error occurred while closing the ticket. Please try again.",
                ephemeral=True
            )

async def handle_claim_ticket(interaction: discord.Interaction):
    await track_user(interaction.user)
    """Handle ticket claiming via interaction"""
    if not is_staff(interaction.user):
        await interaction.response.send_message("You don't have permission to claim tickets.", ephemeral=True)
        return
    
    channel = interaction.channel
    
    async with pool.acquire() as conn:
        ticket = await conn.fetchrow("""
        SELECT channel_id, claimed_by FROM tickets 
        WHERE channel_id = $1 AND closed = FALSE
        """, channel.id)
        
        if not ticket:
            await interaction.response.send_message("This is not an open ticket channel.", ephemeral=True)
            return
        
        if ticket['claimed_by']:
            if ticket['claimed_by'] == interaction.user.id:
                await interaction.response.send_message("You've already claimed this ticket.", ephemeral=True)
            else:
                claimed_by = interaction.guild.get_member(ticket['claimed_by'])
                await interaction.response.send_message(f"This ticket is already claimed by {claimed_by.mention}.", ephemeral=True)
            return
        
        await conn.execute("""
        UPDATE tickets 
        SET claimed_by = $1 
        WHERE channel_id = $2
        """, interaction.user.id, channel.id)
    
    await log_ticket_stat("claimed")
    
    embed = discord.Embed(
        description=f"🎫 This ticket has been claimed by {interaction.user.mention}",
        color=discord.Color.blurple()
    )
    await interaction.response.send_message(embed=embed)
    
    # Update control message
    messages = [message async for message in channel.history(limit=10)]
    for message in messages:
        if message.components:
            is_staff_user = is_staff(interaction.user)
            await message.edit(view=TicketControlView(is_staff=is_staff_user))
            break

async def add_user_to_ticket(interaction: discord.Interaction, user: discord.Member):
    """Add a user to a ticket"""
    channel = interaction.channel
    
    async with pool.acquire() as conn:
        ticket = await conn.fetchrow("""
        SELECT user_id, additional_users FROM tickets 
        WHERE channel_id = $1 AND closed = FALSE
        """, channel.id)
        
        if not ticket:
            await interaction.followup.send("This is not an open ticket channel.", ephemeral=True)
            return
        
        if user.id == ticket['user_id'] or user.id in ticket['additional_users']:
            await interaction.followup.send("User already has access to this ticket.", ephemeral=True)
            return
        
        await conn.execute("""
        UPDATE tickets 
        SET additional_users = array_append(additional_users, $1)
        WHERE channel_id = $2
        """, user.id, channel.id)
        
        await channel.set_permissions(user, read_messages=True, send_messages=True)
        
        embed = discord.Embed(
            description=f"✅ {user.mention} has been added to the ticket by {interaction.user.mention}",
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed)

async def remove_user_from_ticket(interaction: discord.Interaction, user: discord.Member):
    """Remove a user from a ticket"""
    channel = interaction.channel
    
    async with pool.acquire() as conn:
        ticket = await conn.fetchrow("""
        SELECT user_id, additional_users FROM tickets 
        WHERE channel_id = $1 AND closed = FALSE
        """, channel.id)
        
        if not ticket:
            await interaction.followup.send("This is not an open ticket channel.", ephemeral=True)
            return
        
        if user.id == ticket['user_id']:
            await interaction.followup.send("Cannot remove the ticket creator.", ephemeral=True)
            return
        
        if user.id not in ticket['additional_users']:
            await interaction.followup.send("User doesn't have access to this ticket.", ephemeral=True)
            return
        
        await conn.execute("""
        UPDATE tickets 
        SET additional_users = array_remove(additional_users, $1)
        WHERE channel_id = $2
        """, user.id, channel.id)
        
        await channel.set_permissions(user, read_messages=False, send_messages=False)
        
        embed = discord.Embed(
            description=f"❌ {user.mention} has been removed from the ticket by {interaction.user.mention}",
            color=discord.Color.red()
        )
        await interaction.followup.send(embed=embed)

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    
    # Check if this is a ticket channel
    async with pool.acquire() as conn:
        is_ticket = await conn.fetchval("""
        SELECT EXISTS(SELECT 1 FROM tickets WHERE channel_id = $1)
        """, message.channel.id)
    
    if is_ticket:
        # Update user info
        await track_user(message.author)
        
        # Update last activity for the ticket
        async with pool.acquire() as conn:
            await conn.execute("""
            UPDATE tickets 
            SET last_activity = NOW() 
            WHERE channel_id = $1
            """, message.channel.id)
    
    await bot.process_commands(message)

@bot.event
async def on_interaction(interaction: discord.Interaction):
    try:
        if interaction.type == discord.InteractionType.component:
            custom_id = interaction.data["custom_id"]
            
            if custom_id == "close_ticket":
                await handle_close_ticket(interaction)
            elif custom_id == "claim_ticket":
                await handle_claim_ticket(interaction)
            elif custom_id == "add_user":
                await interaction.response.send_modal(AddUserModal())
            elif custom_id == "remove_user":
                await handle_remove_user_interaction(interaction)
    except Exception as e:
        print(f"Error in on_interaction: {e}")
        if not interaction.response.is_done():
            await interaction.response.send_message("An error occurred while processing your request.", ephemeral=True)

async def handle_remove_user_interaction(interaction: discord.Interaction):
    """Handle the remove user button interaction"""
    await interaction.response.send_message(
        "Please mention the user you want to remove or enter their ID.",
        ephemeral=True
    )
    
    def check(m):
        return m.author == interaction.user and m.channel == interaction.channel
    
    try:
        msg = await bot.wait_for('message', check=check, timeout=60)
        user_input = msg.content
        try:
            # Try to parse user ID
            user_id = int(user_input.strip("<@!>"))
            user = interaction.guild.get_member(user_id)
            if not user:
                raise ValueError("User not found")
        except ValueError:
            await interaction.followup.send("Invalid user ID or mention.", ephemeral=True)
            return
        
        await remove_user_from_ticket(interaction, user)
    except asyncio.TimeoutError:
        await interaction.followup.send("Timed out waiting for user input.", ephemeral=True)

@bot.command()
@commands.is_owner()
async def resetdb(ctx):
    """Owner-only command to reset database"""
    async with pool.acquire() as conn:
        await conn.execute("""
        DROP TABLE IF EXISTS archived_tickets CASCADE;
        DROP TABLE IF EXISTS transcripts CASCADE;
        DROP TABLE IF EXISTS tickets CASCADE;
        DROP TABLE IF EXISTS ticket_stats CASCADE;
        DROP TABLE IF EXISTS ticket_setups CASCADE;
        """)
    await init_db()
    await ctx.send("Database has been reset")

@bot.command()
@commands.is_owner()
async def migrate_categories(ctx):
    """Migrate old ticket categories to new ones"""
    category_mapping = {
        "general": "other",
        "technical": "reportBug",
        "billing": "other",
        "report": "reportPlayer",
        "other": "other"
    }
    
    async with pool.acquire() as conn:
        # Update ticket types in database
        for old_type, new_type in category_mapping.items():
            await conn.execute("""
            UPDATE tickets 
            SET ticket_type = $1 
            WHERE ticket_type = $2
            """, new_type, old_type)
        
        # Update category names in the server
        guild = ctx.guild
        for old_type, new_type in category_mapping.items():
            old_category = discord.utils.get(guild.categories, name=TICKET_CATEGORIES.get(old_type, {}).get("name", ""))
            if old_category:
                new_name = TICKET_CATEGORIES[new_type]["name"]
                await old_category.edit(name=new_name)
    
    await ctx.send("Ticket categories migrated successfully!")

# Get token from environment or prompt
token = os.getenv('DISCORD_BOT_TOKEN')
if not token:
    token = input("Please enter your bot token: ")

bot.run(token)