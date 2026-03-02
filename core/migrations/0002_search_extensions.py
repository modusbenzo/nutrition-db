"""
Enable PostgreSQL extensions required for full-text search and trigram similarity.

- pg_trgm: fuzzy matching with trigram similarity
- btree_gin: allows GIN indexes on standard data types
"""

from django.contrib.postgres.operations import (
    BtreeGinExtension,
    TrigramExtension,
)
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        TrigramExtension(),
        BtreeGinExtension(),
    ]
