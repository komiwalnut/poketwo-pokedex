import os
import discord
from discord import app_commands
from discord.ext import commands
from discord.ext import tasks
import aiohttp
import google.generativeai as genai
from io import BytesIO
import logging
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
import json
import asyncio
import uuid
import time
from rembg import remove
from PIL import Image
import cv2
import numpy as np
import random

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
    'logs/pokebot.log',
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
pending_corrections = {}
POKEMON_COLOR_CACHE = {}
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


async def rotating_status():
    while True:
        try:
            guild_count = len(bot.guilds)
            user_count = len(subscribed_users)
            total_subscriptions = sum(len(guild_ids) for guild_ids in subscribed_users.values())

            statuses = [
                {"type": discord.ActivityType.watching, "name": "for wild Pokémon!"},
                {"type": discord.ActivityType.playing, "name": f"in {guild_count} servers"},
                {"type": discord.ActivityType.listening, "name": f"to {user_count} trainers"},
                {"type": discord.ActivityType.watching, "name": "@Pokétwo spawns"},
                {"type": discord.ActivityType.watching, "name": "/sub to get alerts!"},
                {"type": discord.ActivityType.playing, "name": "/stats for bot info"},
                {"type": discord.ActivityType.listening, "name": "/unsub to stop alerts"},
                {"type": discord.ActivityType.watching, "name": "/sub_status for subscriptions"},
                {"type": discord.ActivityType.playing, "name": "/unsub_all to clear all"},
                {"type": discord.ActivityType.watching, "name": f"{total_subscriptions} active alerts"},
            ]

            status = random.choice(statuses)
            await bot.change_presence(activity=discord.Activity(**status))

            await asyncio.sleep(random.randint(120, 240))

        except Exception as err:
            logger.error(f"Status rotation error: {err}")
            await asyncio.sleep(60)


async def fetch_image(session, url):
    async with session.get(url) as response:
        return await response.read()


async def remove_background(image_bytes):
    try:
        image_bytes.seek(0)
        image = Image.open(image_bytes)

        try:
            output = remove(image)

            output_np = np.array(output)
            if len(output_np.shape) > 2 and output_np.shape[2] == 4:
                transparent_pixels = np.sum(output_np[:, :, 3] == 0)
                if transparent_pixels > 100:
                    output_buffer = BytesIO()
                    output.save(output_buffer, format="PNG")
                    output_buffer.seek(0)
                    return output_buffer
        except Exception as err:
            logger.error(f"Rembg error, falling back to custom method: {err}")

        img_np = np.array(image)

        if len(img_np.shape) > 2 and img_np.shape[2] == 4:
            img_np = cv2.cvtColor(img_np, cv2.COLOR_RGBA2RGB)

        hsv = cv2.cvtColor(img_np, cv2.COLOR_RGB2HSV)

        masks = []

        background_definitions = [
            {'lower': np.array([0, 0, 180]), 'upper': np.array([180, 70, 255])},
            {'lower': np.array([90, 40, 150]), 'upper': np.array([140, 255, 255])},
            {'lower': np.array([0, 0, 100]), 'upper': np.array([180, 30, 220])},
            {'lower': np.array([35, 40, 150]), 'upper': np.array([85, 255, 255])}
        ]

        for bg in background_definitions:
            mask = cv2.inRange(hsv, bg['lower'], bg['upper'])
            masks.append(mask)

        combined_mask = masks[0]
        for mask in masks[1:]:
            combined_mask = cv2.bitwise_or(combined_mask, mask)

        kernel = np.ones((3, 3), np.uint8)
        combined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_CLOSE, kernel)
        combined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_OPEN, kernel)

        pokemon_mask = cv2.bitwise_not(combined_mask)

        contours, _ = cv2.findContours(pokemon_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        if contours and len(contours) > 0:
            largest_contour = max(contours, key=cv2.contourArea)
            area = cv2.contourArea(largest_contour)

            if area > 500:
                clean_mask = np.zeros_like(pokemon_mask)
                cv2.drawContours(clean_mask, [largest_contour], 0, 255, -1)

                kernel = np.ones((5, 5), np.uint8)
                clean_mask = cv2.dilate(clean_mask, kernel, iterations=1)

                rgba = cv2.cvtColor(img_np, cv2.COLOR_RGB2RGBA)
                rgba[:, :, 3] = clean_mask

                output = Image.fromarray(rgba)

                output_buffer = BytesIO()
                output.save(output_buffer, format="PNG")
                output_buffer.seek(0)
                return output_buffer

        image_bytes.seek(0)
        try:
            output = remove(image)
            output_buffer = BytesIO()
            output.save(output_buffer, format="PNG")
            output_buffer.seek(0)
            return output_buffer
        except Exception as err:
            logger.error(f"Failed to process output image error: {err}")
            image_bytes.seek(0)
            return image_bytes

    except Exception as err:
        logger.error(f"Background removal error: {err}")
        image_bytes.seek(0)
        return image_bytes


@bot.listen()
async def on_interaction(interaction: discord.Interaction):
    try:
        if interaction.type == discord.InteractionType.component:
            custom_id = interaction.data.get('custom_id', '')
            if custom_id.startswith("wrong_pokemon:"):
                correction_id = custom_id.split(":")[1]
                data = pending_corrections.get(correction_id)

                if not data:
                    await interaction.response.send_message("This request has expired.", ephemeral=True)
                    return

                await interaction.response.defer(ephemeral=True, thinking=True)

                try:
                    async with aiohttp.ClientSession() as session:
                        image_data = await fetch_image(session, data["image_url"])
                        image_bytes = BytesIO(image_data)

                        processed_image = await remove_background(image_bytes)

                        original_embed = interaction.message.embeds[0]
                        previous_name = None
                        if original_embed.description and "I spotted a **" in original_embed.description:
                            previous_name = original_embed.description.split("I spotted a **")[1].split("**")[0].lower()

                        new_name = await identify_pokemon(processed_image, previous_name)

                        if not new_name:
                            await interaction.followup.send("Identification failed. Try again later.")
                            return

                        if new_name:
                            new_color = await get_pokemon_color(new_name)

                            new_embed = discord.Embed(
                                title=original_embed.title,
                                description=f"I spotted a **{new_name.capitalize()}** in **{data['guild_name']}**!",
                                color=new_color
                            )

                            for field in original_embed.fields:
                                if field.name == "Catch Command":
                                    new_embed.add_field(
                                        name="Catch Command",
                                        value=f"```<@716390085896962058> catch {new_name}```",
                                        inline=False
                                    )
                                else:
                                    new_embed.add_field(
                                        name=field.name,
                                        value=field.value,
                                        inline=field.inline
                                    )

                            new_embed.set_thumbnail(url=original_embed.thumbnail.url)
                            new_embed.set_footer(text=original_embed.footer.text)

                            if new_name != previous_name:
                                await interaction.message.edit(content=f"<@716390085896962058> catch {new_name}", embed=new_embed)
                                await interaction.followup.send(f"Updated from **{previous_name.capitalize()}** to **{new_name.capitalize()}**!")
                            else:
                                await interaction.followup.send("AI still identified the same Pokémon. Try a new spawn instead.")
                        else:
                            await interaction.followup.send("Failed to re-identify Pokémon.")

                except Exception as err:
                    logger.error(f"Re-ID error: {err}")
                    await interaction.followup.send("Error processing request. Please try a new spawn.")

    except Exception as err:
        logger.error(f"Interaction error: {err}")


@bot.event
async def on_ready():
    logger.info(f"{bot.user} is online and ready!")
    load_subscriptions()

    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="for wild Pokémon!",
            details="Scanning servers",
            state=f"in {len(bot.guilds)} communities"
        ),
        status=discord.Status.online
    )

    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} command(s)")
        bot.loop.create_task(periodic_save())
        bot.loop.create_task(rotating_status())
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
    wild_pokemon_detected = any(
        pattern in message.content or
        any(embed.title and pattern in embed.title or
            embed.description and pattern in embed.description
            for embed in message.embeds)
        for pattern in [
            "A wild pokémon has appeared!",
            "wild pokémon has appeared!",
            "fled. A new wild pokémon has appeared!"
        ]
    )

    if wild_pokemon_detected and message.guild:
        logger.info(f"Wild Pokémon detected in server: {message.guild.name}!")
        guild_id = message.guild.id
        guild_name = message.guild.name
        message_link = f"https://discord.com/channels/{guild_id}/{message.channel.id}/{message.id}"

        image_url = None
        if message.attachments:
            image_url = message.attachments[0].url
        else:
            for embed in message.embeds:
                if embed.image and embed.image.url:
                    image_url = embed.image.url
                    break

        if image_url:
            await process_pokemon_image(image_url, guild_id, guild_name, message_link)

    await bot.process_commands(message)


async def process_pokemon_image(image_url, guild_id, guild_name, message_link):
    try:
        async with aiohttp.ClientSession() as session:
            image_data = await fetch_image(session, image_url)
            image_bytes = BytesIO(image_data)

            processed_image = await remove_background(image_bytes)
            pokemon_name = await identify_pokemon(processed_image)

            if pokemon_name:
                pokemon_color = await get_pokemon_color(pokemon_name)
                correction_id = str(uuid.uuid4())
                pending_corrections[correction_id] = {
                    "image_url": image_url,
                    "guild_name": guild_name,
                    "message_link": message_link,
                    "image_bytes": image_bytes.getvalue()
                }

                for user_id, guilds in subscribed_users.items():
                    if guild_id in guilds:
                        try:
                            user = await bot.fetch_user(user_id)
                            embed = discord.Embed(
                                title="Wild Pokémon Appeared! ✨",
                                description=f"I spotted a **{pokemon_name.capitalize()}** in **{guild_name}**!",
                                color=pokemon_color
                            )
                            embed.add_field(
                                name="Catch Command",
                                value=f"```<@716390085896962058> catch {pokemon_name}```",
                                inline=False
                            )
                            embed.add_field(
                                name="Server Location",
                                value=f"[Click here to go to the message]({message_link})",
                                inline=False
                            )
                            embed.set_thumbnail(url=image_url)
                            embed.set_footer(text=f"PokéDetector | Guild: {guild_name}")

                            view = discord.ui.View()
                            view.add_item(discord.ui.Button(
                                label="Wrong Pokemon",
                                style=discord.ButtonStyle.danger,
                                custom_id=f"wrong_pokemon:{correction_id}"
                            ))

                            await user.send(content=f"<@716390085896962058> catch {pokemon_name}", embed=embed, view=view)
                        except Exception as err:
                            logger.error(f"Failed to DM user {user_id}: {err}")
    except Exception as err:
        logger.error(f"Error processing Pokémon image: {err}")


async def get_pokemon_color(pokemon_name):
    if pokemon_name in POKEMON_COLOR_CACHE:
        return POKEMON_COLOR_CACHE[pokemon_name]

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://pokeapi.co/api/v2/pokemon/{pokemon_name}") as response:
                if response.status == 200:
                    data = await response.json()
                    primary_type = data["types"][0]["type"]["name"]
                    type_colors = {
                        "normal": 0xA8A77A, "fire": 0xEE8130, "water": 0x6390F0,
                        "electric": 0xF7D02C, "grass": 0x7AC74C, "ice": 0x96D9D6,
                        "fighting": 0xC22E28, "poison": 0xA33EA1, "ground": 0xE2BF65,
                        "flying": 0xA98FF3, "psychic": 0xF95587, "bug": 0xA6B91A,
                        "rock": 0xB6A136, "ghost": 0x735797, "dragon": 0x6F35FC,
                        "dark": 0x705746, "steel": 0xB7B7CE, "fairy": 0xD685AD
                    }
                    color = type_colors.get(primary_type, 0xFF5252)
                    POKEMON_COLOR_CACHE[pokemon_name] = color
                    return color
        return 0xFF5252
    except Exception as err:
        logger.error(f"Error getting Pokémon color: {err}")
        return 0xFF5252


async def identify_pokemon(image_bytes, previous_name=None):
    try:
        image_bytes.seek(0)

        if previous_name:
            prompt = f"This Pokémon was previously identified as '{previous_name}', but that might be incorrect. Look carefully at the features, and colors. What Pokémon is this? Reply ONLY with the lowercase English name, nothing else."
        else:
            prompt = "What Pokémon is this? Reply ONLY with the lowercase English name, nothing else."

        try:
            response = await asyncio.wait_for(
                gemini_model.generate_content_async([
                    prompt,
                    {"mime_type": "image/png", "data": image_bytes.read()}
                ]),
                timeout=10
            )
        except asyncio.TimeoutError:
            logger.warning("Gemini API timeout")
            return None

        image_bytes.seek(0)
        name = response.text.strip().lower()

        if previous_name and name == previous_name:
            image_bytes.seek(0)
            retry_prompt = f"This is definitely NOT {previous_name}. Look more carefully at the distinctive features. What other Pokémon species could this be? Reply ONLY with the lowercase English name, nothing else."

            try:
                retry_response = await asyncio.wait_for(
                    gemini_model.generate_content_async([
                        retry_prompt,
                        {"mime_type": "image/png", "data": image_bytes.read()}
                    ]),
                    timeout=7
                )

                image_bytes.seek(0)
                retry_name = retry_response.text.strip().lower()

                if retry_name != previous_name and len(retry_name) >= 3 and not any(c.isdigit() for c in retry_name):
                    return retry_name
            except Exception as err:
                logger.error(f"Error in retry identification: {err}")
                pass

        if len(name) < 3 or any(c.isdigit() for c in name):
            logger.error(f"Invalid Pokémon name from API: {name}")
            return None

        return name
    except Exception as err:
        logger.error(f"Error identifying Pokémon: {err}")
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


@bot.tree.command(name="stats", description="Show bot statistics")
@app_commands.checks.cooldown(1, 60, key=lambda i: i.user.id)
async def stats(interaction: discord.Interaction):
    server_count = len(bot.guilds)
    user_count = len(subscribed_users)

    total_subscriptions = sum(len(guild_ids) for guild_ids in subscribed_users.values())

    embed = discord.Embed(
        title="📊 Pokétwo Pokédex Stats",
        description="Current bot statistics and usage information",
        color=0x4285F4
    )

    embed.add_field(name="Servers", value=f"`{server_count}` servers", inline=True)
    embed.add_field(name="Subscribed Users", value=f"`{user_count}` users", inline=True)
    embed.add_field(name="Total Subscriptions", value=f"`{total_subscriptions}` subscriptions", inline=True)

    await interaction.response.send_message(embed=embed, ephemeral=True)


if __name__ == "__main__":
    if not DISCORD_TOKEN or not GEMINI_API_KEY:
        logger.error("Missing required environment variables!")
        exit(1)

    last_save_time = asyncio.get_event_loop().time()
    try:
        bot.run(DISCORD_TOKEN)
    except Exception as e:
        logger.error(f"Bot crashed: {e}")
    finally:
        save_subscriptions()
