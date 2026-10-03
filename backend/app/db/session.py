from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from alembic.config import Config
from alembic import command

ROOT = Path(__file__).resolve().parents[2]


def make_engine(data_root):
    data_root = Path(data_root).resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    engine = create_engine("sqlite:///" + (data_root / "crowd_qc.sqlite").as_posix(), connect_args={"check_same_thread": False, "timeout": 30})

    @event.listens_for(engine, "connect")
    def settings(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
    return engine


def migrate(engine):
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


def sessions(engine):
    return sessionmaker(engine, expire_on_commit=False)
