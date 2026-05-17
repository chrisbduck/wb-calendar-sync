from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker

from app.config import database_url


connect_args = {"check_same_thread": False} if database_url().startswith("sqlite") else {}
engine = create_engine(database_url(), pool_pre_ping=True, future=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
db_session = scoped_session(SessionLocal)
Base = declarative_base()
Base.query = db_session.query_property()


def init_db():
	import app.models
	Base.metadata.create_all(bind=engine)
