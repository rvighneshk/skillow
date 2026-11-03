from __future__ import annotations

from datetime import datetime
from sqlalchemy import String, Text, Integer, Float, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base

class Tutorial(Base):
    __tablename__ = "tutorials"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(240), default="Untitled tutorial")
    filename: Mapped[str] = mapped_column(String(300))
    video_path: Mapped[str] = mapped_column(String(600))
    status: Mapped[str] = mapped_column(String(30), default="queued")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    transcript: Mapped[str] = mapped_column(Text, default="")
    lesson_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    steps = relationship("LessonStep", back_populates="tutorial", cascade="all, delete-orphan")

class LessonStep(Base):
    __tablename__ = "lesson_steps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tutorial_id: Mapped[int] = mapped_column(ForeignKey("tutorials.id"))
    step_number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(240), default="")
    instruction: Mapped[str] = mapped_column(Text, default="")
    start_seconds: Mapped[float] = mapped_column(Float, default=0)
    end_seconds: Mapped[float] = mapped_column(Float, default=0)
    visual_observation: Mapped[str] = mapped_column(Text, default="")
    safety: Mapped[str] = mapped_column(Text, default="")
    tutorial = relationship("Tutorial", back_populates="steps")

class Comparison(Base):
    __tablename__ = "comparisons"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tutorial_id: Mapped[int] = mapped_column(ForeignKey("tutorials.id"))
    photo_path: Mapped[str] = mapped_column(String(600))
    analysis_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tutorial_id: Mapped[int] = mapped_column(ForeignKey("tutorials.id"))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
