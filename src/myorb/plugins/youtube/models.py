from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from myorb.db import Base


class SeenVideo(Base):
    """A video the user has watched or marked as seen in myOrb; YouTube does not tell."""

    __tablename__ = "seen_videos"

    video_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
