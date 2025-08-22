import os
from dotenv import load_dotenv
import asyncio
import bot  # ton fichier bot.py
from threading import Thread
from flask import Flask

# ----- Keep Alive Flask -----
app = Flask('')

@app.route('/')
def home():
    return "Bot en ligne ! 🚀"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()

# ----- Charger les variables d'environnement -----
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN_TICKET")

if not DISCORD_TOKEN:
    print("❌ DISCORD_TOKEN_TICKET manquant dans les variables d'environnement")
    exit(1)

# ----- Lancer Flask -----
keep_alive()

# ----- Lancer le bot Discord -----
async def start_bot():
    await bot.bot.start(DISCORD_TOKEN)  # utiliser l'objet bot de bot.py

asyncio.run(start_bot())
