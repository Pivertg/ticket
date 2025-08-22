import os
import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
import json
from threading import Thread
import time


# ---------------------------------
# ----- Bot Discord -----
intents = discord.Intents.default()
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

CATEGORY_NAME = os.getenv("TICKETS_CATEGORY", "TICKETS")
STAFF_ROLE_ID = int(os.getenv("STAFF_ROLE_ID", "1408450110496702545")) if os.getenv("STAFF_ROLE_ID") else 1408450110496702545

# ----- Persistance des messages tickets -----
TICKET_FILE = "tickets.json"
OPEN_TICKETS_FILE = "open_tickets.json"
STATUS_FILE = "status.json"
TICKET_MESSAGE_FILE = "ticket_message.json"
CLOSE_BUTTON_FILE = "close_buttons.json"
STATUS_CHANNEL_ID = 1408498176003932422  # salon status
status_message_id = None
custom_ticket_message = None

# ----- Gestion JSON -----
def save_ticket_message(message_id, channel_id):
    try:
        with open(TICKET_FILE, "r") as f:
            data = json.load(f)
    except:
        data = {}
    data[str(message_id)] = channel_id
    with open(TICKET_FILE, "w") as f:
        json.dump(data, f, indent=4)

def load_ticket_messages():
    try:
        with open(TICKET_FILE, "r") as f:
            data = json.load(f)
        return {int(k): v for k, v in data.items()}
    except:
        return {}

def save_open_ticket(user_id, channel_id):
    data = {}
    if os.path.exists(OPEN_TICKETS_FILE):
        with open(OPEN_TICKETS_FILE, "r") as f:
            try:
                data = json.load(f)
            except:
                data = {}
    # ✅ On empêche d'écraser : si l'user existe déjà on ne recrée pas
    if str(user_id) not in data:
        data[str(user_id)] = {
            "ticket_channel_id": channel_id,
            "created_at": int(time.time())
        }
    with open(OPEN_TICKETS_FILE, "w") as f:
        json.dump(data, f, indent=4)

def remove_open_ticket(user_id):
    if not os.path.exists(OPEN_TICKETS_FILE):
        return
    with open(OPEN_TICKETS_FILE, "r") as f:
        try:
            data = json.load(f)
        except:
            data = {}
    data.pop(str(user_id), None)
    with open(OPEN_TICKETS_FILE, "w") as f:
        json.dump(data, f, indent=4)

def load_open_tickets():
    try:
        with open(OPEN_TICKETS_FILE, "r") as f:
            data = json.load(f)
        return {int(k): v["ticket_channel_id"] for k, v in data.items()}
    except:
        return {}

def user_has_open_ticket(user_id):
    """Vérifier si l'utilisateur a déjà un ticket ouvert"""
    try:
        with open(OPEN_TICKETS_FILE, "r") as f:
            data = json.load(f)
        return str(user_id) in data
    except:
        return False

def save_status_message(message_id):
    with open(STATUS_FILE, "w") as f:
        json.dump({"status_message_id": message_id}, f, indent=4)

def load_status_message():
    try:
        return json.load(open(STATUS_FILE, "r")).get("status_message_id")
    except:
        return None

def save_ticket_message_config(message):
    with open(TICKET_MESSAGE_FILE, "w") as f:
        json.dump({"ticket_message": message}, f, indent=4)

def load_ticket_message_config():
    try:
        with open(TICKET_MESSAGE_FILE, "r") as f:
            data = json.load(f)
        return data.get("ticket_message", "{user} Merci d'avoir ouvert un ticket. Un membre du staff va te répondre.")
    except:
        return "{user} Merci d'avoir ouvert un ticket. Un membre du staff va te répondre."

def save_close_button_message(message_id, channel_id):
    try:
        with open(CLOSE_BUTTON_FILE, "r") as f:
            data = json.load(f)
    except:
        data = {}
    data[str(message_id)] = channel_id
    with open(CLOSE_BUTTON_FILE, "w") as f:
        json.dump(data, f, indent=4)

def load_close_button_messages():
    try:
        with open(CLOSE_BUTTON_FILE, "r") as f:
            data = json.load(f)
        return {int(k): v for k, v in data.items()}
    except:
        return {}

def remove_close_button_message(message_id):
    try:
        with open(CLOSE_BUTTON_FILE, "r") as f:
            data = json.load(f)
    except:
        return
    data.pop(str(message_id), None)
    with open(CLOSE_BUTTON_FILE, "w") as f:
        json.dump(data, f, indent=4)

ticket_messages = load_ticket_messages()
open_tickets = load_open_tickets()
custom_ticket_message = load_ticket_message_config()
close_button_messages = load_close_button_messages()

# ----- Vue bouton ticket -----
class TicketButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Ouvrir un ticket", style=discord.ButtonStyle.green)
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        
        # ✅ Vérifier si l'utilisateur a déjà un ticket ouvert
        if user_has_open_ticket(user_id):
            existing_channel_id = open_tickets.get(user_id)
            await interaction.response.send_message(
                f"❌ Tu as déjà un ticket ouvert <#{existing_channel_id}> ! Ferme ton ticket actuel avant d'en créer un nouveau.", 
                ephemeral=True
            )
            return

        channel = await create_ticket(interaction.user, interaction.guild)
        save_open_ticket(user_id, channel.id)
        open_tickets[user_id] = channel.id
        await interaction.response.send_message(f"🎫 Ticket créé ! <#{channel.id}>", ephemeral=True)

# ----- Vue bouton fermeture ticket -----
class CloseTicketButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🗑️ Fermer le ticket", style=discord.ButtonStyle.red)
    async def close_ticket_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message(
                "Erreur: Impossible d'accéder au serveur.", ephemeral=True
            )

        # Vérifier les permissions staff
        if STAFF_ROLE_ID:
            role = guild.get_role(STAFF_ROLE_ID)
            member = guild.get_member(interaction.user.id)
            if role and member and role not in member.roles:
                return await interaction.response.send_message(
                    "❌ Seul le staff peut fermer les tickets.", ephemeral=True
                )

        await interaction.response.send_message(
            "🗑️ Fermeture du ticket dans 5 secondes..."
        )
        await asyncio.sleep(5)

        # ✅ Supprimer de la liste des tickets ouverts
        for user_id, ch_id in list(open_tickets.items()):
            if ch_id == interaction.channel.id:
                remove_open_ticket(user_id)
                open_tickets.pop(int(user_id), None)
                break

        # ✅ Supprimer le message du JSON des boutons de fermeture  
        for msg_id, ch_id in list(close_button_messages.items()):
            if ch_id == interaction.channel.id:
                remove_close_button_message(msg_id)
                close_button_messages.pop(int(msg_id), None)
                break

        # ✅ Vérifier que le channel existe encore avant de le supprimer
        try:
            await interaction.channel.delete(reason="Ticket fermé par le staff")
        except discord.NotFound:
            print(f"Channel {interaction.channel.id} déjà supprimé")
        except Exception as e:
            print(f"Erreur lors de la suppression du channel: {e}")

# ----- Création ticket -----
async def create_ticket(user, guild):
    category = discord.utils.get(guild.categories, name=CATEGORY_NAME)
    if category is None:
        category = await guild.create_category(CATEGORY_NAME, reason="Catégorie tickets")

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        user: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }

    if STAFF_ROLE_ID:
        role = guild.get_role(STAFF_ROLE_ID)
        if role:
            overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    channel = await guild.create_text_channel(
        name=f"ticket-{user.name}",
        category=category,
        overwrites=overwrites,
        reason="Ticket créé"
    )

    # Utiliser le message personnalisé ou le message par défaut
    message = custom_ticket_message.replace("{user}", user.mention)
    msg = await channel.send(message, view=CloseTicketButton())
    
    # Sauvegarder le message avec bouton de fermeture
    close_button_messages[msg.id] = channel.id
    save_close_button_message(msg.id, channel.id)
    
    return channel

# ----- /ticket commande -----
@tree.command(description="Créer un ticket privé avec le staff")
async def ticket(interaction: discord.Interaction):
    user_id = interaction.user.id
    
    # ✅ Vérifier si l'utilisateur a déjà un ticket ouvert
    if user_has_open_ticket(user_id):
        existing_channel_id = open_tickets.get(user_id)
        await interaction.response.send_message(
            f"❌ Tu as déjà un ticket ouvert <#{existing_channel_id}> ! Ferme ton ticket actuel avant d'en créer un nouveau.", 
            ephemeral=True
        )
        return

    guild = interaction.guild
    if not guild:
        return await interaction.response.send_message(
            "Utilise cette commande sur le serveur.", ephemeral=True
        )

    channel = await create_ticket(interaction.user, guild)
    save_open_ticket(user_id, channel.id)
    open_tickets[user_id] = channel.id
    
    await interaction.response.send_message(
        f"🎫 Ticket créé : <#{channel.id}>", ephemeral=True
    )

# ----- /close_ticket -----
@tree.command(description="[STAFF] Fermer un ticket")
async def close_ticket(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        return await interaction.response.send_message(
            "Utilise cette commande sur le serveur.", ephemeral=True
        )

    if not isinstance(interaction.channel, discord.TextChannel):
        return await interaction.response.send_message(
            "❌ Cette commande ne peut être utilisée que dans un salon textuel.",
            ephemeral=True
        )

    if not interaction.channel.name.startswith("ticket-"):
        return await interaction.response.send_message(
            "❌ Cette commande ne peut être utilisée que dans un ticket.",
            ephemeral=True
        )

    if STAFF_ROLE_ID:
        role = guild.get_role(STAFF_ROLE_ID)
        member = guild.get_member(interaction.user.id)
        if role and member and role not in member.roles:
            return await interaction.response.send_message(
                "❌ Seul le staff peut fermer les tickets.", ephemeral=True
            )

    await interaction.response.send_message(
        "🗑️ Fermeture du ticket dans 5 secondes..."
    )
    await asyncio.sleep(5)

    # ✅ Supprimer de la liste des tickets ouverts
    for user_id, ch_id in list(open_tickets.items()):
        if ch_id == interaction.channel.id:
            remove_open_ticket(user_id)
            open_tickets.pop(int(user_id), None)
            break

    # ✅ Supprimer le message du JSON des boutons de fermeture  
    for msg_id, ch_id in list(close_button_messages.items()):
        if ch_id == interaction.channel.id:
            remove_close_button_message(msg_id)
            close_button_messages.pop(int(msg_id), None)
            break

    # ✅ Vérifier que le channel existe encore avant de le supprimer
    try:
        await interaction.channel.delete(reason="Ticket fermé par le staff")
    except discord.NotFound:
        print(f"Channel {interaction.channel.id} déjà supprimé")
    except Exception as e:
        print(f"Erreur lors de la suppression du channel: {e}")

# ----- /config avec bouton -----
@tree.command(description="[STAFF] Configurer le message ticket")
@app_commands.describe(
    channel_id="ID du salon où poster le message",
    message_text="Texte du message avec bouton",
    ticket_message="Message envoyé dans le ticket (utilise {user} pour mentionner l'utilisateur)"
)
async def config(interaction: discord.Interaction, channel_id: str, message_text: str, ticket_message: str = None):
    guild = interaction.guild
    if not guild:
        return await interaction.response.send_message(
            "Cette commande doit être utilisée sur le serveur.", ephemeral=True
        )

    if STAFF_ROLE_ID:
        role = guild.get_role(STAFF_ROLE_ID)
        member = guild.get_member(interaction.user.id)
        if role and member and role not in member.roles:
            return await interaction.response.send_message(
                "❌ Seul le staff peut configurer.", ephemeral=True
            )

    try:
        channel = guild.get_channel(int(channel_id))
        if not channel:
            return await interaction.response.send_message(
                "Salon introuvable.", ephemeral=True
            )
    except:
        return await interaction.response.send_message(
            "ID de salon invalide.", ephemeral=True
        )

    msg = await channel.send(message_text, view=TicketButton())
    ticket_messages[msg.id] = channel.id
    save_ticket_message(msg.id, channel.id)
    
    # Configurer le message de ticket si fourni
    global custom_ticket_message
    if ticket_message:
        custom_ticket_message = ticket_message
        save_ticket_message_config(ticket_message)
        response_text = f"Message configuré dans {channel.mention} avec bouton 'Ouvrir un ticket'.\n✅ Message d'accueil du ticket configuré : `{ticket_message}`"
    else:
        response_text = f"Message configuré dans {channel.mention} avec bouton 'Ouvrir un ticket'."

    await interaction.response.send_message(response_text, ephemeral=True)

# ----- Update status -----
@tasks.loop(minutes=5)
async def update_status():
    global status_message_id
    channel = bot.get_channel(STATUS_CHANNEL_ID)
    if not channel:
        return
        
    if not status_message_id:
        msg = await channel.send("✅ Bot en ligne")
        status_message_id = msg.id
        save_status_message(status_message_id)
    else:
        try:
            msg = await channel.fetch_message(status_message_id)
            await msg.edit(
                content=f"✅ Bot toujours en ligne (maj <t:{int(discord.utils.utcnow().timestamp())}:R>)"
            )
        except:
            msg = await channel.send("✅ Bot en ligne")
            status_message_id = msg.id
            save_status_message(status_message_id)

# ----- Vérification automatique des tickets -----
@tasks.loop(minutes=1)
async def check_tickets():
    global open_tickets
    to_remove = []
    for user_id, channel_id in open_tickets.items():
        found = False
        for guild in bot.guilds:
            if guild.get_channel(channel_id):
                found = True
                break
        if not found:
            to_remove.append(user_id)

    for user_id in to_remove:
        remove_open_ticket(user_id)
        open_tickets.pop(int(user_id), None)
        print(f"Ticket de {user_id} supprimé du JSON car le salon n'existe plus.")

# ----- on_ready -----
@bot.event
async def on_ready():
    global status_message_id, custom_ticket_message
    await tree.sync()
    print(f"[Manager] Connecté en tant que {bot.user}")

    # Charger le message de ticket personnalisé
    custom_ticket_message = load_ticket_message_config()

    # Restaurer les boutons des messages de ticket
    for msg_id, channel_id in ticket_messages.items():
        channel = bot.get_channel(channel_id)
        if channel:
            try:
                msg = await channel.fetch_message(msg_id)
                await msg.edit(view=TicketButton())
            except:
                pass
    
    # Restaurer les boutons de fermeture des tickets
    for msg_id, channel_id in close_button_messages.items():
        channel = bot.get_channel(channel_id)
        if channel:
            try:
                msg = await channel.fetch_message(msg_id)
                await msg.edit(view=CloseTicketButton())
            except:
                pass

    # Gérer le message de status
    status_message_id = load_status_message()
    channel = bot.get_channel(STATUS_CHANNEL_ID)
    if channel:
        if status_message_id:
            try:
                msg = await channel.fetch_message(status_message_id)
                await msg.edit(content="✅ Bot en ligne (redémarré)")
            except:
                msg = await channel.send("✅ Bot en ligne")
                status_message_id = msg.id
                save_status_message(status_message_id)
        else:
            msg = await channel.send("✅ Bot en ligne")
            status_message_id = msg.id
            save_status_message(status_message_id)

    # Démarrer les tâches
    update_status.start()
    check_tickets.start()

# ----- Lancement du bot -----
if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN_TICKET")
    if not token:
        print("❌ DISCORD_TOKEN_TICKET manquant dans les variables d'environnement")
        exit(1)
    bot.run(token)