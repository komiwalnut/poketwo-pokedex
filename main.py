import os
import discord
from discord import app_commands
from discord.ext import commands
import requests
import google.generativeai as genai
from io import BytesIO
import logging
from dotenv import load_dotenv
import json

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
POKETWO_ID = 716390085896962058
CATCH_USER_ID = 716390085896962058

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel('gemini-2.5-flash-preview-04-17')

subscribed_users = set()
pokemon_database = {}


async def load_pokemon_database():
    global pokemon_database
    try:
        response = requests.get("https://pokeapi.co/api/v2/pokemon?limit=1000")
        if response.status_code == 200:
            data = response.json()
            pokemon_database = {entry['name']: True for entry in data['results']}
            logger.info(f"Loaded {len(pokemon_database)} Pokémon names into database")
        else:
            logger.error(f"Failed to fetch Pokémon database: {response.status_code}")
    except Exception as e:
        logger.error(f"Error loading Pokémon database: {e}")


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
    await load_pokemon_database()
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} command(s)")
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}")


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if message.author.id != POKETWO_ID:
        await bot.process_commands(message)
        return

    logger.info(f"Received message from Pokétwo: '{message.content}'")
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

    if wild_pokemon_detected:
        logger.info("Wild Pokémon detected!")

        if message.attachments and len(message.attachments) > 0:
            attachment = message.attachments[0]
            await process_pokemon_image(attachment.url)

        elif message.embeds:
            for embed in message.embeds:
                if embed.image and embed.image.url:
                    logger.info(f"Found image in embed: {embed.image.url}")
                    await process_pokemon_image(embed.image.url)
                    break

    await bot.process_commands(message)


async def process_pokemon_image(image_url):
    try:
        response = requests.get(image_url)
        image_bytes = BytesIO(response.content)

        pokemon_name = await identify_pokemon(image_bytes)

        if pokemon_name:
            for user_id in subscribed_users:
                try:
                    user = await bot.fetch_user(user_id)
                    catch_command = f"<@{CATCH_USER_ID}> catch {pokemon_name}"
                    await user.send(f"Pokémon identified: {pokemon_name}\n```{catch_command}```")
                    await user.send(image_url)
                except Exception as e:
                    logger.error(f"Failed to DM user {user_id}: {e}")

    except Exception as e:
        logger.error(f"Error processing Pokémon image: {e}")


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

    except Exception as e:
        logger.error(f"Error identifying Pokémon with Gemini: {e}")
        return None


@bot.tree.command(name="sub", description="Subscribe to receive Pokémon notifications")
async def subscribe(interaction: discord.Interaction):
    user_id = interaction.user.id

    if user_id in subscribed_users:
        await interaction.response.send_message("You're already subscribed to Pokémon notifications!", ephemeral=True)
    else:
        subscribed_users.add(user_id)
        await interaction.response.send_message("You've been subscribed to Pokémon notifications!", ephemeral=True)


@bot.tree.command(name="unsub", description="Unsubscribe from Pokémon notifications")
async def unsubscribe(interaction: discord.Interaction):
    user_id = interaction.user.id

    if user_id in subscribed_users:
        subscribed_users.remove(user_id)
        await interaction.response.send_message("You've been unsubscribed from Pokémon notifications.", ephemeral=True)
    else:
        await interaction.response.send_message("You weren't subscribed to Pokémon notifications.", ephemeral=True)


@bot.tree.command(name="sub_status", description="Check your subscription status")
async def subscription_status(interaction: discord.Interaction):
    user_id = interaction.user.id

    if user_id in subscribed_users:
        await interaction.response.send_message("You are currently subscribed to Pokémon notifications.", ephemeral=True)
    else:
        await interaction.response.send_message("You are not subscribed to Pokémon notifications.", ephemeral=True)


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN environment variable not set!")
        exit(1)
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY environment variable not set!")
        exit(1)

    bot.run(DISCORD_TOKEN)
