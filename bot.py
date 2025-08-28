import os
import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio
import json
import time
import psycopg2
import psycopg2.extras
from typing import Optional, Dict, Any

DATABASE_URL = os.getenv("DATABASE_URL")

# ----- Connexion PostgreSQL (synchrone) -----
conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.DictCursor)
cursor = conn.cursor()

# ----- Bot Discord -----
intents = discord.Intents.default()
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ----- Fonction utilitaire pour exécuter DB en thread -----
async def db_execute(query, *args):
    def run_query():
        cursor.execute(query, args)
        if query.strip().upper().startswith("SELECT"):
            return cursor.fetchall()
        else:
            conn.commit()
            return None
    return await asyncio.to_thread(run_query)

# ---------------------------------
# ----- Configuration PostgreSQL -----
DATABASE_URL = os.getenv("DATABASE_URL")

# Connexion PostgreSQL
conn = None
cursor = None

def init_database():
    """Initialiser la base de données PostgreSQL et créer les tables"""
    global conn, cursor
    
    if not DATABASE_URL:
        raise ValueError("❌ DATABASE_URL manquant dans les variables d'environnement")
    
    # Connexion
    conn = psycopg2.connect(DATABASE_URL, sslmode="require")
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    # Table pour la configuration des serveurs
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS servers_config (
            guild_id BIGINT PRIMARY KEY,
            category_name VARCHAR(255) DEFAULT 'TICKETS',
            staff_role_id BIGINT,
            ticket_message TEXT DEFAULT '{user} Merci d''avoir ouvert un ticket. Un membre du staff va te répondre.',
            status_channel_id BIGINT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Table pour les messages avec boutons de tickets
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ticket_messages (
            message_id BIGINT,
            guild_id BIGINT,
            channel_id BIGINT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (message_id, guild_id)
        )
    ''')
    
    # Table pour les tickets ouverts
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS open_tickets (
            user_id BIGINT,
            guild_id BIGINT,
            ticket_channel_id BIGINT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, guild_id)
        )
    ''')
    
    # Table pour les messages de fermeture
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS close_button_messages (
            message_id BIGINT PRIMARY KEY,
            channel_id BIGINT,
            guild_id BIGINT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Table pour le message de status
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS status_message (
            id INTEGER PRIMARY KEY DEFAULT 1,
            message_id BIGINT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Valider les changements
    conn.commit()
    
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
                "ticket_message": row["ticket_message"],
                "status_channel_id": row["status_channel_id"]
            }
        else:
            # Créer la configuration par défaut
            default_config = {
                "category_name": "TICKETS",
                "staff_role_id": None,
                "ticket_message": "{user} Merci d'avoir ouvert un ticket. Un membre du staff va te répondre.",
                "status_channel_id": None
            }
            
            await conn.execute('''
                INSERT INTO servers_config (guild_id, category_name, staff_role_id, ticket_message, status_channel_id)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (guild_id) DO NOTHING
            ''', guild_id, default_config["category_name"], default_config["staff_role_id"], 
                default_config["ticket_message"], default_config["status_channel_id"])
            
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
                INSERT INTO servers_config (guild_id, category_name, staff_role_id, ticket_message, status_channel_id)
                VALUES ($1, 'TICKETS', NULL, $2, NULL)
            ''', guild_id, '{user} Merci d\'avoir ouvert un ticket. Un membre du staff va te répondre.')
        
        # Mettre à jour les champs spécifiés
        for key, value in updates.items():
            if key in ["category_name", "staff_role_id", "ticket_message", "status_channel_id"]:
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

# ----- Fonctions de gestion du status -----
async def save_status_message(message_id: int):
    """Sauvegarder l'ID du message de status"""
    async with db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO status_message (id, message_id)
            VALUES (1, $1)
            ON CONFLICT (id) 
            DO UPDATE SET message_id = $1, updated_at = CURRENT_TIMESTAMP
        ''', message_id)

async def load_status_message() -> Optional[int]:
    """Charger l'ID du message de status"""
    async with db_pool.acquire() as conn:
        return await conn.fetchval("SELECT message_id FROM status_message WHERE id = 1")

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
status_message_id = None

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

# ----- /ticket commande -----
@tree.command(description="Créer un ticket privé avec le staff")
async def ticket(interaction: discord.Interaction):
    user_id = interaction.user.id
    guild_id = interaction.guild.id
    
    # Vérifier si l'utilisateur a déjà un ticket ouvert sur ce serveur
    if await user_has_open_ticket(user_id, guild_id):
        existing_channel_id = await get_user_open_ticket(user_id, guild_id)
        print(f"Commande /ticket bloquée: utilisateur {user_id} a déjà le ticket {existing_channel_id} sur serveur {guild_id}")
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

    print(f"Commande /ticket autorisée pour utilisateur {user_id} sur serveur {guild_id}")
    channel = await create_ticket(interaction.user, guild)
    await save_open_ticket(user_id, channel.id, guild_id)
    print(f"Ticket créé via commande: salon {channel.id} pour utilisateur {user_id}")
    
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

    server_config = await get_server_config(guild.id)
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
            print(f"Ticket fermé via commande: utilisateur {user_id} sur serveur {guild_id}")

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
    
    # Forcer un nettoyage immédiat pour ce serveur
    await asyncio.sleep(1)  # Petite pause pour que Discord traite la suppression
    await force_clean_guild_tickets(guild_id_to_clean)

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
    
    if ticket_message:
        response_parts.append(f"✅ Message d'accueil du ticket : `{ticket_message}`")
    if staff_role_id:
        role = guild.get_role(int(staff_role_id))
        if role:
            response_parts.append(f"✅ Rôle staff configuré : {role.mention}")
    if category_name:
        response_parts.append(f"✅ Catégorie des tickets : `{category_name}`")

    await interaction.response.send_message("\n".join(response_parts), ephemeral=True)

# ----- /debug_tickets pour vérifier l'état -----
@tree.command(description="[ADMIN] Vérifier l'état des tickets et nettoyer si nécessaire")
async def debug_tickets(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(
            "❌ Seuls les administrateurs peuvent utiliser cette commande.", ephemeral=True
        )
    
    guild_id = interaction.guild.id
    user_id = interaction.user.id
    
    # Vérifier les tickets ouverts pour ce serveur
    open_tickets_for_guild = []
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM open_tickets WHERE guild_id = $1", guild_id)
        
        for row in rows:
            channel_id = row["ticket_channel_id"]
            channel = interaction.guild.get_channel(channel_id)
            status = "✅ Existe" if channel else "❌ N'existe plus"
            open_tickets_for_guild.append(f"- <@{row['user_id']}>: <#{channel_id}> {status}")
    
    # Vérifier si l'utilisateur actuel a un ticket
    user_ticket = await user_has_open_ticket(user_id, guild_id)
    user_ticket_channel = await get_user_open_ticket(user_id, guild_id)
    
    embed = discord.Embed(title="🔍 Debug Tickets", color=0x00ff00)
    embed.add_field(
        name="📊 Tickets ouverts sur ce serveur", 
        value="\n".join(open_tickets_for_guild) if open_tickets_for_guild else "Aucun ticket ouvert", 
        inline=False
    )
    embed.add_field(
        name="👤 Votre statut", 
        value=f"Ticket ouvert: {'✅ Oui' if user_ticket else '❌ Non'}\nSalon: {f'<#{user_ticket_channel}>' if user_ticket_channel else 'Aucun'}", 
        inline=False
    )
    embed.add_field(
        name="🧹 Actions", 
        value="Utilise `/force_clean_tickets` si tu vois des tickets qui n'existent plus", 
        inline=False
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

# ----- /force_clean_tickets pour forcer le nettoyage -----
@tree.command(description="[ADMIN] Forcer le nettoyage des tickets inexistants")
async def force_clean_tickets(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message(
            "❌ Seuls les administrateurs peuvent utiliser cette commande.", ephemeral=True
        )
    
    await interaction.response.defer(ephemeral=True)
    
    guild_id = interaction.guild.id
    cleaned_count = 0
    
    # Nettoyer les tickets de ce serveur
    global open_tickets
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM open_tickets WHERE guild_id = $1", guild_id)
        
        for row in rows:
            channel_id = row["ticket_channel_id"]
            user_id = row["user_id"]
            channel = interaction.guild.get_channel(channel_id)
            
            if not channel:
                await remove_open_ticket(user_id, guild_id)
                key = f"{user_id}_{guild_id}"
                open_tickets.pop(key, None)
                cleaned_count += 1
                print(f"Nettoyage forcé: ticket {key} supprimé")
    
    await interaction.followup.send(
        f"🧹 Nettoyage terminé ! **{cleaned_count}** ticket(s) inexistant(s) supprimé(s).", 
        ephemeral=True
    )

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

# ----- Update status -----
@tasks.loop(minutes=5)
async def update_status():
    global status_message_id
    
    # Trouver le premier serveur qui a un status_channel_id configuré
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow('''
            SELECT status_channel_id FROM servers_config 
            WHERE status_channel_id IS NOT NULL 
            LIMIT 1
        ''')
        
        if not row:
            return
        
        status_channel_id = row["status_channel_id"]
        
    channel = bot.get_channel(status_channel_id)
    if not channel:
        return
        
    if not status_message_id:
        msg = await channel.send("✅ Bot en ligne")
        status_message_id = msg.id
        await save_status_message(status_message_id)
    else:
        try:
            msg = await channel.fetch_message(status_message_id)
            await msg.edit(
                content=f"✅ Bot toujours en ligne (maj <t:{int(discord.utils.utcnow().timestamp())}:R>)"
            )
        except:
            msg = await channel.send("✅ Bot en ligne")
            status_message_id = msg.id
            await save_status_message(status_message_id)

# ----- on_ready -----
@bot.event
async def on_ready():
    global status_message_id, ticket_messages, open_tickets, close_button_messages
    
    # Initialiser la base de données
    await init_database()
    
    await tree.sync()
    print(f"[Manager] Connecté en tant que {bot.user}")

    # Recharger toutes les données depuis la DB
    ticket_messages = await load_ticket_messages()
    open_tickets = await load_open_tickets()
    close_button_messages = await load_close_button_messages()

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
        channel_id = data.get("channel_id")
        guild_id = data.get("guild_id")
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
    status_message_id = await load_status_message()
    
    if status_message_id is None:
        status_message_id = 0

    row = await db_execute('''
        SELECT status_channel_id FROM servers_config 
        WHERE status_channel_id IS NOT NULL 
        LIMIT 1
    ''')
    
    if row:
        status_channel_id = row[0]["status_channel_id"]
        channel = bot.get_channel(status_channel_id)
        if channel:
            try:
                if status_message_id:
                    msg = await channel.fetch_message(status_message_id)
                    await msg.edit(content="✅ Bot en ligne (redémarré)")
                else:
                    msg = await channel.send("✅ Bot en ligne")
                    status_message_id = msg.id
                    await save_status_message(status_message_id)
            except Exception as e:
                print(f"Erreur lors de l'envoi du message de status: {e}")

    # Démarrer les tâches
    update_status.start()
    check_tickets.start()
    check_ticket_messages.start()
    
    print(f"Bot prêt ! Configuré sur {len(ticket_messages)} serveur(s) avec des messages de tickets.")
    print(f"Tickets ouverts actuellement: {len(open_tickets)}")
    
    # Afficher un résumé des serveurs configurés
    for guild_id, messages in ticket_messages.items():
        guild = bot.get_guild(guild_id)
        guild_name = guild.name if guild else f"Serveur {guild_id} (inaccessible)"
        tickets_count = len(messages)
        print(f"  - {guild_name}: {tickets_count} message(s) de tickets configuré(s)")

# ----- Gestion propre de la fermeture -----
async def cleanup_on_exit():
    """Fermer proprement la connexion à la base de données"""
    global conn
    if conn:
        conn.close()
        print("🔌 Connexion PostgreSQL fermée")

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
    
    token = os.getenv("DISCORD_TOKEN_TICKET")
    if not token:
        print("❌ DISCORD_TOKEN_TICKET manquant dans les variables d'environnement")
        exit(1)
    
    try:
        bot.run(token)
    except KeyboardInterrupt:
        print("\n🛑 Bot arrêté par l'utilisateur")
    finally:
        import asyncio
        asyncio.run(cleanup_on_exit())
