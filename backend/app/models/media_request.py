from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UserScoped

REQUEST_STATUSES = ("pending", "sent", "declined", "available")


class MediaRequest(UserScoped, Base):
    """Demande d'ajout d'un titre à la bibliothèque Plex.

    - Acceptée (par l'administrateur, ou directement pour un compte qu'il autorise) : envoyée à Radarr / Sonarr,
      sinon à Seerr (statut « sent », destination dans sent_to).
    - Sinon : reste « pending » ; l'administrateur la traite depuis SynCiné.
    """

    __tablename__ = "media_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    tmdb_id: Mapped[int] = mapped_column(Integer, index=True)
    media_type: Mapped[str] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(String, default="")
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    poster: Mapped[str | None] = mapped_column(String, nullable=True)
    requested_by: Mapped[str] = mapped_column(String, default="admin")  # identifiant de compte (multi-comptes à venir)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    seerr_url: Mapped[str | None] = mapped_column(String, nullable=True)
    sent_to: Mapped[str] = mapped_column(String(16), default="")  # "radarr" | "sonarr" | "seerr" une fois envoyée
    sent_category: Mapped[str] = mapped_column(String, default="")  # catégorie Radarr / Sonarr choisie
    note: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tmdb_id": self.tmdb_id,
            "media_type": self.media_type,
            "title": self.title,
            "year": self.year,
            "poster": self.poster,
            "requested_by": self.requested_by,
            "user_id": self.user_id,
            "status": self.status,
            "seerr_url": self.seerr_url,
            "sent_to": self.sent_to or ("seerr" if self.status == "sent" else ""),
            "sent_category": self.sent_category or "",
            "note": self.note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
