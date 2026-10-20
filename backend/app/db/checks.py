from pathlib import Path

from sqlalchemy import Connection, text

from app.core.catalog import load_llm_routing


class EmbeddingDimMismatch(RuntimeError):
    pass


def db_embedding_dim(conn: Connection) -> int | None:
    """Dimension of signals.embedding in the DB, or None if the table doesn't exist yet."""
    return conn.execute(
        text(
            "SELECT atttypmod FROM pg_attribute "
            "WHERE attrelid = to_regclass('signals') AND attname = 'embedding'"
        )
    ).scalar()


def assert_embedding_dim_matches_db(conn: Connection, config_dir: Path | None = None) -> None:
    """Fail fast if config/llm_routing.yaml and the migrated column disagree."""
    configured = load_llm_routing(config_dir).embedding.dimensions
    actual = db_embedding_dim(conn)
    if actual is None:
        raise EmbeddingDimMismatch("signals.embedding not found; run `make migrate` first")
    if actual != configured:
        raise EmbeddingDimMismatch(
            f"llm_routing.yaml embedding.dimensions={configured} but the DB column is "
            f"vector({actual}). "
            "Changing the dimension needs a migration and a full re-embed."
        )
