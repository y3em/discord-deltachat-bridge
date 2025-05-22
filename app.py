import os
import asyncio
import discord
from discord.ext import commands
import threading
import json
import logging
from deltachat_rpc_client import Bot, DeltaChat, Rpc, events

DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN')
MY_DC_EMAIL = os.environ.get('MY_DC_EMAIL')
DELTACHAT_RPC_HOST = os.environ.get('DELTACHAT_RPC_HOST', '127.0.0.1')
DELTACHAT_RPC_PORT = os.environ.get('DELTACHAT_RPC_PORT', '23123')
CHAT_MAPPING_FILE = '/tmp/discord_chat_mapping.json'

chat_mapping = {}
dc_account = None
dc_bot_instance = None
intents = discord.Intents.default()
intents.messages = intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)
dc_hooks = events.HookCollection()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(threadName)s] - %(message)s')

def load_mapping():
    global chat_mapping
    try:
        if os.path.exists(CHAT_MAPPING_FILE):
            with open(CHAT_MAPPING_FILE, 'r') as f:
                chat_mapping = json.load(f)
                logging.info(f"Loaded {len(chat_mapping)} chat mappings")
        else:
            logging.info("Chat mapping file not found, starting fresh")
    except Exception as e:
        logging.error(f"Error loading mapping: {e}")

def save_mapping():
    try:
        os.makedirs(os.path.dirname(CHAT_MAPPING_FILE), exist_ok=True)
        with open(CHAT_MAPPING_FILE, 'w') as f:
            json.dump(chat_mapping, f)
            logging.info(f"Saved {len(chat_mapping)} chat mappings")
    except Exception as e:
        logging.error(f"Error saving mapping: {e}")

@bot.event
async def on_ready():
    logging.info(f"Discord bot {bot.user} is connected!")
    load_mapping()

@bot.event
async def on_message(message):
    global dc_bot_instance
    
    logging.info(f"Received message: '{message.content}' from {message.author.name} in channel {message.channel.name}")
    
    if message.author.bot:
        logging.info("Ignoring bot message")
        return
        
    if dc_bot_instance is None:
        logging.error("DeltaChat bot instance is None, can't process message")
        return
    
    channel_id = str(message.channel.id)
    create_new_group = False
    
    if channel_id in chat_mapping:
        dc_chat_id = chat_mapping[channel_id]
        chat = dc_bot_instance.account.get_chat_by_id(dc_chat_id)
        if not chat:
            create_new_group = True
    else:
        create_new_group = True
    
    if create_new_group:
        group_name = f"Discord: #{message.channel.name}"
        logging.info(f"Creating new DeltaChat group: {group_name}")
        chat = dc_bot_instance.account.create_group(group_name)
        
        personal_email = "y@4r.ma"
        try:
            contact = dc_bot_instance.account.create_contact(personal_email)
            chat.add_contact(personal_email)
            logging.info(f"Added {personal_email} to the group")
        except Exception as e:
            logging.error(f"Error adding personal email to group: {e}")
        
        chat_mapping[channel_id] = chat.id
        save_mapping()
        await message.channel.send(f"Created new DeltaChat group: {group_name}")
    
    try:
        dc_chat_id = chat_mapping[channel_id]
        chat = dc_bot_instance.account.get_chat_by_id(dc_chat_id)
        if chat:
            chat.send_message(text=f"{message.content}")
            logging.info(f"Sent message to DeltaChat group {dc_chat_id}")
        else:
            logging.error(f"Could not find DeltaChat group with ID {dc_chat_id}")
            del chat_mapping[channel_id]
            save_mapping()
            await message.channel.send("Error: Could not find DeltaChat group. Will recreate on next message.")
    except Exception as e:
        logging.error(f"Error sending to DeltaChat: {e}")
        if channel_id in chat_mapping:
            del chat_mapping[channel_id]
            save_mapping()
        await message.channel.send(f"Error sending to DeltaChat: {e}")

@dc_hooks.on(events.NewMessage())
def on_dc_message(event):
    global dc_account
    if dc_account is None:
        logging.error("DeltaChat account is None, can't process message")
        return
    
    message = event.message_snapshot
    from_addr = getattr(message, "from_addr", "")
    logging.info(f"Received DeltaChat message from {from_addr}")
    
    if MY_DC_EMAIL and MY_DC_EMAIL.lower() in from_addr.lower():
        if message.text and message.text.strip().startswith("<"):
            logging.info("Ignoring outgoing message (sent from Discord)")
            return
            
    dc_chat_id = getattr(message, "chat_id", None)
    if not dc_chat_id:
        logging.error("Message has no chat_id")
        return
        
    discord_channel_id = None
    for d_id, dc_id in chat_mapping.items():
        if int(dc_id) == int(dc_chat_id):
            discord_channel_id = int(d_id)
            break
    
    if discord_channel_id:
        channel = bot.get_channel(discord_channel_id)
        if channel:
            text = getattr(message, "text", "")
            asyncio.run_coroutine_threadsafe(
                channel.send(text), 
                bot.loop
            )
            logging.info(f"Sent message to Discord channel {discord_channel_id}")
        else:
            logging.error(f"Could not find Discord channel with ID {discord_channel_id}")
    else:
        logging.error(f"No mapping found for DeltaChat group {dc_chat_id}")

def run_deltachat(email, password):
    global dc_account, dc_bot_instance
    try:
        logging.info(f"Starting DeltaChat RPC client")
        os.environ["DELTACHAT_RPC_ADDRESS"] = f"{DELTACHAT_RPC_HOST}:{DELTACHAT_RPC_PORT}"
        
        with Rpc() as rpc:
            deltachat = DeltaChat(rpc)
            
            logging.info(f"Creating new account for {email}")
            account_id = deltachat.add_account()
            logging.info(f"Created new account: {account_id}")
            
            dc_account = account_id
            dc_bot_instance = Bot(dc_account, dc_hooks)
            
            logging.info(f"Configuring DeltaChat account with email: {email}")
            dc_bot_instance.configure(email=email, password=password)
            
            logging.info("Connected to DeltaChat RPC server")
            dc_bot_instance.account.set_config("displayname", "Nezmi Bot")
            
            logging.info("Starting DeltaChat bot loop")
            dc_bot_instance.run_forever()
    except Exception as e:
        logging.error(f"Error in DeltaChat thread: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python app.py <dc_email> <dc_password>")
        sys.exit(1)
        
    email, password = sys.argv[1], sys.argv[2]
    
    if os.path.exists(CHAT_MAPPING_FILE):
        os.rename(CHAT_MAPPING_FILE, f"{CHAT_MAPPING_FILE}.bak")
        logging.info(f"Renamed existing mapping file to {CHAT_MAPPING_FILE}.bak")
    
    if not DISCORD_TOKEN:
        logging.error("DISCORD_TOKEN environment variable not set")
        print("Please set the DISCORD_TOKEN environment variable")
        sys.exit(1)
        
    logging.info("Starting DeltaChat thread")
    threading.Thread(target=run_deltachat, args=(email, password), daemon=True, name="DeltaChatThread").start()
    
    logging.info("Starting Discord bot")
    bot.run(DISCORD_TOKEN)