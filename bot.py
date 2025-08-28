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

# ----- Configuration par serveur -----
SERVERS_CONFIG_FILE = "servers_config.json"
TICKET_MESSAGES_FILE = "ticket_messages.json"  # Messages avec boutons par serveur
OPEN_TICKETS_FILE = "open_tickets.json"
STATUS_FILE = "status.json"
CLOSE_BUTTON_FILE = "close_buttons.json"

# Configuration par défaut pour un nouveau serveur
DEFAULT_CONFIG = {
    "category_name": "TICKETS",
    "staff_role_id": None,
    "ticket_message": "{user} Merci d'avoir ouvert un ticket. Un membre du staff va te répondre.",
    "status_channel_id": None
}

status_message_id = None

# ----- Gestion JSON Multi-Serveurs -----
def load_servers_config():
    """Charger la configuration de tous les serveurs"""
    try:
        with open(SERVERS_CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_servers_config(config):
    """Sauvegarder la configuration de tous les serveurs"""
    with open(SERVERS_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

def get_server_config(guild_id):
    """Obtenir la configuration d'un serveur spécifique"""
    config = load_servers_config()
    guild_id_str = str(guild_id)
    if guild_id_str not in config:
        config[guild_id_str] = DEFAULT_CONFIG.copy()
        save_servers_config(config)
    return config[guild_id_str]

def update_server_config(guild_id, updates):
    """Mettre à jour la configuration d'un serveur"""
    config = load_servers_config()
    guild_id_str = str(guild_id)
    if guild_id_str not in config:
        config[guild_id_str] = DEFAULT_CONFIG.copy()
    config[guild_id_str].update(updates)
    save_servers_config(config)

def load_ticket_messages():
    """Charger les messages de tickets par serveur"""
    try:
        with open(TICKET_MESSAGES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Convertir les clés en int pour les message_id
        result = {}
        for guild_id, messages in data.items():
            result[int(guild_id)] = {int(msg_id): channel_id for msg_id, channel_id in messages.items()}
        return result
    except:
        return {}

def save_ticket_messages(ticket_messages):
    """Sauvegarder les messages de tickets par serveur"""
    # Convertir les clés en string pour JSON
    data = {}
    for guild_id, messages in ticket_messages.items():
        data[str(guild_id)] = {str(msg_id): channel_id for msg_id, channel_id in messages.items()}
    
    with open(TICKET_MESSAGES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def add_ticket_message(guild_id, message_id, channel_id):
    """Ajouter un message de ticket pour un serveur"""
    ticket_messages = load_ticket_messages()
    if guild_id not in ticket_messages:
        ticket_messages[guild_id] = {}
    ticket_messages[guild_id][message_id] = channel_id
    save_ticket_messages(ticket_messages)

def remove_ticket_message(guild_id, message_id):
    """Supprimer un message de ticket pour un serveur"""
    ticket_messages = load_ticket_messages()
    if guild_id in ticket_messages and message_id in ticket_messages[guild_id]:
        del ticket_messages[guild_id][message_id]
        save_ticket_messages(ticket_messages)

def save_open_ticket(user_id, channel_id, guild_id):
    data = {}
    if os.path.exists(OPEN_TICKETS_FILE):
        with open(OPEN_TICKETS_FILE, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except:
                data = {}
    
    # Clé unique : user_id + guild_id pour permettre un ticket par serveur
    key = f"{user_id}_{guild_id}"
    if key not in data:
        data[key] = {
            "user_id": user_id,
            "guild_id": guild_id,
            "ticket_channel_id": channel_id,
            "created_at": int(time.time())
        }
    with open(OPEN_TICKETS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def remove_open_ticket(user_id, guild_id):
    if not os.path.exists(OPEN_TICKETS_FILE):
        return
    with open(OPEN_TICKETS_FILE, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except:
            data = {}
    key = f"{user_id}_{guild_id}"
    data.pop(key, None)
    with open(OPEN_TICKETS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def load_open_tickets():
    try:
        with open(OPEN_TICKETS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Retourner un dict avec clé user_id_guild_id
        return data
    except:
        return {}

def user_has_open_ticket(user_id, guild_id):
    """Vérifier si l'utilisateur a déjà un ticket ouvert sur ce serveur"""
    try:
        with open(OPEN_TICKETS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        key = f"{user_id}_{guild_id}"
        return key in data
    except:
        return False

def get_user_open_ticket(user_id, guild_id):
    """Obtenir le channel ID du ticket ouvert de l'utilisateur sur ce serveur"""
    try:
        with open(OPEN_TICKETS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        key = f"{user_id}_{guild_id}"
        if key in data:
            return data[key]["ticket_channel_id"]
        return None
    except:
        return None

def save_status_message(message_id):
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump({"status_message_id": message_id}, f, indent=4)

def load_status_message():
    try:
        return json.load(open(STATUS_FILE, "r", encoding="utf-8")).get("status_message_id")
    except:
        return None

def save_close_button_message(message_id, channel_id, guild_id):
    try:
        with open(CLOSE_BUTTON_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except:
        data = {}
    data[str(message_id)] = {"channel_id": channel_id, "guild_id": guild_id}
    with open(CLOSE_BUTTON_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def load_close_button_messages():
    try:
        with open(CLOSE_BUTTON_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {int(k): v for k, v in data.items()}
    except:
        return {}

def remove_close_button_message(message_id):
    try:
        with open(CLOSE_BUTTON_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except:
        return
    data.pop(str(message_id), None)
    with open(CLOSE_BUTTON_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# Variables globales
ticket_messages = load_ticket_messages()
open_tickets = load_open_tickets()
close_button_messages = load_close_button_messages()

# ----- Vue bouton ticket -----
class TicketButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Ouvrir un ticket", style=discord.ButtonStyle.green)
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        guild_id = interaction.guild.id
        
        # Vérifier si l'utilisateur a déjà un ticket ouvert sur ce serveur
        if user_has_open_ticket(user_id, guild_id):
            existing_channel_id = get_user_open_ticket(user_id, guild_id)
            await interaction.response.send_message(
                f"❌ Tu as déjà un ticket ouvert <#{existing_channel_id}> sur ce serveur ! Ferme ton ticket actuel avant d'en créer un nouveau.", 
                ephemeral=True
            )
            return

        channel = await create_ticket(interaction.user, interaction.guild)
        save_open_ticket(user_id, channel.id, guild_id)
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

        # Obtenir la configuration du serveur
        server_config = get_server_config(guild.id)
        staff_role_id = server_config.get("staff_role_id")

        # Vérifier les permissions staff
        if staff_role_id:
            role = guild.get_role(staff_role_id)
            member = guild.get_member(interaction.user.id)
            if role and member and role not in member.roles:
                return await interaction.response.send_message(
                    "❌ Seul le staff peut fermer les tickets.", ephemeral=True
                )

        await interaction.response.send_message(
            "🗑️ Fermeture du ticket dans 5 secondes..."
        )
        await asyncio.sleep(5)

        # Supprimer de la liste des tickets ouverts
        global open_tickets
        to_remove = []
        for key, ticket_data in open_tickets.items():
            if ticket_data["ticket_channel_id"] == interaction.channel.id:
                user_id = ticket_data["user_id"]
                guild_id = ticket_data["guild_id"]
                remove_open_ticket(user_id, guild_id)
                to_remove.append(key)
                break
        
        for key in to_remove:
            open_tickets.pop(key, None)

        # Supprimer le message du JSON des boutons de fermeture  
        for msg_id, data in list(close_button_messages.items()):
            if data["channel_id"] == interaction.channel.id:
                remove_close_button_message(msg_id)
                close_button_messages.pop(int(msg_id), None)
                break

        # Vérifier que le channel existe encore avant de le supprimer
        try:
            await interaction.channel.delete(reason="Ticket fermé par le staff")
        except discord.NotFound:
            print(f"Channel {interaction.channel.id} déjà supprimé")
        except Exception as e:
            print(f"Erreur lors de la suppression du channel: {e}")

# ----- Création ticket -----
async def create_ticket(user, guild):
    server_config = get_server_config(guild.id)
    category_name = server_config.get("category_name", "TICKETS")
    staff_role_id = server_config.get("staff_role_id")
    ticket_message = server_config.get("ticket_message", "{user} Merci d'avoir ouvert un ticket. Un membre du staff va te répondre.")

    category = discord.utils.get(guild.categories, name=category_name)
    if category is None:
        category = await guild.create_category(category_name, reason="Catégorie tickets")

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        user: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }

    if staff_role_id:
        role = guild.get_role(staff_role_id)
        if role:
            overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    channel = await guild.create_text_channel(
        name=f"ticket-{user.name}",
        category=category,
        overwrites=overwrites,
        reason="Ticket créé"
    )

    # Utiliser le message personnalisé du serveur
    message = ticket_message.replace("{user}", user.mention)
    msg = await channel.send(message, view=CloseTicketButton())
    
    # Sauvegarder le message avec bouton de fermeture
    close_button_messages[msg.id] = {"channel_id": channel.id, "guild_id": guild.id}
    save_close_button_message(msg.id, channel.id, guild.id)
    
    return channel

# ----- /ticket commande -----
@tree.command(description="Créer un ticket privé avec le staff")
async def ticket(interaction: discord.Interaction):
    user_id = interaction.user.id
    guild_id = interaction.guild.id
    
    # Vérifier si l'utilisateur a déjà un ticket ouvert sur ce serveur
    if user_has_open_ticket(user_id, guild_id):
        existing_channel_id = get_user_open_ticket(user_id, guild_id)
        await interaction.response.send_message(
            f"❌ Tu as déjà un ticket ouvert <#{existing_channel_id}> sur ce serveur ! Ferme ton ticket actuel avant d'en créer un nouveau.", 
            ephemeral=True
        )
        return

    guild = interaction.guild
    if not guild:
        return await interaction.response.send_message(
            "Utilise cette commande sur le serveur.", ephemeral=True
        )

    channel = await create_ticket(interaction.user, guild)
    save_open_ticket(user_id, channel.id, guild_id)
    
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

    server_config = get_server_config(guild.id)
    staff_role_id = server_config.get("staff_role_id")

    if staff_role_id:
        role = guild.get_role(staff_role_id)
        member = guild.get_member(interaction.user.id)
        if role and member and role not in member.roles:
            return await interaction.response.send_message(
                "❌ Seul le staff peut fermer les tickets.", ephemeral=True
            )

    await interaction.response.send_message(
        "🗑️ Fermeture du ticket dans 5 secondes..."
    )
    await asyncio.sleep(5)

    # Supprimer de la liste des tickets ouverts
    global open_tickets
    to_remove = []
    for key, ticket_data in open_tickets.items():
        if ticket_data["ticket_channel_id"] == interaction.channel.id:
            user_id = ticket_data["user_id"]
            guild_id = ticket_data["guild_id"]
            remove_open_ticket(user_id, guild_id)
            to_remove.append(key)
            break
    
    for key in to_remove:
        open_tickets.pop(key, None)

    # Supprimer le message du JSON des boutons de fermeture  
    for msg_id, data in list(close_button_messages.items()):
        if data["channel_id"] == interaction.channel.id:
            remove_close_button_message(msg_id)
            close_button_messages.pop(int(msg_id), None)
            break

    # Vérifier que le channel existe encore avant de le supprimer
    try:
        await interaction.channel.delete(reason="Ticket fermé par le staff")
    except discord.NotFound:
        print(f"Channel {interaction.channel.id} déjà supprimé")
    except Exception as e:
        print(f"Erreur lors de la suppression du channel: {e}")

# ----- /config avec bouton -----
@tree.command(description="[ADMIN] Configurer le système de tickets pour ce serveur")
@app_commands.describe(
    channel_id="ID du salon où poster le message avec bouton",
    message_text="Texte du message avec bouton",
    ticket_message="Message envoyé dans le ticket (utilise {user} pour mentionner l'utilisateur)",
    staff_role_id="ID du rôle staff (optionnel)",
    category_name="Nom de la catégorie des tickets (optionnel)"
)
async def config(interaction: discord.Interaction, channel_id: str, message_text: str, 
                ticket_message: str = None, staff_role_id: str = None, category_name: str = None):
    guild = interaction.guild
    if not guild:
        return await interaction.response.send_message(
            "Cette commande doit être utilisée sur le serveur.", ephemeral=True
        )

    # Vérification permission ADMIN
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(
            "❌ Seuls les administrateurs peuvent utiliser cette commande.", ephemeral=True
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

    # Mettre à jour la configuration du serveur
    updates = {}
    if ticket_message:
        updates["ticket_message"] = ticket_message
    if staff_role_id:
        try:
            updates["staff_role_id"] = int(staff_role_id)
        except:
            return await interaction.response.send_message(
                "ID de rôle staff invalide.", ephemeral=True
            )
    if category_name:
        updates["category_name"] = category_name
    
    if updates:
        update_server_config(guild.id, updates)

    # Créer le message avec bouton
    msg = await channel.send(message_text, view=TicketButton())
    
    # Sauvegarder le message de ticket
    global ticket_messages
    if guild.id not in ticket_messages:
        ticket_messages[guild.id] = {}
    ticket_messages[guild.id][msg.id] = channel.id
    add_ticket_message(guild.id, msg.id, channel.id)
    
    response_parts = [f"✅ Message configuré dans {channel.mention} avec bouton 'Ouvrir un ticket'."]
    
    if ticket_message:
        response_parts.append(f"✅ Message d'accueil du ticket : `{ticket_message}`")
    if staff_role_id:
        role = guild.get_role(int(staff_role_id))
        if role:
            response_parts.append(f"✅ Rôle staff configuré : {role.mention}")
    if category_name:
        response_parts.append(f"✅ Catégorie des tickets : `{category_name}`")

    await interaction.response.send_message("\n".join(response_parts), ephemeral=True)

# ----- Vérification automatique des messages de tickets (toutes les heures) -----
@tasks.loop(hours=1)
async def check_ticket_messages():
    """Vérifier si les messages avec boutons de tickets existent encore"""
    global ticket_messages
    ticket_messages_copy = ticket_messages.copy()
    
    for guild_id, messages in ticket_messages_copy.items():
        guild = bot.get_guild(guild_id)
        if not guild:
            continue
            
        messages_copy = messages.copy()
        for msg_id, channel_id in messages_copy.items():
            channel = guild.get_channel(channel_id)
            if not channel:
                # Le salon n'existe plus
                del ticket_messages[guild_id][msg_id]
                remove_ticket_message(guild_id, msg_id)
                print(f"Message de ticket {msg_id} supprimé du JSON car le salon {channel_id} n'existe plus dans {guild.name}")
                continue
                
            try:
                await channel.fetch_message(msg_id)
            except discord.NotFound:
                # Le message n'existe plus
                del ticket_messages[guild_id][msg_id]
                remove_ticket_message(guild_id, msg_id)
                print(f"Message de ticket {msg_id} supprimé du JSON car le message n'existe plus dans {guild.name}")
            except Exception as e:
                print(f"Erreur lors de la vérification du message {msg_id}: {e}")
        
        # Nettoyer les entrées vides
        if not ticket_messages[guild_id]:
            del ticket_messages[guild_id]

# ----- Vérification automatique des tickets ouverts -----
@tasks.loop(minutes=1)
async def check_tickets():
    global open_tickets
    open_tickets_copy = open_tickets.copy()
    
    for key, ticket_data in open_tickets_copy.items():
        channel_id = ticket_data["ticket_channel_id"]
        guild_id = ticket_data["guild_id"]
        user_id = ticket_data["user_id"]
        
        guild = bot.get_guild(guild_id)
        if not guild or not guild.get_channel(channel_id):
            remove_open_ticket(user_id, guild_id)
            del open_tickets[key]
            print(f"Ticket {key} supprimé du JSON car le salon n'existe plus.")

# ----- Update status -----
@tasks.loop(minutes=5)
async def update_status():
    global status_message_id
    # Utiliser le premier serveur qui a un status_channel_id configuré
    servers_config = load_servers_config()
    status_channel_id = None
    
    for guild_id, config in servers_config.items():
        if config.get("status_channel_id"):
            status_channel_id = config["status_channel_id"]
            break
    
    if not status_channel_id:
        return
        
    channel = bot.get_channel(status_channel_id)
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

# ----- on_ready -----
@bot.event
async def on_ready():
    global status_message_id, ticket_messages, open_tickets, close_button_messages
    await tree.sync()
    print(f"[Manager] Connecté en tant que {bot.user}")

    # Recharger toutes les données
    ticket_messages = load_ticket_messages()
    open_tickets = load_open_tickets()
    close_button_messages = load_close_button_messages()

    # Restaurer les boutons des messages de ticket pour tous les serveurs
    for guild_id, messages in ticket_messages.items():
        guild = bot.get_guild(guild_id)
        if not guild:
            continue
            
        for msg_id, channel_id in messages.items():
            channel = guild.get_channel(channel_id)
            if channel:
                try:
                    msg = await channel.fetch_message(msg_id)
                    await msg.edit(view=TicketButton())
                    print(f"Bouton de ticket restauré pour le message {msg_id} dans {guild.name}")
                except Exception as e:
                    print(f"Erreur lors de la restauration du bouton de ticket {msg_id} dans {guild.name}: {e}")
    
    # Restaurer les boutons de fermeture des tickets
    for msg_id, data in close_button_messages.items():
        channel_id = data["channel_id"]
        guild_id = data["guild_id"]
        guild = bot.get_guild(guild_id)
        if guild:
            channel = guild.get_channel(channel_id)
            if channel:
                try:
                    msg = await channel.fetch_message(msg_id)
                    await msg.edit(view=CloseTicketButton())
                    print(f"Bouton de fermeture restauré pour le message {msg_id} dans {guild.name}")
                except Exception as e:
                    print(f"Erreur lors de la restauration du bouton de fermeture {msg_id}: {e}")

    # Gérer le message de status
    status_message_id = load_status_message()
    servers_config = load_servers_config()
    for guild_id, config in servers_config.items():
        status_channel_id = config.get("status_channel_id")
        if status_channel_id:
            channel = bot.get_channel(status_channel_id)
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
                break

    # Démarrer les tâches
    update_status.start()
    check_tickets.start()
    check_ticket_messages.start()
    
    print(f"Bot prêt ! Configuré sur {len(ticket_messages)} serveur(s) avec des messages de tickets.")

# ----- Lancement du bot -----
if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN_TICKET")
    if not token:
        print("❌ DISCORD_TOKEN_TICKET manquant dans les variables d'environnement")
        exit(1)
    bot.run(token)