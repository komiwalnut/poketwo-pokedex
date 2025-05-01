import os
import discord
from discord import app_commands
from discord.ext import commands
import requests
import google.generativeai as genai
from io import BytesIO
import logging
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
import json
import asyncio

load_dotenv()

os.makedirs('logs', exist_ok=True)
os.makedirs('data', exist_ok=True)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(console_format)

file_handler = RotatingFileHandler(
    'logs/poketwo_bot.log',
    maxBytes=30 * 1024 * 1024,
    backupCount=5
)
file_handler.setLevel(logging.INFO)
file_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_format)

logger.addHandler(console_handler)
logger.addHandler(file_handler)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
POKETWO_ID = 716390085896962058
SUBSCRIPTION_FILE = 'data/subscriptions.json'
SAVE_INTERVAL = 300

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel('gemini-2.5-flash-preview-04-17')

subscribed_users = {}
pokemon_database = {}
last_save_time = 0


def load_subscriptions():
    global subscribed_users
    try:
        if os.path.exists(SUBSCRIPTION_FILE):
            with open(SUBSCRIPTION_FILE, 'r') as f:
                data = json.load(f)

            subscribed_users = {int(user_id): set(guild_ids) for user_id, guild_ids in data.items()}
            logger.info(f"Loaded subscriptions for {len(subscribed_users)} users")
        else:
            logger.info("No subscription file found, starting with empty subscriptions")
            subscribed_users = {}
    except Exception as err:
        logger.error(f"Error loading subscriptions: {err}")
        subscribed_users = {}


def save_subscriptions():
    global last_save_time
    try:
        data = {str(user_id): list(guild_ids) for user_id, guild_ids in subscribed_users.items()}

        temp_file = f"{SUBSCRIPTION_FILE}.tmp"
        with open(temp_file, 'w') as f:
            json.dump(data, f, indent=2)

        os.replace(temp_file, SUBSCRIPTION_FILE)

        last_save_time = asyncio.get_event_loop().time()
        logger.info(f"Saved subscriptions for {len(subscribed_users)} users")
    except Exception as err:
        logger.error(f"Error saving subscriptions: {err}")


async def periodic_save():
    while True:
        current_time = asyncio.get_event_loop().time()
        if current_time - last_save_time >= SAVE_INTERVAL:
            save_subscriptions()
        await asyncio.sleep(60)


async def load_pokemon_database():
    global pokemon_database
    try:
        response = requests.get("https://pokeapi.co/api/v2/pokemon?limit=1302")
        if response.status_code == 200:
            data = response.json()
            pokemon_database = {entry['name']: True for entry in data['results']}
            logger.info(f"Loaded {len(pokemon_database)} Pokémon names into database")
        else:
            logger.error(f"Failed to fetch Pokémon database: {response.status_code}")
    except Exception as err:
        logger.error(f"Error loading Pokémon database: {err}")


def verify_pokemon_name(name):
    if not name:
        return None

    name = name.lower().strip()

    if name in pokemon_database:
        return name

    for pokemon in pokemon_database:
        if name in pokemon or pokemon in name:
            return pokemon

    return name


@bot.event
async def on_ready():
    logger.info(f"{bot.user} is online and ready!")
    load_subscriptions()
    await load_pokemon_database()
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} command(s)")

        bot.loop.create_task(periodic_save())
    except Exception as err:
        logger.error(f"Failed to sync commands: {err}")


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if message.author.id != POKETWO_ID:
        await bot.process_commands(message)
        return

    logger.info(f"Received message from Pokétwo in server: {message.guild.name if message.guild else 'DM'}")
    if message.embeds:
        for i, embed in enumerate(message.embeds):
            logger.info(f"Embed {i + 1} - Title: '{embed.title}', Description: '{embed.description}'")
            if embed.image:
                logger.info(f"Embed {i + 1} has image: {embed.image.url}")
    if message.attachments:
        logger.info(f"Message has {len(message.attachments)} attachments")
        for i, attachment in enumerate(message.attachments):
            logger.info(f"Attachment {i + 1}: {attachment.url}")

    wild_pokemon_detected = False

    pokemon_patterns = [
        "A wild pokémon has appeared!",
        "wild pokémon has appeared!",
        "fled. A new wild pokémon has appeared!"
    ]

    if message.content:
        for pattern in pokemon_patterns:
            if pattern in message.content:
                wild_pokemon_detected = True
                break

    if message.embeds:
        for embed in message.embeds:
            if embed.title:
                for pattern in pokemon_patterns:
                    if pattern in embed.title:
                        wild_pokemon_detected = True
                        break

            if embed.description:
                for pattern in pokemon_patterns:
                    if pattern in embed.description:
                        wild_pokemon_detected = True
                        break

    if wild_pokemon_detected and message.guild:
        logger.info(f"Wild Pokémon detected in server: {message.guild.name}!")
        guild_id = message.guild.id
        guild_name = message.guild.name
        channel_id = message.channel.id
        message_id = message.id
        message_link = f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"

        if message.attachments and len(message.attachments) > 0:
            attachment = message.attachments[0]
            await process_pokemon_image(attachment.url, guild_id, guild_name, message_link)

        elif message.embeds:
            for embed in message.embeds:
                if embed.image and embed.image.url:
                    logger.info(f"Found image in embed: {embed.image.url}")
                    await process_pokemon_image(embed.image.url, guild_id, guild_name, message_link)
                    break

    await bot.process_commands(message)


async def process_pokemon_image(image_url, guild_id, guild_name, message_link):
    try:
        response = requests.get(image_url)
        image_bytes = BytesIO(response.content)

        pokemon_name = await identify_pokemon(image_bytes)

        if pokemon_name:
            pokemon_color = await get_pokemon_color(pokemon_name)

            for user_id, guilds in subscribed_users.items():
                if guild_id in guilds:
                    try:
                        user = await bot.fetch_user(user_id)

                        embed = discord.Embed(
                            title=f"Wild Pokémon Appeared! ✨",
                            description=f"I spotted a **{pokemon_name.capitalize()}** in **{guild_name}**!",
                            color=pokemon_color
                        )
                        embed.add_field(
                            name="Catch Command",
                            value=f"`<@716390085896962058> catch {pokemon_name}`",
                            inline=False
                        )
                        embed.add_field(
                            name="Server Location",
                            value=f"[Click here to go to the message]({message_link})",
                            inline=False
                        )
                        embed.set_thumbnail(url=image_url)
                        embed.set_footer(text=f"PokéDetector | Guild: {guild_name}")

                        await user.send(embed=embed)

                    except Exception as err:
                        logger.error(f"Failed to DM user {user_id}: {err}")

    except Exception as err:
        logger.error(f"Error processing Pokémon image: {err}")


async def get_pokemon_color(pokemon_name):
    try:
        response = requests.get(f"https://pokeapi.co/api/v2/pokemon/{pokemon_name}")
        if response.status_code == 200:
            data = response.json()
            if "types" in data and len(data["types"]) > 0:
                primary_type = data["types"][0]["type"]["name"]
                type_colors = {
                    "normal": 0xA8A77A,  # Light brown
                    "fire": 0xEE8130,  # Orange
                    "water": 0x6390F0,  # Blue
                    "electric": 0xF7D02C,  # Yellow
                    "grass": 0x7AC74C,  # Green
                    "ice": 0x96D9D6,  # Light blue
                    "fighting": 0xC22E28,  # Red
                    "poison": 0xA33EA1,  # Purple
                    "ground": 0xE2BF65,  # Tan
                    "flying": 0xA98FF3,  # Light purple
                    "psychic": 0xF95587,  # Pink
                    "bug": 0xA6B91A,  # Light green
                    "rock": 0xB6A136,  # Dark yellow
                    "ghost": 0x735797,  # Dark purple
                    "dragon": 0x6F35FC,  # Indigo
                    "dark": 0x705746,  # Dark brown
                    "steel": 0xB7B7CE,  # Gray
                    "fairy": 0xD685AD,  # Light pink
                }
                return type_colors.get(primary_type, 0xFF5252)

        return 0xFF5252
    except Exception as err:
        logger.error(f"Error getting Pokémon color: {err}")
        return 0xFF5252


async def identify_pokemon(image_bytes):
    try:
        image_bytes.seek(0)

        prompt1 = "This is a Pokémon from the Pokétwo Discord bot. Identify the exact Pokémon name. Be precise and give only the name."

        response1 = gemini_model.generate_content([
            prompt1,
            {"mime_type": "image/jpeg", "data": image_bytes.read()}
        ])

        image_bytes.seek(0)

        prompt2 = "What Pokémon is this? Give me just the name, no other text."

        response2 = gemini_model.generate_content([
            prompt2,
            {"mime_type": "image/jpeg", "data": image_bytes.read()}
        ])

        pokemon_name1 = response1.text.strip().lower()
        pokemon_name2 = response2.text.strip().lower()

        logger.info(f"Gemini identified Pokémon as: {pokemon_name1} and {pokemon_name2}")

        final_name = pokemon_name1
        if pokemon_name2 in pokemon_database and pokemon_name1 not in pokemon_database:
            final_name = pokemon_name2

        verified_name = verify_pokemon_name(final_name)

        if verified_name:
            logger.info(f"Verified Pokémon name: {verified_name}")
            return verified_name
        else:
            logger.warning(f"Could not verify Pokémon name: {final_name}")
            return final_name

    except Exception as err:
        logger.error(f"Error identifying Pokémon with Gemini: {err}")
        return None


@bot.tree.command(name="sub", description="Subscribe to receive Pokémon notifications in this server")
async def subscribe(interaction: discord.Interaction):
    user_id = interaction.user.id
    guild_id = interaction.guild_id

    if not guild_id:
        await interaction.response.send_message("This command can only be used in servers, not in DMs!", ephemeral=True)
        return

    if user_id not in subscribed_users:
        subscribed_users[user_id] = {guild_id}
        await interaction.response.send_message(f"You've been subscribed to Pokémon notifications in **{interaction.guild.name}**!", ephemeral=True)
    else:
        if guild_id in subscribed_users[user_id]:
            await interaction.response.send_message(f"You're already subscribed to Pokémon notifications in **{interaction.guild.name}**!", ephemeral=True)
        else:
            subscribed_users[user_id].add(guild_id)
            await interaction.response.send_message(f"You've been subscribed to Pokémon notifications in **{interaction.guild.name}**!", ephemeral=True)

    save_subscriptions()


@bot.tree.command(name="unsub", description="Unsubscribe from Pokémon notifications in this server")
async def unsubscribe(interaction: discord.Interaction):
    user_id = interaction.user.id
    guild_id = interaction.guild_id

    if not guild_id:
        await interaction.response.send_message("This command can only be used in servers, not in DMs!", ephemeral=True)
        return

    if user_id in subscribed_users and guild_id in subscribed_users[user_id]:
        subscribed_users[user_id].remove(guild_id)

        if not subscribed_users[user_id]:
            del subscribed_users[user_id]

        await interaction.response.send_message(f"You've been unsubscribed from Pokémon notifications in **{interaction.guild.name}**.", ephemeral=True)

        save_subscriptions()
    else:
        await interaction.response.send_message(f"You weren't subscribed to Pokémon notifications in **{interaction.guild.name}**.", ephemeral=True)


@bot.tree.command(name="sub_status", description="Check your subscription status across all servers")
async def subscription_status(interaction: discord.Interaction):
    user_id = interaction.user.id

    if user_id in subscribed_users and subscribed_users[user_id]:
        subscribed_servers = []

        for guild_id in subscribed_users[user_id]:
            guild = bot.get_guild(guild_id)
            server_name = guild.name if guild else f"Unknown Server ({guild_id})"
            subscribed_servers.append(f"• {server_name}")

        servers_list = "\n".join(subscribed_servers)
        await interaction.response.send_message(f"You are currently subscribed to Pokémon notifications in the following servers:\n\n{servers_list}", ephemeral=True)
    else:
        await interaction.response.send_message("You are not subscribed to Pokémon notifications in any server.", ephemeral=True)


@bot.tree.command(name="sub_all", description="Subscribe to servers the bot is in (limited to 10 servers)")
@app_commands.describe(confirm="Type 'confirm' to acknowledge you may receive multiple notifications")
async def subscribe_all(interaction: discord.Interaction, confirm: str = None):
    user_id = interaction.user.id

    if confirm != "confirm":
        await interaction.response.send_message(
            "⚠️ **Warning**: This will subscribe you to multiple servers at once.\n"
            "You may receive many notifications if Pokémon spawn frequently.\n\n"
            "To confirm, use: `/sub_all confirm:confirm`",
            ephemeral=True
        )
        return

    if user_id not in subscribed_users:
        subscribed_users[user_id] = set()

    available_guilds = [g for g in bot.guilds if g.id not in subscribed_users.get(user_id, set())]

    MAX_SERVERS = 10
    if len(available_guilds) > MAX_SERVERS:
        available_guilds = available_guilds[:MAX_SERVERS]
        await interaction.response.send_message(
            f"⚠️ The bot is in too many servers. Subscribing you to {MAX_SERVERS} servers only.\n"
            "For the remaining servers, please use `/sub` in each server individually.",
            ephemeral=True
        )
        return

    count = 0
    guild_names = []

    for guild in available_guilds:
        if guild.id not in subscribed_users[user_id]:
            subscribed_users[user_id].add(guild.id)
            guild_names.append(f"• {guild.name}")
            count += 1

    if count > 0:
        server_list = "\n".join(guild_names)
        await interaction.response.send_message(
            f"You've been subscribed to Pokémon notifications in {count} servers:\n\n{server_list}",
            ephemeral=True
        )

        save_subscriptions()
    else:
        await interaction.response.send_message(
            "You're already subscribed to all available servers!",
            ephemeral=True
        )


@bot.tree.command(name="unsub_all", description="Unsubscribe from all servers")
async def unsubscribe_all(interaction: discord.Interaction):
    user_id = interaction.user.id

    if user_id in subscribed_users:
        server_count = len(subscribed_users[user_id])
        del subscribed_users[user_id]
        await interaction.response.send_message(f"You've been unsubscribed from Pokémon notifications in all {server_count} servers.", ephemeral=True)

        save_subscriptions()
    else:
        await interaction.response.send_message("You weren't subscribed to any Pokémon notifications.", ephemeral=True)


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN environment variable not set!")
        exit(1)
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY environment variable not set!")
        exit(1)

    last_save_time = asyncio.get_event_loop().time()

    try:
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
    finally:
        save_subscriptions()
