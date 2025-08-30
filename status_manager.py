import discord
from discord.ext import tasks
import json
import os

STATUS_CHANNEL_ID = 1408498176003932422  # Remplace par ton salon
STATUS_FILE = "status.json"  # fichier pour sauvegarder l'ID du message

class StatusManager:
    def __init__(self, bot, channel_id):
        self.bot = bot
        self.channel_id = channel_id
        self.status_message_id = None
        self.load_status_message()
        # Ne pas démarrer la boucle ici ! → start depuis on_ready

    # Charger l'ID du message depuis un JSON
    def load_status_message(self):
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, "r") as f:
                data = json.load(f)
                self.status_message_id = data.get("status_message_id")

    # Sauvegarder l'ID du message dans le JSON
    def save_status_message(self, msg_id):
        self.status_message_id = msg_id
        with open(STATUS_FILE, "w") as f:
            json.dump({"status_message_id": msg_id}, f)

    @tasks.loop(minutes=5)
    async def update_status(self):
        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            return

        if not self.status_message_id:
            # Pas encore de message → envoie un nouveau
            msg = await channel.send("✅ Bot en ligne")
            self.save_status_message(msg.id)
        else:
            try:
                msg = await channel.fetch_message(self.status_message_id)
                await msg.edit(content=f"✅ Bot toujours en ligne (maj <t:{int(discord.utils.utcnow().timestamp())}:R>)")
            except discord.NotFound:
                # Si le message a été supprimé → envoyer un nouveau et sauvegarder l'ID
                msg = await channel.send("✅ Bot en ligne")
                self.save_status_message(msg.id)
            except discord.Forbidden:
                print("❌ Je n'ai pas la permission de modifier le message de statut.")
            except Exception as e:
                print(f"❌ Erreur update status: {e}")
