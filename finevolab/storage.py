"""Index durable analysis tasks in the upstream database."""

from datetime import datetime, timezone
import json

from sqlalchemy import Column, DateTime, String, Text
from finrobot_equity.web_app.database.connection import SessionLocal, engine
from finrobot_equity.web_app.database.models import Base


class AnalysisTask(Base):
    __tablename__ = "finevolab_tasks"
    task_id = Column(String(32), primary_key=True)
    payload = Column(Text, nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


def save_task(state: dict) -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        session.merge(AnalysisTask(task_id=state["task_id"], payload=json.dumps(state, ensure_ascii=False),
                                   updated_at=datetime.now(timezone.utc)))
        session.commit()


def recent_tasks(limit: int = 20) -> list[dict]:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        rows = session.query(AnalysisTask).order_by(AnalysisTask.updated_at.desc()).limit(limit).all()
        return [json.loads(row.payload) for row in rows]
