import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
from sqlalchemy.orm import Session # Import Session
from datetime import datetime, timezone # Import datetime and timezone
import config

# Import database models and functions
from database import User, Conversation, Message, SessionLocal, create_db_and_tables

# --- For OpenAI GPT ---
from openai import OpenAI
# --- For Anthropic Claude ---
import anthropic
# --- For Azure OpenAI ---
from openai import AzureOpenAI
from azure.core.credentials import AzureKeyCredential

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load API keys from environment variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Load Azure OpenAI environment variables
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT_URL")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION")
AZURE_OPENAI_DEPLOYMENT_NAME = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")

# Check if tokens/keys are loaded
if not TELEGRAM_BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN is not set. Please check your .env file.")
    exit(1)

ai_client = None
# Use unified system prompt from config
system_prompt_content = config.SYSTEM_PROMPT

# Initialize AI client based on available API keys
if OPENAI_API_KEY:
    ai_client = OpenAI(api_key=OPENAI_API_KEY)
    logger.info("Using OpenAI client.")
elif ANTHROPIC_API_KEY:
    ai_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    logger.info("Using Anthropic Claude client.")
elif AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_VERSION and AZURE_OPENAI_DEPLOYMENT_NAME:
    ai_client = AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=AZURE_OPENAI_API_KEY,
        api_version=AZURE_OPENAI_API_VERSION
    )
    logger.info("Using Azure OpenAI client.")
else:
    logger.error("No OpenAI, Anthropic, or Azure OpenAI API key found. Please set one in your .env file.")
    exit(1)

# --- Authorized User IDs (for survey access control) ---
# For now, you can populate this by manually adding your own Telegram user ID for testing.
# In a real scenario, this list would be managed via an admin interface or pre-populated.
AUTHORIZED_TELEGRAM_USER_IDS = {
}

# Helper function to get or create a user and their current conversation
def get_or_create_user_and_conversation(db: Session, telegram_user_id: int, first_name: str, last_name: str, username: str, is_bot: bool):
    user = db.query(User).filter(User.telegram_user_id == telegram_user_id).first()
    if not user:
        user = User(
            telegram_user_id=telegram_user_id,
            first_name=first_name,
            last_name=last_name,
            username=username,
            is_bot=is_bot
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        logger.info(f"New user created: {user.username} (ID: {user.telegram_user_id})")

    # Get the active conversation for this user, or create a new one if none open
    # A simple way for now: assume a new conversation starts with /start, or create one if no messages yet
    conversation = db.query(Conversation).filter(
        Conversation.user_id == user.id,
        Conversation.ended_at == None # Find open conversations
    ).first()

    if not conversation:
        conversation = Conversation(user_id=user.id)
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        logger.info(f"New conversation started for user {user.telegram_user_id}")
    
    return user, conversation

# Helper function to get conversation history from DB
def get_conversation_history_from_db(db: Session, conversation_id: int):
    messages = db.query(Message).filter(
        Message.conversation_id == conversation_id
    ).order_by(Message.timestamp).all()
    
    history = []
    for msg in messages:
        history.append({"role": msg.sender_role, "content": msg.content})
    return history


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.message.from_user.id
    telegram_user = update.effective_user # Get full user object

    if user_id not in AUTHORIZED_TELEGRAM_USER_IDS and len(AUTHORIZED_TELEGRAM_USER_IDS) > 0:
        logger.warning(f"Unauthorized /start attempt by user ID: {user_id}")
        await update.message.reply_text(
            "Sorry, this bot is for authorized users only. Please contact your administrator for access."
        )
        return

    db: Session = SessionLocal()
    try:
        # Get or create user and their conversation
        user, conversation = get_or_create_user_and_conversation(
            db, 
            telegram_user.id, 
            telegram_user.first_name, 
            telegram_user.last_name, 
            telegram_user.username, 
            telegram_user.is_bot
        )

        # Mark any previous open conversations as ended if a new /start is issued
        # This is a simple logic for now; real survey might need more robust session management
        db.query(Conversation).filter(
            Conversation.user_id == user.id,
            Conversation.id != conversation.id, # Exclude the current one
            Conversation.ended_at == None
        ).update({"ended_at": datetime.now(timezone.utc)})
        db.commit()
        
        # Log the start message
        new_message = Message(
            conversation_id=conversation.id,
            sender_role='user',
            content='/start'
        )
        db.add(new_message)
        db.commit()

        await update.message.reply_text(
            'Hello! I\'m your survey bot. Let\'s get started. '
            'You can type your responses, and I\'ll guide you through the survey.'
        )
        logger.info(f"User {user_id} started the bot. Conversation ID: {conversation.id}")

    except Exception as e:
        logger.error(f"Error in start command for user {user_id}: {e}", exc_info=True)
        await update.message.reply_text("Sorry, I encountered an error. Please try again later.")
    finally:
        db.close()


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.message.from_user.id
    chat_id = update.message.chat_id # Chat ID is essentially user_id for private chats
    user_message_content = update.message.text

    db: Session = SessionLocal()
    try:
        # Access control
        if user_id not in AUTHORIZED_TELEGRAM_USER_IDS and len(AUTHORIZED_TELEGRAM_USER_IDS) > 0:
            logger.warning(f"Unauthorized message from user ID: {user_id} - '{user_message_content}'")
            await update.message.reply_text(
                "Sorry, I cannot process your request. This bot is for authorized users only."
            )
            return

        # Ensure private chat
        if update.message.chat.type != 'private':
            await update.message.reply_text("Sorry, this bot only works in private chats.")
            logger.warning(f"Message from non-private chat (type: {update.message.chat.type}) received from chat_id: {chat_id}")
            return

        # Get or create user and their active conversation
        # Note: If user sends message without /start, a new conversation will be created.
        # You might want to force /start or handle this differently for a formal survey.
        user, conversation = get_or_create_user_and_conversation(
            db, 
            user_id, 
            update.effective_user.first_name, 
            update.effective_user.last_name, 
            update.effective_user.username, 
            update.effective_user.is_bot
        )
        
        # Save user's message to the database
        user_message_db = Message(
            conversation_id=conversation.id,
            sender_role='user',
            content=user_message_content
        )
        db.add(user_message_db)
        db.commit()
        db.refresh(user_message_db) # Refresh to get ID, timestamp etc.


        # Get last 100 messages for context
        # We fetch from DB now instead of in-memory dictionary
        conversation_history = get_conversation_history_from_db(db, conversation.id)
        messages_for_ai = conversation_history[-100:] # Use last 100 messages of the history

        ai_response_content = "I'm thinking..." # Default response while AI processes

        if OPENAI_API_KEY and ai_client:
            # --- Interact with GPT ---
            # Prepend system message for OpenAI
            full_messages_for_ai = [{"role": "system", "content": system_prompt_content}] + messages_for_ai

            response = ai_client.chat.completions.create(
                model="gpt-4.1-2025-04-14",
                messages=full_messages_for_ai,
                temperature=0
            )
            ai_response_content = response.choices[0].message.content

        elif ANTHROPIC_API_KEY and ai_client:
            # --- Interact with Claude ---
            response = ai_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=1024,
                messages=messages_for_ai, # For Claude, system prompt is a separate parameter
                system=system_prompt_content,
                temperature=0
            )
            ai_response_content = response.content[0].text
        
        elif AZURE_OPENAI_API_KEY and ai_client:
            # --- Interact with Azure OpenAI ---
            full_messages_for_ai = [{"role": "system", "content": system_prompt_content}] + messages_for_ai
            response = ai_client.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT_NAME,
                messages=full_messages_for_ai,
                temperature=0,
                max_tokens=1024
            )
            ai_response_content = response.choices[0].message.content
        else:
            logger.error("No AI client initialized. Please check your API keys.")
            await update.message.reply_text("Sorry, I cannot process your request at the moment.")
            return

        # Save AI's response to the database
        ai_message_db = Message(
            conversation_id=conversation.id,
            sender_role='assistant',
            content=ai_response_content
        )
        db.add(ai_message_db)
        db.commit()
        db.refresh(ai_message_db)

        await update.message.reply_text(ai_response_content)
        logger.info(f"Chat ID: {chat_id}, User: '{user_message_content}', AI: '{ai_response_content}'")

    except Exception as e:
        logger.error(f"Error processing message for user {user_id}: {e}", exc_info=True)
        await update.message.reply_text("Sorry, I encountered an error. Please try again later.")
    finally:
        db.close()

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the error and send a telegram message to notify the developer."""
    logger.error(f"Update {update} caused error {context.error}", exc_info=True)
    if update.effective_message:
        await update.effective_message.reply_text("An internal error occurred. I've logged it.")


def main() -> None:
    """Start the bot."""
    # Ensure database tables are created before starting the bot
    if os.getenv("CREATE_DB_ON_START") == "true":
        create_db_and_tables()


    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)

    logger.info("Bot started polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
