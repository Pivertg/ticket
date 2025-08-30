import discord
from discord.ext import commands, tasks
import redis
import json
import os
from datetime import datetime, timedelta
import asyncio
from typing import Dict, List, Optional
import threading
import time

# ---------- Configuration Redis ----------
redis_client = redis.Redis(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", 6379)),
    password=os.getenv("REDIS_PASSWORD"),
    decode_responses=True,
    socket_keepalive=True,
    socket_keepalive_options={},
    health_check_interval=30
)

# ---------- Configuration des serveurs/rôles ----------
MAIN_GUILD_ID = 1408449935870791841  # Serveur principal
REQUIRED_ROLE_ID = 1410986350877868042  # Rôle requis
STATUS_CHANNEL_ID = 1408498176003932422  # Channel de statut

class RedisManager:
    """Gestionnaire Redis avec retry automatique"""
    
    @staticmethod
    def safe_operation(operation, *args, **kwargs):
        """Wrapper pour les opérations Redis avec retry"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                return operation(*args, **kwargs)
            except redis.ConnectionError as e:
                if attempt == max_retries - 1:
                    print(f"Redis connexion échouée après {max_retries} tentatives: {e}")
                    return None
                time.sleep(2 ** attempt)
            except Exception as e:
                print(f"Erreur Redis: {e}")
                return None
        return None

class MultiBot:
    def __init__(self):
        self.bots: Dict[str, commands.Bot] = {}
        self.status_managers: Dict[str, 'StatusManager'] = {}
        self.running = False
        
    async def create_bot(self, token: str, user_id: str) -> Optional[commands.Bot]:
        """Crée un nouveau bot avec le token donné"""
        try:
            intents = discord.Intents.all()
            bot = commands.Bot(command_prefix="!", intents=intents)
            bot.owner_id = user_id
            
            # Événements du bot
            @bot.event
            async def on_ready():
                print(f"Bot {bot.user} connecté pour l'utilisateur {user_id}")
                
                # Synchroniser les commandes slash avec retry
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        synced = await bot.tree.sync()
                        print(f"{len(synced)} commandes synchronisées pour {bot.user} (tentative {attempt + 1})")
                        break
                    except discord.HTTPException as e:
                        if attempt == max_retries - 1:
                            print(f"Échec sync commandes après {max_retries} tentatives: {e}")
                        else:
                            print(f"Retry sync commandes (tentative {attempt + 1}): {e}")
                            await asyncio.sleep(2 ** attempt)
                    except Exception as e:
                        print(f"Erreur inattendue sync commandes: {e}")
                        break
                
                # Créer le gestionnaire de statut
                if token not in self.status_managers:
                    self.status_managers[token] = StatusManager(bot, STATUS_CHANNEL_ID)
                    try:
                        if not self.status_managers[token].update_status.is_running():
                            self.status_managers[token].update_status.start()
                    except Exception as e:
                        print(f"Erreur démarrage StatusManager: {e}")
            
            @bot.event
            async def on_disconnect():
                print(f"Bot {bot.user} déconnecté")
            
            @bot.event
            async def on_resumed():
                print(f"Bot {bot.user} reconnecté")
            
            @bot.event
            async def on_error(event, *args, **kwargs):
                print(f"Erreur dans {event}: {args}")
            
            # Commandes du bot
            self.setup_commands(bot, user_id)
            
            # Stocker le bot
            self.bots[user_id] = bot
            
            return bot
            
        except discord.LoginFailure:
            print(f"Token invalide pour l'utilisateur {user_id}")
            self.remove_token(user_id)
            return None
        except Exception as e:
            print(f"Erreur lors de la création du bot pour {user_id}: {e}")
            return None
    
    def setup_commands(self, bot: commands.Bot, owner_id: str):
        """Configure les commandes pour le bot"""
        
        @bot.tree.command(name="help", description="Affiche toutes les commandes disponibles")
        async def help_command(interaction: discord.Interaction):
            try:
                embed = discord.Embed(
                    title="Aide - Commandes du Bot",
                    description="Voici toutes les commandes disponibles :",
                    color=0x00ff00
                )
                
                # Commandes pour tous les utilisateurs
                embed.add_field(
                    name="Commandes Générales",
                    value=(
                        "`/help` - Affiche cette aide\n"
                        "`/addtoken` - Ajouter votre token de bot (DM uniquement)\n"
                    ),
                    inline=False
                )
                
                # Commandes de modération
                embed.add_field(
                    name="Commandes de Modération",
                    value=(
                        "`/ban <utilisateur> [raison]` - Bannir un utilisateur\n"
                        "`/kick <utilisateur> <durée> [raison]` - Exclure temporairement\n"
                        "`/warn <utilisateur> [raison]` - Donner un avertissement\n"
                    ),
                    inline=False
                )
                
                # Commandes d'administration
                embed.add_field(
                    name="Commandes d'Administration",
                    value=(
                        "`/configrole` - Configurer les permissions d'un rôle\n"
                        "`/roleownerbot <rôle>` - Donner permissions owner à un rôle\n"
                        "`/deflimwarn <nombre>` - Définir limite d'avertissements\n"
                    ),
                    inline=False
                )
                
                embed.set_footer(text="Multi-Bot System | Utilisez les commandes avec responsabilité")
                await interaction.response.send_message(embed=embed, ephemeral=True)
                
            except Exception as e:
                print(f"Erreur commande help: {e}")
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message("Erreur lors de l'affichage de l'aide.", ephemeral=True)
                    else:
                        await interaction.followup.send("Erreur lors de l'affichage de l'aide.", ephemeral=True)
                except:
                    pass
        
        @bot.tree.command(name="addtoken", description="Ajouter votre token de bot")
        async def addtoken(interaction: discord.Interaction):
            try:
                # Vérifier si l'utilisateur a le bon rôle sur le serveur principal
                has_permission = await self.check_user_permission(interaction.user.id)
                if not has_permission:
                    return await interaction.response.send_message(
                        "Vous n'avez pas les permissions nécessaires.", ephemeral=True
                    )
                
                await interaction.response.send_message(
                    "Envoyez-moi votre token de bot en message privé (DM). "
                    "**Attention: Ne partagez JAMAIS votre token publiquement!**", 
                    ephemeral=True
                )
                
                # Attendre le DM avec le token
                def check(m):
                    return (m.author.id == interaction.user.id and 
                           isinstance(m.channel, discord.DMChannel))
                
                try:
                    token_msg = await bot.wait_for("message", check=check, timeout=180)  # 3 minutes
                    token = token_msg.content.strip()
                    
                    # Valider le token
                    if await self.validate_and_store_token(str(interaction.user.id), token):
                        await token_msg.author.send("Token ajouté avec succès!")
                        
                        # Créer et démarrer le nouveau bot
                        try:
                            new_bot = await self.create_bot(token, str(interaction.user.id))
                            if new_bot:
                                # Démarrer dans une tâche avec gestion d'erreur
                                async def safe_start():
                                    try:
                                        await new_bot.start(token)
                                    except Exception as start_error:
                                        print(f"Erreur démarrage bot {interaction.user.id}: {start_error}")
                                        if str(interaction.user.id) in self.bots:
                                            del self.bots[str(interaction.user.id)]
                                
                                asyncio.create_task(safe_start())
                        except Exception as create_error:
                            print(f"Erreur création bot: {create_error}")
                            await token_msg.author.send("Erreur lors de la création du bot.")
                    else:
                        await token_msg.author.send("Token invalide ou erreur lors de l'ajout.")
                        
                except asyncio.TimeoutError:
                    try:
                        await interaction.followup.send("Temps écoulé. Réessayez.", ephemeral=True)
                    except:
                        pass
                        
            except Exception as e:
                print(f"Erreur commande addtoken: {e}")
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message("Une erreur s'est produite.", ephemeral=True)
                    else:
                        await interaction.followup.send("Une erreur s'est produite.", ephemeral=True)
                except:
                    pass
        
        @bot.tree.command(name="configrole", description="Configurer les permissions d'un rôle")
        async def configrole(interaction: discord.Interaction):
            try:
                # Vérifier si l'utilisateur peut utiliser cette commande
                if not await self.can_use_owner_commands(interaction.user, interaction.guild):
                    return await interaction.response.send_message(
                        "Vous n'avez pas les permissions pour utiliser cette commande.", ephemeral=True
                    )
                
                await self.handle_role_config(interaction)
            except Exception as e:
                print(f"Erreur configrole: {e}")
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message("Erreur lors de la configuration.", ephemeral=True)
                except:
                    pass
        
        @bot.tree.command(name="roleownerbot", description="Donner les permissions d'owner à un rôle")
        async def roleownerbot(interaction: discord.Interaction, role: discord.Role):
            try:
                # Seul le vrai propriétaire peut utiliser cette commande
                if str(interaction.user.id) != owner_id:
                    return await interaction.response.send_message(
                        "Seul le propriétaire du bot peut utiliser cette commande.", ephemeral=True
                    )
                
                # Ajouter le rôle aux owners
                key = f"owner_roles:{interaction.guild.id}"
                RedisManager.safe_operation(redis_client.sadd, key, str(role.id))
                
                await interaction.response.send_message(
                    f"Le rôle {role.mention} peut maintenant utiliser les commandes d'administration.", 
                    ephemeral=True
                )
            except Exception as e:
                print(f"Erreur roleownerbot: {e}")
                try:
                    await interaction.response.send_message("Erreur lors de la configuration du rôle.", ephemeral=True)
                except:
                    pass
        
        @bot.tree.command(name="deflimwarn", description="Définir la limite d'avertissements avant kick")
        async def deflimwarn(interaction: discord.Interaction, limite: int):
            try:
                # Vérifier les permissions
                if not await self.can_use_owner_commands(interaction.user, interaction.guild):
                    return await interaction.response.send_message(
                        "Vous n'avez pas les permissions pour utiliser cette commande.", ephemeral=True
                    )
                
                if limite < 1 or limite > 20:
                    return await interaction.response.send_message(
                        "La limite doit être entre 1 et 20 avertissements.", ephemeral=True
                    )
                
                ModerationManager.set_warn_limit(str(interaction.guild.id), limite)
                await interaction.response.send_message(
                    f"Limite d'avertissements définie à {limite}.", ephemeral=True
                )
            except Exception as e:
                print(f"Erreur deflimwarn: {e}")
                try:
                    await interaction.response.send_message("Erreur lors de la configuration.", ephemeral=True)
                except:
                    pass
        
        @bot.tree.command(name="ban", description="Bannir un utilisateur (ajouter à la liste noire)")
        async def ban_user(interaction: discord.Interaction, user: discord.User, raison: str = "Aucune raison"):
            await self.handle_moderation_action(interaction, "ban", user, raison)
        
        @bot.tree.command(name="kick", description="Exclure temporairement un utilisateur")
        async def kick_user(interaction: discord.Interaction, user: discord.User, temps: str, raison: str = "Aucune raison"):
            await self.handle_moderation_action(interaction, "kick", user, raison, temps)
        
        @bot.tree.command(name="warn", description="Donner un avertissement à un utilisateur")
        async def warn_user(interaction: discord.Interaction, user: discord.User, raison: str = "Aucune raison"):
            await self.handle_moderation_action(interaction, "warn", user, raison)
    
    async def can_use_owner_commands(self, user: discord.User, guild: discord.Guild) -> bool:
        """Vérifie si un utilisateur peut utiliser les commandes d'owner"""
        try:
            # Le propriétaire original du bot peut toujours utiliser
            if any(str(bot.owner_id) == str(user.id) for bot in self.bots.values() if hasattr(bot, 'owner_id')):
                return True
            
            # Vérifier si l'utilisateur a un rôle owner
            key = f"owner_roles:{guild.id}"
            owner_role_ids = RedisManager.safe_operation(redis_client.smembers, key) or set()
            
            member = guild.get_member(user.id)
            if member and owner_role_ids:
                user_role_ids = {str(role.id) for role in member.roles}
                return bool(owner_role_ids.intersection(user_role_ids))
                
            return False
        except Exception as e:
            print(f"Erreur vérification owner: {e}")
            return False
    
    async def handle_moderation_action(self, interaction: discord.Interaction, action: str, target_user: discord.User, reason: str, duration: str = None):
        """Gère les actions de modération avec système de crédits"""
        try:
            # Vérifier les permissions de base
            user_roles = [role for role in interaction.user.roles if role != interaction.guild.default_role]
            if not user_roles:
                return await interaction.response.send_message(
                    "Vous n'avez aucun rôle configuré pour la modération.", ephemeral=True
                )
            
            # Trouver le rôle avec les meilleures permissions
            best_role = None
            best_permissions = {"ban": False, "kick": False, "warn": False}
            
            for role in user_roles:
                perms = ModerationManager.get_role_permissions(str(interaction.guild.id), str(role.id))
                if perms.get(action):
                    best_role = role
                    best_permissions = perms
                    break
            
            if not best_role or not best_permissions.get(action):
                return await interaction.response.send_message(
                    f"Vous n'avez pas la permission d'utiliser {action.upper()}.", ephemeral=True
                )
            
            # Vérifier les crédits quotidiens
            credits = ModerationManager.get_daily_credits(str(interaction.user.id), str(interaction.guild.id))
            
            # Si le rôle a changé, réinitialiser les crédits
            if credits["role_id"] != str(best_role.id):
                credits = {"ban": 0, "kick": 0, "warn": 0, "role_id": str(best_role.id)}
            
            # Vérifier la limite de crédits
            max_credits = self.parse_credit_limit(best_permissions.get(action))
            if max_credits > 0 and credits[action] >= max_credits:
                return await interaction.response.send_message(
                    f"Vous avez atteint votre limite quotidienne de {action.upper()} ({max_credits}).", 
                    ephemeral=True
                )
            
            # Exécuter l'action
            embed = None
            if action == "ban":
                ModerationManager.add_to_blacklist(
                    str(interaction.guild.id), str(target_user.id), reason, str(interaction.user.id)
                )
                
                embed = discord.Embed(
                    title="Utilisateur banni",
                    description=f"{target_user.mention} a été ajouté à la liste noire.",
                    color=0xff0000
                )
                embed.add_field(name="Raison", value=reason, inline=False)
                embed.add_field(name="Modérateur", value=interaction.user.mention, inline=True)
                
            elif action == "kick":
                embed = discord.Embed(
                    title="Utilisateur exclu temporairement",
                    description=f"{target_user.mention} a été exclu temporairement.",
                    color=0xffa500
                )
                embed.add_field(name="Durée", value=duration, inline=True)
                embed.add_field(name="Raison", value=reason, inline=False)
                embed.add_field(name="Modérateur", value=interaction.user.mention, inline=True)
                
                # Envoyer DM à l'utilisateur
                try:
                    dm_embed = discord.Embed(
                        title="Exclusion temporaire",
                        description=f"Vous avez été exclu temporairement de {interaction.guild.name}",
                        color=0xffa500
                    )
                    dm_embed.add_field(name="Durée", value=duration, inline=True)
                    dm_embed.add_field(name="Raison", value=reason, inline=False)
                    await target_user.send(embed=dm_embed)
                except:
                    pass
                
            elif action == "warn":
                # Ajouter l'avertissement
                warning_count = ModerationManager.add_warning(
                    str(interaction.guild.id), str(target_user.id), reason, str(interaction.user.id)
                )
                
                warn_limit = ModerationManager.get_warn_limit(str(interaction.guild.id))
                
                embed = discord.Embed(
                    title="Avertissement donné",
                    description=f"{target_user.mention} a reçu un avertissement ({warning_count}/{warn_limit})",
                    color=0xffff00
                )
                embed.add_field(name="Raison", value=reason, inline=False)
                embed.add_field(name="Modérateur", value=interaction.user.mention, inline=True)
                
                # Envoyer DM à l'utilisateur
                try:
                    dm_embed = discord.Embed(
                        title="Avertissement reçu",
                        description=f"Vous avez reçu un avertissement sur {interaction.guild.name}",
                        color=0xffff00
                    )
                    dm_embed.add_field(name="Raison", value=reason, inline=False)
                    dm_embed.add_field(name="Total", value=f"{warning_count}/{warn_limit}", inline=True)
                    await target_user.send(embed=dm_embed)
                except:
                    pass
                
                # Vérifier si kick automatique
                if warning_count >= warn_limit:
                    embed.add_field(
                        name="Action automatique", 
                        value=f"Limite d'avertissements atteinte! Kick automatique recommandé.", 
                        inline=False
                    )
            
            # Mettre à jour les crédits
            ModerationManager.set_daily_credits(
                str(interaction.user.id), str(interaction.guild.id), 
                action, credits[action] + 1, str(best_role.id)
            )
            
            # Ajouter info crédits à l'embed
            remaining = max_credits - (credits[action] + 1) if max_credits > 0 else "∞"
            embed.add_field(name="Crédits restants", value=f"{remaining}", inline=True)
            
            await interaction.response.send_message(embed=embed)
            
        except Exception as e:
            print(f"Erreur modération: {e}")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(f"Erreur: {e}", ephemeral=True)
                else:
                    await interaction.followup.send(f"Erreur: {e}", ephemeral=True)
            except:
                pass
    
    def parse_credit_limit(self, permission_value) -> int:
        """Parse la valeur de permission pour extraire la limite"""
        try:
            if isinstance(permission_value, bool):
                return 0 if permission_value else -1
            elif isinstance(permission_value, str):
                if permission_value.lower() in ["non", "false", "0"]:
                    return -1
                elif permission_value.lower() in ["oui", "true", "illimité", "unlimited"]:
                    return 0
                else:
                    return int(permission_value)
            elif isinstance(permission_value, int):
                return permission_value
        except:
            pass
        return -1
    
    async def check_user_permission(self, user_id: int) -> bool:
        """Vérifie si l'utilisateur a le rôle requis sur le serveur principal"""
        try:
            # Trouver un bot connecté pour vérifier
            available_bot = None
            for bot in self.bots.values():
                if bot and bot.is_ready():
                    available_bot = bot
                    break
            
            if not available_bot:
                print("Aucun bot disponible pour vérifier les permissions")
                return False
                
            guild = available_bot.get_guild(MAIN_GUILD_ID)
            if not guild:
                print(f"Serveur {MAIN_GUILD_ID} introuvable")
                return False
                
            member = guild.get_member(user_id)
            if not member:
                print(f"Membre {user_id} introuvable sur le serveur")
                return False
                
            required_role = guild.get_role(REQUIRED_ROLE_ID)
            if not required_role:
                print(f"Rôle {REQUIRED_ROLE_ID} introuvable")
                return False
                
            has_role = required_role in member.roles
            print(f"Vérification permission {user_id}: {'OK' if has_role else 'KO'}")
            return has_role
            
        except Exception as e:
            print(f"Erreur vérification permission {user_id}: {e}")
            return False
    
    async def validate_and_store_token(self, user_id: str, token: str) -> bool:
        """Valide et stocke un token"""
        try:
            # Tester la connexion avec le token
            test_bot = discord.Client(intents=discord.Intents.default())
            await test_bot.login(token)
            bot_user = test_bot.user
            await test_bot.close()
            
            # Stocker dans Redis
            token_data = {
                "user_id": user_id,
                "bot_name": str(bot_user),
                "bot_id": str(bot_user.id),
                "added_date": datetime.utcnow().isoformat()
            }
            
            RedisManager.safe_operation(redis_client.hset, f"bot_token:{user_id}", mapping=token_data)
            RedisManager.safe_operation(redis_client.set, f"token_raw:{user_id}", token)
            
            print(f"Token validé et stocké pour {user_id} ({bot_user})")
            return True
            
        except Exception as e:
            print(f"Erreur validation token: {e}")
            return False
    
    def remove_token(self, user_id: str):
        """Supprime un token de la base de données"""
        try:
            RedisManager.safe_operation(redis_client.delete, f"bot_token:{user_id}")
            RedisManager.safe_operation(redis_client.delete, f"token_raw:{user_id}")
            print(f"Token supprimé pour l'utilisateur {user_id}")
        except Exception as e:
            print(f"Erreur suppression token: {e}")
    
    def get_stored_tokens(self) -> Dict[str, str]:
        """Récupère tous les tokens stockés"""
        tokens = {}
        try:
            for key in redis_client.scan_iter(match="token_raw:*"):
                user_id = key.split(":")[-1]
                token = RedisManager.safe_operation(redis_client.get, key)
                if token:
                    tokens[user_id] = token
        except Exception as e:
            print(f"Erreur récupération tokens: {e}")
        return tokens
    
    async def handle_role_config(self, interaction: discord.Interaction):
        """Gère la configuration des rôles"""
        try:
            await interaction.response.send_message(
                "Quel rôle voulez-vous configurer ? (écrivez le nom)", ephemeral=True
            )
            
            def check(m):
                return m.author == interaction.user and m.channel == interaction.channel
            
            # Récupérer le bot correspondant à cet utilisateur
            bot = None
            for b in self.bots.values():
                if hasattr(b, 'owner_id') and b.owner_id == str(interaction.user.id):
                    bot = b
                    break
            
            if not bot:
                return await interaction.followup.send("Bot non trouvé.", ephemeral=True)
            
            role_msg = await bot.wait_for("message", check=check, timeout=300)
            role_name = role_msg.content
            role = discord.utils.get(interaction.guild.roles, name=role_name)
            
            if not role:
                return await interaction.followup.send("Rôle introuvable.", ephemeral=True)
            
            # Configuration des permissions
            role_data = {"ban": None, "kick": None, "warn": None}
            actions = ["ban", "kick", "warn"]
            
            for action in actions:
                await interaction.followup.send(
                    f"Voulez-vous que ce rôle puisse {action.upper()} ? (oui/non)", ephemeral=True
                )
                ans = await bot.wait_for("message", check=check, timeout=300)
                
                if ans.content.lower() == "oui":
                    if action in ["ban", "kick"]:
                        await interaction.followup.send(
                            f"Quelle limite pour {action.upper()} ? (nombre ou 'non' pour illimité)", 
                            ephemeral=True
                        )
                        limit_msg = await bot.wait_for("message", check=check, timeout=300)
                        role_data[action] = limit_msg.content
                    else:
                        role_data[action] = True
                else:
                    role_data[action] = False
            
            # Sauvegarder dans Redis
            redis_key = f"role_config:{interaction.guild.id}:{role.id}"
            RedisManager.safe_operation(redis_client.hset, redis_key, mapping={
                "role_name": role.name,
                "config": json.dumps(role_data),
                "updated_by": str(interaction.user.id),
                "updated_at": datetime.utcnow().isoformat()
            })
            
            await interaction.followup.send(
                f"Configuration du rôle {role.name} enregistrée.", ephemeral=True
            )
            
        except asyncio.TimeoutError:
            try:
                await interaction.followup.send("Temps écoulé.", ephemeral=True)
            except:
                pass
        except Exception as e:
            print(f"Erreur handle_role_config: {e}")
            try:
                await interaction.followup.send(f"Erreur: {e}", ephemeral=True)
            except:
                pass

class StatusManager:
    def __init__(self, bot: commands.Bot, channel_id: int):
        self.bot = bot
        self.channel_id = channel_id
        self.status_message_id = None
        self.bot_user_id = str(bot.user.id) if bot.user else "unknown"
        self.load_status_message()
    
    def load_status_message(self):
        """Charge l'ID du message de statut depuis Redis"""
        try:
            msg_id = RedisManager.safe_operation(redis_client.get, f"status_msg:{self.bot_user_id}")
            self.status_message_id = int(msg_id) if msg_id else None
        except Exception as e:
            print(f"Erreur chargement status: {e}")
    
    def save_status_message(self, msg_id: int):
        """Sauvegarde l'ID du message de statut"""
        self.status_message_id = msg_id
        RedisManager.safe_operation(redis_client.set, f"status_msg:{self.bot_user_id}", str(msg_id))
    
    @tasks.loop(minutes=30)
    async def update_status(self):
        """Met à jour le message de statut"""
        if not self.bot.is_ready():
            return
            
        channel = self.bot.get_channel(self.channel_id)
        if not channel:
            return
        
        try:
            # Supprimer l'ancien message s'il existe
            if self.status_message_id:
                try:
                    old_msg = await channel.fetch_message(self.status_message_id)
                    # Vérifier que c'est bien notre message
                    if old_msg.author.id == self.bot.user.id:
                        await old_msg.delete()
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
            
            # Envoyer le nouveau message
            timestamp = int(datetime.utcnow().timestamp())
            content = f"✅ {self.bot.user.name} en ligne (<t:{timestamp}:R>)"
            msg = await channel.send(content)
            self.save_status_message(msg.id)
                    
        except discord.Forbidden:
            print(f"Permission refusée pour le status de {self.bot.user}")
        except Exception as e:
            print(f"Erreur update status {self.bot.user}: {e}")
    
    @update_status.before_loop
    async def before_update_status(self):
        await self.bot.wait_until_ready()

class ModerationManager:
    """Gestionnaire de modération avec système de crédits"""
    
    @staticmethod
    def get_daily_credits(user_id: str, guild_id: str) -> Dict:
        """Récupère les crédits quotidiens d'un utilisateur"""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        key = f"daily_credits:{guild_id}:{user_id}:{today}"
        
        credits_data = RedisManager.safe_operation(redis_client.hgetall, key) or {}
        if not credits_data:
            return {"ban": 0, "kick": 0, "warn": 0, "role_id": None}
        
        return {
            "ban": int(credits_data.get("ban", 0)),
            "kick": int(credits_data.get("kick", 0)), 
            "warn": int(credits_data.get("warn", 0)),
            "role_id": credits_data.get("role_id")
        }
    
    @staticmethod
    def set_daily_credits(user_id: str, guild_id: str, action: str, used: int, role_id: str):
        """Met à jour les crédits quotidiens"""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        key = f"daily_credits:{guild_id}:{user_id}:{today}"
        
        RedisManager.safe_operation(redis_client.hset, key, mapping={
            action: str(used),
            "role_id": role_id,
            "updated_at": datetime.utcnow().isoformat()
        })
        # Expiration automatique après 2 jours
        RedisManager.safe_operation(redis_client.expire, key, 172800)
    
    @staticmethod
    def get_role_permissions(guild_id: str, role_id: str) -> Dict:
        """Récupère les permissions d'un rôle"""
        try:
            key = f"role_config:{guild_id}:{role_id}"
            role_data = RedisManager.safe_operation(redis_client.hgetall, key) or {}
            if role_data and "config" in role_data:
                return json.loads(role_data["config"])
            return {"ban": False, "kick": False, "warn": False}
        except Exception as e:
            print(f"Erreur récupération permissions: {e}")
            return {"ban": False, "kick": False, "warn": False}
    
    @staticmethod
    def get_warn_limit(guild_id: str) -> int:
        """Récupère la limite d'avertissements avant kick"""
        try:
            limit = RedisManager.safe_operation(redis_client.get, f"warn_limit:{guild_id}")
            return int(limit) if limit else 3
        except:
            return 3
    
    @staticmethod
    def set_warn_limit(guild_id: str, limit: int):
        """Définit la limite d'avertissements"""
        RedisManager.safe_operation(redis_client.set, f"warn_limit:{guild_id}", str(limit))
    
    @staticmethod
    def add_warning(guild_id: str, user_id: str, reason: str, moderator_id: str):
        """Ajoute un avertissement à un utilisateur"""
        key = f"warnings:{guild_id}:{user_id}"
        warning_data = {
            "reason": reason,
            "moderator_id": moderator_id,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        # Ajouter à la liste des avertissements
        RedisManager.safe_operation(redis_client.lpush, key, json.dumps(warning_data))
        
        # Garder seulement les 50 derniers avertissements
        RedisManager.safe_operation(redis_client.ltrim, key, 0, 49)
        
        # Retourner le nombre total d'avertissements
        return RedisManager.safe_operation(redis_client.llen, key) or 0
    
    @staticmethod
    def get_warning_count(guild_id: str, user_id: str) -> int:
        """Récupère le nombre d'avertissements d'un utilisateur"""
        key = f"warnings:{guild_id}:{user_id}"
        return RedisManager.safe_operation(redis_client.llen, key) or 0
    
    @staticmethod
    def add_to_blacklist(guild_id: str, user_id: str, reason: str, moderator_id: str):
        """Ajoute un utilisateur à la liste noire"""
        key = f"blacklist:{guild_id}"
        blacklist_data = {
            "user_id": user_id,
            "reason": reason,
            "moderator_id": moderator_id,
            "timestamp": datetime.utcnow().isoformat()
        }
        RedisManager.safe_operation(redis_client.hset, key, user_id, json.dumps(blacklist_data))

# ---------- Gestionnaire principal ----------
multi_bot = MultiBot()

class BackgroundTasks:
    """Gestionnaire des tâches en arrière-plan pour éviter les problèmes avec asyncio sur Render"""
    
    def __init__(self):
        self.tasks_running = False
        self.thread = None
    
    def start_background_tasks(self):
        """Démarre les tâches en arrière-plan dans un thread séparé"""
        if not self.tasks_running:
            self.tasks_running = True
            self.thread = threading.Thread(target=self._run_background_loop, daemon=True)
            self.thread.start()
            print("Tâches en arrière-plan démarrées")
    
    def stop_background_tasks(self):
        """Arrête les tâches en arrière-plan"""
        self.tasks_running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
    
    def _run_background_loop(self):
        """Boucle principale des tâches en arrière-plan"""
        while self.tasks_running:
            try:
                # Vérification quotidienne des rôles (toutes les 24h)
                self._check_user_roles()
                
                # Nettoyage des anciens crédits (toutes les heures)
                self._cleanup_old_credits()
                
                # Attendre 1 heure avant la prochaine vérification
                time.sleep(3600)
                
            except Exception as e:
                print(f"Erreur tâche arrière-plan: {e}")
                time.sleep(300)  # Attendre 5 minutes en cas d'erreur
    
    def _check_user_roles(self):
        """Vérifie si les utilisateurs ont toujours le rôle requis"""
        try:
            tokens = multi_bot.get_stored_tokens()
            for user_id in list(tokens.keys()):
                print(f"Vérification du rôle pour l'utilisateur {user_id}")
                # Cette vérification nécessiterait une implémentation asyncio complète
                
        except Exception as e:
            print(f"Erreur vérification rôles: {e}")
    
    def _cleanup_old_credits(self):
        """Nettoie les anciens crédits"""
        try:
            yesterday = (datetime.utcnow() - timedelta(days=2)).strftime("%Y-%m-%d")
            
            # Supprimer les anciens crédits
            count = 0
            for key in redis_client.scan_iter(match=f"daily_credits:*:{yesterday}"):
                RedisManager.safe_operation(redis_client.delete, key)
                count += 1
            
            if count > 0:
                print(f"{count} anciens crédits supprimés")
                
        except Exception as e:
            print(f"Erreur nettoyage crédits: {e}")

# Gestionnaire des tâches
background_tasks = BackgroundTasks()

async def start_all_bots():
    """Démarre tous les bots stockés"""
    try:
        stored_tokens = multi_bot.get_stored_tokens()
        
        if not stored_tokens:
            print("Aucun token utilisateur stocké")
            return
        
        print(f"Démarrage de {len(stored_tokens)} bots utilisateur...")
        
        for user_id, token in stored_tokens.items():
            try:
                bot = await multi_bot.create_bot(token, user_id)
                if bot:
                    # Démarrer le bot dans une tâche séparée avec gestion d'erreur
                    async def start_bot_safely(b, t, uid):
                        try:
                            await b.start(t)
                        except Exception as e:
                            print(f"Bot {uid} crashé: {e}")
                            # Nettoyer le bot de la liste
                            if uid in multi_bot.bots:
                                del multi_bot.bots[uid]
                    
                    asyncio.create_task(start_bot_safely(bot, token, user_id))
                    print(f"Bot pour {user_id} créé et en cours de démarrage...")
                    
                    # Petite pause pour éviter la surcharge
                    await asyncio.sleep(2)
                    
            except Exception as e:
                print(f"Erreur création bot {user_id}: {e}")
                continue
                
    except Exception as e:
        print(f"Erreur démarrage bots: {e}")

async def start_bot(main_token: str):
    """Démarre le système multi-bots de manière optimisée pour Render"""
    try:
        print("Initialisation du système multi-bots...")
        
        # Démarrer les tâches en arrière-plan
        background_tasks.start_background_tasks()
        
        # Créer le bot principal
        intents = discord.Intents.all()
        main_bot = commands.Bot(command_prefix="!", intents=intents)
        main_bot.owner_id = "0"  # Bot principal
        
        # Configuration du bot principal
        multi_bot.setup_commands(main_bot, "0")
        
        @main_bot.event
        async def on_ready():
            print(f"Bot principal {main_bot.user} connecté")
            
            # Synchroniser les commandes avec retry
            for attempt in range(3):
                try:
                    synced = await main_bot.tree.sync()
                    print(f"{len(synced)} commandes synchronisées pour le bot principal")
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f"Échec sync commandes principales: {e}")
                    else:
                        print(f"Retry sync commandes principales: {e}")
                        await asyncio.sleep(2)
            
            # Démarrer les bots utilisateur après le bot principal
            print("Démarrage des bots utilisateur...")
            await start_all_bots()
        
        @main_bot.event
        async def on_disconnect():
            print("Bot principal déconnecté")
        
        @main_bot.event
        async def on_resumed():
            print("Bot principal reconnecté")
        
        @main_bot.event
        async def on_error(event, *args, **kwargs):
            print(f"Erreur dans l'événement {event}: {args}")
        
        # Ajouter le bot principal à la collection
        multi_bot.bots["0"] = main_bot
        
        # Démarrer le bot principal
        print("Connexion du bot principal...")
        await main_bot.start(main_token)
        
    except discord.LoginFailure:
        print("Token principal invalide!")
        raise
    except KeyboardInterrupt:
        print("Arrêt demandé par l'utilisateur")
        background_tasks.stop_background_tasks()
        raise
    except Exception as e:
        print(f"Erreur critique au démarrage: {e}")
        background_tasks.stop_background_tasks()
        raise

if __name__ == "__main__":
    print("Ce fichier doit être importé depuis main.py")