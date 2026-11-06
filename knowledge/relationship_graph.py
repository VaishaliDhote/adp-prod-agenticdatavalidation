# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 10 - BU/Platform/Category lookup -> retrieve applicable JSON (task 17). REUSABLE.
# ============================================================================
"""
knowledge/relationship_graph.py
---------------------------------
Stores and queries the BU → Platform → Category hierarchy as a relationship graph.
Backed by DynamoDB — no separate graph DB needed at this scale.

Delegates all reads/writes to storage.dynamodb.business_repository.
Provides higher-level graph traversal helpers used by knowledge layer consumers.
"""
from __future__ import annotations

from storage.dynamodb.business_repository import (
    fetch_all_bus,
    fetch_platforms,
    fetch_categories,
    insert_bu,
    insert_platform,
    insert_category,
    delete_bu,
    delete_platform,
    delete_category,
)


def get_full_hierarchy() -> dict:
    """
    Returns the complete BU → Platform → Category tree as a nested dict.
    {
        "Finance": {
            "SAP": ["Invoices", "Payroll"],
            "Oracle": ["GL"]
        },
        ...
    }
    """
    hierarchy = {}
    for bu in fetch_all_bus():
        hierarchy[bu] = {}
        for platform in fetch_platforms(bu):
            hierarchy[bu][platform] = fetch_categories(bu, platform)
    return hierarchy


def get_platforms_for_bu(bu: str) -> list[str]:
    return fetch_platforms(bu)


def get_categories_for_scope(bu: str, platform: str) -> list[str]:
    return fetch_categories(bu, platform)


def scope_exists(bu: str, platform: str, category: str) -> bool:
    """Check if a full BU/Platform/Category combination is valid and active."""
    return category in fetch_categories(bu, platform)


def add_node(bu: str, platform: str = None, category: str = None) -> None:
    """
    Add a node to the hierarchy.
    - bu only          → adds BU
    - bu + platform    → adds Platform under BU
    - bu + platform + category → adds Category under BU/Platform
    """
    if bu and not platform and not category:
        insert_bu(bu)
    elif bu and platform and not category:
        insert_platform(bu, platform)
    elif bu and platform and category:
        insert_category(bu, platform, category)
    else:
        raise ValueError("Provide bu alone, bu+platform, or bu+platform+category")


def remove_node(bu: str, platform: str = None, category: str = None) -> None:
    """
    Soft-delete a node and cascade to children.
    - bu only          → soft-deletes BU + all platforms + all categories
    - bu + platform    → soft-deletes Platform + all its categories
    - bu + platform + category → soft-deletes Category only
    """
    if bu and not platform and not category:
        delete_bu(bu)
    elif bu and platform and not category:
        delete_platform(bu, platform)
    elif bu and platform and category:
        delete_category(bu, platform, category)
    else:
        raise ValueError("Provide bu alone, bu+platform, or bu+platform+category")
