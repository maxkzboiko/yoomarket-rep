import os
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, create_engine, BigInteger
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from datetime import datetime, timezone

# Database URL: defaults to SQLite file 'bot.db' in project root, override via DATABASE_URL env var
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./bot.db")

# Create engine and session factory
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for ORM models
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    telegram_user_id = Column(BigInteger, unique=True, index=True, nullable=False)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    username = Column(String, nullable=True)
    is_bot = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    conversations = relationship("Conversation", back_populates="user")

class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    ended_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation")

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False)
    sender_role = Column(String, nullable=False)
    content = Column(String, nullable=False)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    conversation = relationship("Conversation", back_populates="messages")


def create_db_and_tables():
    """
    Create database tables based on ORM models.
    """
    Base.metadata.create_all(bind=engine)

def drop_and_recreate_tables():
    """
    Drop all tables and recreate them. USE WITH CAUTION - THIS DELETES ALL DATA!
    """
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    
