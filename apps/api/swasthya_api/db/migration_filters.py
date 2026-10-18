"""What Alembic autogenerate compares. Shared by migrations/env.py and tests."""

# LangGraph's checkpoint tables come from the library's own SQL (migration
# 0004) and are deliberately not modelled in SQLAlchemy.
CHECKPOINT_TABLES = frozenset(
    {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}
)


def include_object(obj, name, type_, reflected, compare_to):  # noqa: ANN001
    if type_ == "table" and name in CHECKPOINT_TABLES:
        return False
    table = getattr(obj, "table", None)
    if type_ == "index" and table is not None:
        return table.name not in CHECKPOINT_TABLES
    return True
