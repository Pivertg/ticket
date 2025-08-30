import os
import json
import asyncio
import signal
import sys
from dotenv import load_dotenv
from keep_alive import keep_alive
import bot

# Charger le .env
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
BOT_TYPE = os.getenv("BOT_TYPE", "MULTI_BOT")

# Charger BOTS_JSON si présent (pour compatibilité)
BOTS_JSON = os.getenv("BOTS_JSON")
if BOTS_JSON:
    try:
        BOTS_JSON = json.loads(BOTS_JSON)
        print(f"📋 Configuration BOTS_JSON chargée: {len(BOTS_JSON)} bots")
    except Exception as e:
        print(f"❌ Erreur parsing BOTS_JSON: {e}")
        BOTS_JSON = []

print(f"🚀 Lancement du système {BOT_TYPE}...")
print(f"🔗 Redis Host: {os.getenv('REDIS_HOST', 'localhost')}")
print(f"🔑 Token principal disponible: {'✅' if DISCORD_TOKEN else '❌'}")

class GracefulKiller:
    """Gestionnaire pour l'arrêt gracieux du bot"""
    def __init__(self):
        self.kill_now = False
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)
    
    def _handle_signal(self, signum, frame):
        print(f"\n🛑 Signal {signum} reçu, arrêt en cours...")
        self.kill_now = True

async def main():
    """Fonction principale optimisée pour Render"""
    killer = GracefulKiller()
    
    try:
        # Vérifier le token
        if not DISCORD_TOKEN:
            print("❌ DISCORD_TOKEN manquant dans les variables d'environnement!")
            sys.exit(1)
        
        # Lancer Flask pour maintenir le script actif sur Render
        print("🌐 Démarrage du serveur Flask...")
        keep_alive()
        
        # Petite pause pour laisser Flask se stabiliser
        await asyncio.sleep(2)
        
        print("🤖 Démarrage du système de bots...")
        
        # Créer une tâche pour le bot principal
        bot_task = asyncio.create_task(bot.start_bot(DISCORD_TOKEN))
        
        # Boucle principale avec gestion de l'arrêt gracieux
        while not killer.kill_now:
            try:
                # Vérifier si la tâche du bot est encore en vie
                if bot_task.done():
                    # Si la tâche est terminée, vérifier s'il y a eu une erreur
                    try:
                        await bot_task
                    except Exception as e:
                        print(f"❌ Erreur du bot principal: {e}")
                        print("🔄 Tentative de redémarrage...")
                        await asyncio.sleep(5)
                        bot_task = asyncio.create_task(bot.start_bot(DISCORD_TOKEN))
                
                # Attendre un peu avant la prochaine vérification
                await asyncio.sleep(10)
                
            except KeyboardInterrupt:
                print("\n⏹️ Interruption clavier détectée")
                break
            except Exception as e:
                print(f"❌ Erreur dans la boucle principale: {e}")
                await asyncio.sleep(30)  # Attendre avant de continuer
        
        print("🛑 Arrêt du système...")
        
        # Annuler la tâche du bot si elle est encore en cours
        if not bot_task.done():
            bot_task.cancel()
            try:
                await bot_task
            except asyncio.CancelledError:
                pass
        
        # Fermer toutes les connexions des bots
        for bot_instance in bot.multi_bot.bots.values():
            try:
                if not bot_instance.is_closed():
                    await bot_instance.close()
            except Exception as e:
                print(f"❌ Erreur fermeture bot: {e}")
        
        print("✅ Arrêt complet du système")
        
    except KeyboardInterrupt:
        print("\n⏹️ Arrêt demandé par l'utilisateur")
    except Exception as e:
        print(f"❌ Erreur critique: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

def run_bot():
    """Point d'entrée principal pour éviter les problèmes d'asyncio sur Render"""
    try:
        # Configurer la politique d'événements pour Windows/Linux
        if sys.platform.startswith('win'):
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        
        # Créer un nouvel event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # Exécuter la fonction principale
            loop.run_until_complete(main())
        finally:
            # Nettoyer le loop
            try:
                # Annuler toutes les tâches en cours
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                
                # Attendre que toutes les tâches se terminent
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                
                # Fermer le loop
                loop.close()
            except Exception as e:
                print(f"❌ Erreur nettoyage loop: {e}")
    
    except Exception as e:
        print(f"❌ Erreur fatale: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    print("=" * 50)
    print("🤖 Multi-Bot System - Starting...")
    print("=" * 50)
    
    run_bot()