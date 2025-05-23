# Pokétwo Pokédex

<img src="poketwo_image.svg" alt="Pokétwo Pokedex Bot" width="200"/>
<img src="sample.png" alt="Sample Image"/> 

A Discord bot that automatically identifies wild Pokémons from the [Pokétwo](https://poketwo.net/) Discord bot and sends notifications to subscribed users.

> **Note:**
> This bot uses the [Google Gemini AI model](https://ai.google.dev/gemini-api/docs/models) `gemini-2.5-flash-preview-04-17` to identify Pokémon. As with any AI-based system, the results (Pokémon names) may not always be accurate.

## Features

- **Real-time Pokémon Detection**  
  Monitors Pokétwo's "A wild pokémon has appeared!" messages
- **AI Identification**  
  Uses Google Gemini AI to identify Pokémon from images
- **Smart Correction System**  
  "Wrong Pokémon?" button triggers fresh AI analysis of the original image
- **Rich Notifications**  
  Color-coded embeds with:
  - Pokémon name and thumbnail
  - Direct message link
  - Server context
  - Pre-formatted catch command

## Setup

### Prerequisites

- Python 3.8 or higher
- Discord Bot Token
- Google Gemini API Key

### Installation

1. Clone this repository:
   ```
   git clone https://github.com/komiwalnut/poketwo-pokedex.git
   cd poketwo-pokedex
   ```

2. Install Python 3 and venv (if not already installed):
   ```
   sudo apt install python3 venv
   ```
3. Create and activate a virtual environment:
   ```
   python3 -m venv pokedex-venv
   source pokedex-venv/bin/activate
   ```

4. Install dependencies:
   ```
   pip install -r requirements.txt
   ```

5. Create a `.env` file based on the `.env.example` template:
   ```
   cp .env.example .env
   ```

6. Edit the `.env` file with your Discord Bot Token and Google Gemini API Key.

### Running the Bot

```
nohup python3 -u pokedex.py > /dev/null 2>&1 &
```

The bot will automatically create a `logs` directory with rotating log files.

## Commands

- `/sub` - Subscribe to receive Pokémon notifications in the current server
- `/unsub` - Unsubscribe from Pokémon notifications in the current server
- `/sub_status` - Check your subscription status across all servers
- `/unsub_all` - Unsubscribe from all servers
- `/stats` - Show bot statistics

## Getting a Discord Bot Token

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application
3. Go to the Bot tab and click "Add Bot"
4. Click "Reset Token" to view your token (keep this secret!)
5. Enable "Message Content Intent" under Privileged Gateway Intents
6. Make sure to add the following permissions: Read Messages, View Channels, Send Messages, Send Messages in Threads, Create Slash Commands, Embed Links, and Attach Files
7. Invite the bot to your server using the OAuth2 URL Generator

## Getting a Google Gemini API Key

1. Go to the [Google AI Studio](https://aistudio.google.com/)
2. Create an account if you don't have one
3. Navigate to the API keys section
4. Create a new API key and copy it (keep this secret!)
