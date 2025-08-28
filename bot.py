import os
import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
import json
import time
import asyncpg
from typing import Optional, Dict, Any

# ---------------------------------
# ----- Configuration PostgreSQL -----
DATABASE_URL = os.getenv("DATABASE_URL")

# Pool de connexions PostgreSQL
db_pool = None

async def init_database():
    """Initialiser la base de données PostgreSQL et créer les tables"""
    global db_pool
    
    if not DATABASE_URL:
        raise ValueError("❌ DATABASE_URL manquant dans les variables d'environnement")
    
    # Créer le pool de connexions
    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    
    # Créer les tables si elles n'existent pas
    async with db_pool.acquire() as conn:
        # Table pour la configuration des serveurs
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS servers_config (
                guild_id BIGINT PRIMARY KEY,
                category_name VARCHAR(255) DEFAULT 'TICKETS',
                staff_role_id BIGINT,
                ticket_message TEXT DEFAULT '{user} Merci d''avoir ouvert un ticket. Un membre du staff va te répondre.',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Table pour les messages avec boutons de tickets
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS ticket_messages (
                message_id BIGINT,
                guild_id BIGINT,
                channel_id BIGINT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (message_id, guild_id)
            )
        ''')
        
        # Table pour les tickets ouverts
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS open_tickets (
                user_id BIGINT,
                guild_id BIGINT,
                ticket_channel_id BIGINT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, guild_id)
            )
        ''')
        
        # Table pour les messages de fermeture
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS close_button_messages (
                message_id BIGINT PRIMARY KEY,
                channel_id BIGINT,
                guild_id BIGINT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    
    print("✅ Base de données PostgreSQL initialisée")

# ----- Fonctions de gestion de la configuration des serveurs -----
async def get_server_config(guild_id: int) -> Dict[str, Any]:
    """Obtenir la configuration d'un serveur spécifique"""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM servers_config WHERE guild_id = $1", guild_id
        )
        
        if row:
            return {
                "category_name": row["category_name"],
                "staff_role_id": row["staff_role_id"],
                "ticket_message": row["ticket_message"]
            }
        else:
            # Créer la configuration par défaut
            default_config = {
                "category_name": "TICKETS",
                "staff_role_id": None,
                "ticket_message": "{user} Merci d'avoir ouvert un ticket. Un membre du staff va te répondre."
            }
            
            await conn.execute('''
                INSERT INTO servers_config (guild_id, category_name, staff_role_id, ticket_message)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (guild_id) DO NOTHING
            ''', guild_id, default_config["category_name"], default_config["staff_role_id"], 
                default_config["ticket_message"])
            
            return default_config

async def update_server_config(guild_id: int, updates: Dict[str, Any]):
    """Mettre à jour la configuration d'un serveur"""
    async with db_pool.acquire() as conn:
        # Vérifier si la configuration existe
        exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM servers_config WHERE guild_id = $1)", guild_id
        )
        
        if not exists:
            # Créer la configuration par défaut
            await conn.execute('''
                INSERT INTO servers_config (guild_id, category_name, staff_role_id, ticket_message)
                VALUES ($1, 'TICKETS', NULL, $2)
            ''', guild_id, '{user} Merci d\'avoir ouvert un ticket. Un membre du staff va te répondre.')
        
        # Mettre à jour les champs spécifiés
        for key, value in updates.items():
            if key in ["category_name", "staff_role_id", "ticket_message"]:
                await conn.execute(f'''
                    UPDATE servers_config 
                    SET {key} = $1 
                    WHERE guild_id = $2
                ''', value, guild_id)

# ----- Fonctions de gestion des messages de tickets -----
async def add_ticket_message(guild_id: int, message_id: int, channel_id: int):
    """Ajouter un message de ticket pour un serveur"""
    async with db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO ticket_messages (message_id, guild_id, channel_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (message_id, guild_id) DO UPDATE SET channel_id = $3
        ''', message_id, guild_id, channel_id)

async def remove_ticket_message(guild_id: int, message_id: int):
    """Supprimer un message de ticket pour un serveur"""
    async with db_pool.acquire() as conn:
        await conn.execute('''
            DELETE FROM ticket_messages 
            WHERE guild_id = $1 AND message_id = $2
        ''', guild_id, message_id)

async def load_ticket_messages() -> Dict[int, Dict[int, int]]:
    """Charger tous les messages de tickets"""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM ticket_messages")
        
        result = {}
        for row in rows:
            guild_id = row["guild_id"]
            message_id = row["message_id"]
            channel_id = row["channel_id"]
            
            if guild_id not in result:
                result[guild_id] = {}
            result[guild_id][message_id] = channel_id
        
        return result

# ----- Fonctions de gestion des tickets ouverts -----
async def save_open_ticket(user_id: int, channel_id: int, guild_id: int):
    """Sauvegarder un ticket ouvert"""
    async with db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO open_tickets (user_id, guild_id, ticket_channel_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id, guild_id) 
            DO UPDATE SET ticket_channel_id = $3, created_at = CURRENT_TIMESTAMP
        ''', user_id, guild_id, channel_id)
    
    print(f"Ticket sauvegardé: utilisateur {user_id} sur serveur {guild_id} -> salon {channel_id}")

async def remove_open_ticket(user_id: int, guild_id: int):
    """Supprimer un ticket ouvert"""
    async with db_pool.acquire() as conn:
        result = await conn.execute('''
            DELETE FROM open_tickets 
            WHERE user_id = $1 AND guild_id = $2
        ''', user_id, guild_id)
        
        if result == "DELETE 1":
            print(f"Ticket supprimé de la DB: utilisateur {user_id} sur serveur {guild_id}")
        else:
            print(f"Ticket non trouvé dans la DB: utilisateur {user_id} sur serveur {guild_id}")

async def user_has_open_ticket(user_id: int, guild_id: int) -> bool:
    """Vérifier si l'utilisateur a déjà un ticket ouvert sur ce serveur"""
    async with db_pool.acquire() as conn:
        return await conn.fetchval('''
            SELECT EXISTS(SELECT 1 FROM open_tickets WHERE user_id = $1 AND guild_id = $2)
        ''', user_id, guild_id)

async def get_user_open_ticket(user_id: int, guild_id: int) -> Optional[int]:
    """Obtenir le channel ID du ticket ouvert de l'utilisateur sur ce serveur"""
    async with db_pool.acquire() as conn:
        return await conn.fetchval('''
            SELECT ticket_channel_id FROM open_tickets 
            WHERE user_id = $1 AND guild_id = $2
        ''', user_id, guild_id)

async def load_open_tickets() -> Dict[str, Dict[str, Any]]:
    """Charger tous les tickets ouverts"""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM open_tickets")
        
        result = {}
        for row in rows:
            key = f"{row['user_id']}_{row['guild_id']}"
            result[key] = {
                "user_id": row["user_id"],
                "guild_id": row["guild_id"],
                "ticket_channel_id": row["ticket_channel_id"],
                "created_at": int(row["created_at"].timestamp())
            }
        
        return result

# ----- Fonctions de gestion des boutons de fermeture -----
async def save_close_button_message(message_id: int, channel_id: int, guild_id: int):
    """Sauvegarder un message avec bouton de fermeture"""
    async with db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO close_button_messages (message_id, channel_id, guild_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (message_id) 
            DO UPDATE SET channel_id = $2, guild_id = $3
        ''', message_id, channel_id, guild_id)

async def load_close_button_messages() -> Dict[int, Dict[str, int]]:
    """Charger tous les messages avec boutons de fermeture"""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM close_button_messages")
        
        result = {}
        for row in rows:
            result[row["message_id"]] = {
                "channel_id": row["channel_id"],
                "guild_id": row["guild_id"]
            }
        
        return result

async def remove_close_button_message(message_id: int):
    """Supprimer un message avec bouton de fermeture"""
    async with db_pool.acquire() as conn:
        await conn.execute('''
            DELETE FROM close_button_messages WHERE message_id = $1
        ''', message_id)

# ---------------------------------
# ----- Bot Discord -----
intents = discord.Intents.default()
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# Variables globales
ticket_messages = {}
open_tickets = {}
close_button_messages = {}

# ----- Fonction de nettoyage immédiat -----
async def force_clean_guild_tickets(guild_id: int):
    """Nettoyer immédiatement les tickets inexistants pour un serveur"""
    global open_tickets
    guild = bot.get_guild(guild_id)
    if not guild:
        return
    
    to_remove = []
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM open_tickets WHERE guild_id = $1", guild_id)
        
        for row in rows:
            channel_id = row["ticket_channel_id"]
            user_id = row["user_id"]
            channel = guild.get_channel(channel_id)
            
            if not channel:
                await remove_open_ticket(user_id, guild_id)
                key = f"{user_id}_{guild_id}"
                to_remove.append(key)
                print(f"Nettoyage immédiat: ticket {key} supprimé")
    
    for key in to_remove:
        open_tickets.pop(key, None)
    
    print(f"Nettoyage immédiat terminé pour le serveur {guild.name}: {len(to_remove)} ticket(s) nettoyé(s)")

# ----- Vue bouton ticket -----
class TicketButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Ouvrir un ticket", style=discord.ButtonStyle.green)
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = interaction.user.id
        guild_id = interaction.guild.id
        
        # Vérifier si l'utilisateur a déjà un ticket ouvert sur ce serveur
        if await user_has_open_ticket(user_id, guild_id):
            existing_channel_id = await get_user_open_ticket(user_id, guild_id)
            print(f"Tentative d'ouverture de ticket bloquée: utilisateur {user_id} a déjà le ticket {existing_channel_id} sur serveur {guild_id}")
            await interaction.response.send_message(
                f"❌ Tu as déjà un ticket ouvert <#{existing_channel_id}> sur ce serveur ! Ferme ton ticket actuel avant d'en créer un nouveau.", 
                ephemeral=True
            )
            return

        print(f"Création de ticket autorisée pour utilisateur {user_id} sur serveur {guild_id}")
        channel = await create_ticket(interaction.user, interaction.guild)
        await save_open_ticket(user_id, channel.id, guild_id)
        print(f"Ticket créé avec succès: salon {channel.id} pour utilisateur {user_id}")
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
        server_config = await get_server_config(guild.id)
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
        
        # Sauvegarder les IDs avant suppression
        channel_id_to_remove = interaction.channel.id
        guild_id_to_clean = interaction.guild.id
        
        await asyncio.sleep(5)

        # Supprimer de la liste des tickets ouverts
        global open_tickets
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT user_id, guild_id FROM open_tickets 
                WHERE ticket_channel_id = $1
            ''', channel_id_to_remove)
            
            if row:
                user_id = row["user_id"]
                guild_id = row["guild_id"]
                await remove_open_ticket(user_id, guild_id)
                key = f"{user_id}_{guild_id}"
                open_tickets.pop(key, None)
                print(f"Ticket fermé: utilisateur {user_id} sur serveur {guild_id}")

        # Supprimer le message du JSON des boutons de fermeture  
        for msg_id, data in list(close_button_messages.items()):
            if data["channel_id"] == channel_id_to_remove:
                await remove_close_button_message(msg_id)
                close_button_messages.pop(int(msg_id), None)
                print(f"Bouton de fermeture supprimé pour le message {msg_id}")
                break

        # Vérifier que le channel existe encore avant de le supprimer
        try:
            channel_to_delete = bot.get_channel(channel_id_to_remove)
            if channel_to_delete:
                await channel_to_delete.delete(reason="Ticket fermé par le staff")
                print(f"Salon {channel_id_to_remove} supprimé avec succès")
            else:
                print(f"Salon {channel_id_to_remove} déjà supprimé ou introuvable")
        except discord.NotFound:
            print(f"Channel {channel_id_to_remove} déjà supprimé")
        except Exception as e:
            print(f"Erreur lors de la suppression du channel: {e}")

# ----- Création ticket -----
async def create_ticket(user, guild):
    server_config = await get_server_config(guild.id)
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
    await save_close_button_message(msg.id, channel.id, guild.id)
    
    return channel

# ----- /help commande -----
@tree.command(description="Afficher l'aide pour configurer le système de tickets")
async def help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎫 Guide de configuration du bot Tickets",
        description="Voici comment configurer le système de tickets sur votre serveur :",
        color=0x00ff00
    )
    
    embed.add_field(
        name="📋 Commande principale",
        value="`/config`",
        inline=False
    )
    
    embed.add_field(
        name="🔧 Paramètres de /config",
        value="""
**channel_id** (obligatoire)
• L'ID du salon où poster le message avec bouton
• Exemple: `123456789012345678`

**message_text** (obligatoire)  
• Le texte du message qui contiendra le bouton
• Exemple: `"Cliquez ici pour ouvrir un ticket"`

**ticket_message** (obligatoire)
• Message envoyé dans le nouveau ticket
• Utilisez `{user}` pour mentionner l'utilisateur
• Exemple: `"{user} Bonjour ! Un staff va vous répondre."`

**staff_role_id** (optionnel)
• ID du rôle qui peut fermer les tickets
• Exemple: `987654321098765432`

**category_name** (optionnel)
• Nom de la catégorie pour les tickets
• Par défaut: `TICKETS`
        """,
        inline=False
    )
    
    embed.add_field(
        name="💡 Exemple complet",
        value="""
```
/config 
channel_id:123456789012345678 
message_text:"Cliquez pour ouvrir un ticket" 
ticket_message:"{user} Merci d'avoir ouvert un ticket !" 
staff_role_id:987654321098765432
category_name:"SUPPORT"
```
        """,
        inline=False
    )
    
    embed.add_field(
        name="🔍 Comment obtenir les IDs",
        value="""
1. Activez le mode développeur Discord
2. Clic droit sur le salon/rôle → "Copier l'ID"
3. Collez l'ID dans la commande
        """,
        inline=False
    )
    
    embed.set_footer(text="Seuls les administrateurs peuvent utiliser /config")
    
    await interaction.response.send_message(embed=embed, ephemeral=False)

@tree.command(description="[ADMIN] Configurer le système de tickets pour ce serveur")
@app_commands.describe(
    channel_id="ID du salon où poster le message avec bouton",
    message_text="Texte du message avec bouton",
    ticket_message="Message envoyé dans le ticket (utilise {user} pour mentionner l'utilisateur) - OBLIGATOIRE",
    staff_role_id="ID du rôle staff (optionnel)",
    category_name="Nom de la catégorie des tickets (optionnel)"
)
async def config(interaction: discord.Interaction, channel_id: str, message_text: str, 
                ticket_message: str, staff_role_id: str = None, category_name: str = None):
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
    # Le ticket_message est maintenant obligatoire
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
        await update_server_config(guild.id, updates)

    # Créer le message avec bouton
    msg = await channel.send(message_text, view=TicketButton())
    
    # Sauvegarder le message de ticket
    global ticket_messages
    if guild.id not in ticket_messages:
        ticket_messages[guild.id] = {}
    ticket_messages[guild.id][msg.id] = channel.id
    await add_ticket_message(guild.id, msg.id, channel.id)
    
    response_parts = [f"✅ Message configuré dans {channel.mention} avec bouton 'Ouvrir un ticket'."]
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
    
    # Charger les messages depuis la DB
    ticket_messages = await load_ticket_messages()
    
    for guild_id, messages in ticket_messages.items():
        guild = bot.get_guild(guild_id)
        if not guild:
            continue
            
        messages_copy = messages.copy()
        for msg_id, channel_id in messages_copy.items():
            channel = guild.get_channel(channel_id)
            if not channel:
                # Le salon n'existe plus
                await remove_ticket_message(guild_id, msg_id)
                del ticket_messages[guild_id][msg_id]
                print(f"Message de ticket {msg_id} supprimé de la DB car le salon {channel_id} n'existe plus dans {guild.name}")
                continue
                
            try:
                await channel.fetch_message(msg_id)
            except discord.NotFound:
                # Le message n'existe plus
                await remove_ticket_message(guild_id, msg_id)
                del ticket_messages[guild_id][msg_id]
                print(f"Message de ticket {msg_id} supprimé de la DB car le message n'existe plus dans {guild.name}")
            except Exception as e:
                print(f"Erreur lors de la vérification du message {msg_id}: {e}")
        
        # Nettoyer les entrées vides
        if guild_id in ticket_messages and not ticket_messages[guild_id]:
            del ticket_messages[guild_id]

# ----- Vérification automatique des tickets ouverts -----
@tasks.loop(minutes=2)
async def check_tickets():
    """Vérifier toutes les 2 minutes si les tickets ouverts existent encore"""
    global open_tickets
    
    # Charger les tickets depuis la DB
    open_tickets = await load_open_tickets()
    
    for key, ticket_data in open_tickets.copy().items():
        channel_id = ticket_data["ticket_channel_id"]
        guild_id = ticket_data["guild_id"]
        user_id = ticket_data["user_id"]
        
        guild = bot.get_guild(guild_id)
        if not guild:
            # Le serveur n'existe plus ou le bot n'y est plus
            await remove_open_ticket(user_id, guild_id)
            del open_tickets[key]
            print(f"Ticket {key} supprimé de la DB car le serveur {guild_id} n'est plus accessible.")
            continue
            
        channel = guild.get_channel(channel_id)
        if not channel:
            # Le salon n'existe plus
            await remove_open_ticket(user_id, guild_id)
            del open_tickets[key]
            print(f"Ticket {key} supprimé de la DB car le salon {channel_id} n'existe plus dans {guild.name}.")
            continue

# ----- on_ready -----
@bot.event
async def on_ready():
    global status_messages, ticket_messages, open_tickets, close_button_messages
    
    # Initialiser la base de données
    await init_database()
    
    await tree.sync()
    print(f"[Manager] Connecté en tant que {bot.user}")

    # Recharger toutes les données depuis la DB
    ticket_messages = await load_ticket_messages()
    open_tickets = await load_open_tickets()
    close_button_messages = await load_close_button_messages()
    status_messages = await load_status_messages()

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

    # Initialiser les messages de status pour les serveurs configurés
    async with db_pool.acquire() as conn:
        rows = await conn.fetch('''
            SELECT guild_id, status_channel_id FROM servers_config 
            WHERE status_channel_id IS NOT NULL
        ''')
        
        current_time = int(discord.utils.utcnow().timestamp())
        
        for row in rows:
            guild_id = row["guild_id"]
            status_channel_id = row["status_channel_id"]
            
            guild = bot.get_guild(guild_id)
            if not guild:
                print(f"Serveur {guild_id} non accessible au démarrage")
                continue
                
            channel = guild.get_channel(status_channel_id)
            if not channel:
                print(f"Salon de status {status_channel_id} non trouvé dans {guild.name}")
                continue
            
            message_created = False
            
            # Vérifier si on a déjà un message de status en DB
            if guild_id in status_messages:
                message_id = status_messages[guild_id]["message_id"]
                try:
                    msg = await channel.fetch_message(message_id)
                    await msg.edit(content=f"✅ Bot en ligne (redémarré) - <t:{current_time}:R>")
                    message_created = True
                    print(f"Message de status restauré pour {guild.name}")
                except discord.NotFound:
                    print(f"Message de status {message_id} non trouvé dans {guild.name}, création d'un nouveau")
                    # Le message n'existe plus, supprimer de la mémoire
                    status_messages.pop(guild_id, None)
                except Exception as e:
                    print(f"Erreur lors de la restauration du status pour {guild.name}: {e}")
            
            # Si aucun message existant ou restauration échouée, créer un nouveau
            if not message_created:
                try:
                    msg = await channel.send(f"✅ Bot en ligne (redémarré) - <t:{current_time}:R>")
                    await save_status_message(guild_id, msg.id, channel.id)
                    status_messages[guild_id] = {"message_id": msg.id, "channel_id": channel.id}
                    print(f"Nouveau message de status créé au démarrage pour {guild.name}")
                except discord.Forbidden:
                    print(f"Pas de permission pour envoyer un message dans le salon de status de {guild.name}")
                except Exception as e:
                    print(f"Erreur lors de la création du message de status pour {guild.name}: {e}")

    # Démarrer les tâches
    update_status.start()
    check_tickets.start()
    check_ticket_messages.start()
    
    print(f"Bot prêt ! Configuré sur {len(ticket_messages)} serveur(s) avec des messages de tickets.")
    print(f"Tickets ouverts actuellement: {len(open_tickets)}")
    print(f"Messages de status configurés: {len(status_messages)}")
    
    # Afficher un résumé des serveurs configurés
    for guild_id, messages in ticket_messages.items():
        guild = bot.get_guild(guild_id)
        guild_name = guild.name if guild else f"Serveur {guild_id} (inaccessible)"
        tickets_count = len(messages)
        print(f"  - {guild_name}: {tickets_count} message(s) de tickets configuré(s)")

# ----- Gestion propre de la fermeture -----
async def cleanup_on_exit():
    """Fermer proprement la connexion à la base de données"""
    global db_pool
    if db_pool:
        await db_pool.close()
        print("🔌 Connexion PostgreSQL fermée")

# ----- Keep alive pour Render -----
import threading
from flask import Flask

app = Flask("")

@app.route("/")
def home():
    return "Bot en ligne !"

def run_flask():
    app.run(host="0.0.0.0", port=10000)

threading.Thread(target=run_flask).start()

# ----- Lancement du bot -----
if __name__ == "__main__":
    import signal
    
    def signal_handler(sig, frame):
        print("\n🛑 Arrêt du bot demandé...")
        import asyncio
        asyncio.create_task(cleanup_on_exit())
        exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("❌ DISCORD_TOKEN manquant dans les variables d'environnement")
        exit(1)
    
    try:
        bot.run(token)
    except KeyboardInterrupt:
        print("\n🛑 Bot arrêté par l'utilisateur")
    finally:
        import asyncio
        asyncio.run(cleanup_on_exit())