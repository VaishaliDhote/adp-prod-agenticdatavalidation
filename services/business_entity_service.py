# ============================================================================
# MODIFICATION REQUIRED LATER
# Deliverable 10 - business entity CRUD service. REUSABLE.
# Rework: tie entity records to stored configuration versions / UUID mappings.
# ============================================================================
"""
Business Entity Service

Handles CRUD operations for Business Units, Platforms, and Categories.
"""
from __future__ import annotations

import logging
from typing import Optional

from storage.dynamodb.business_repository import (
    fetch_all_bus,
    fetch_platforms,
    fetch_categories,
    insert_category,
    delete_category,
    validate_combo,
)

logger = logging.getLogger(__name__)


class BusinessEntityService:
    """Service for managing business entities (BU, Platform, Category)."""
    
    def get_all_business_units(self) -> list[str]:
        """Fetch all business units."""
        return fetch_all_bus()
    
    def get_platforms(self, business_unit: str) -> list[str]:
        """Fetch platforms for a business unit."""
        return fetch_platforms(business_unit)
    
    def get_categories(self, business_unit: str, platform: str) -> list[str]:
        """Fetch categories for a business unit and platform."""
        return fetch_categories(business_unit, platform)
    
    def validate_combination(
        self,
        business_unit: str,
        platform: str,
        category: str,
    ) -> bool:
        """
        Validate that the BU/platform/category combination exists and is active.
        
        Returns:
            True if valid, False otherwise
        """
        return validate_combo(business_unit, platform, category)
    
    def create_category(
        self,
        business_unit: str,
        platform: str,
        category: str,
    ) -> None:
        """
        Create a new category.
        
        Raises:
            ValueError: If combination already exists or is invalid
        """
        # Validate the combo first
        validate_combo(business_unit, platform, category)
        insert_category(business_unit, platform, category)
        logger.info(f"Created category: {business_unit}/{platform}/{category}")
    
    def remove_category(
        self,
        business_unit: str,
        platform: str,
        category: str,
    ) -> None:
        """
        Delete a category.
        
        Raises:
            ValueError: If combination doesn't exist
        """
        validate_combo(business_unit, platform, category)
        delete_category(business_unit, platform, category)
        logger.info(f"Deleted category: {business_unit}/{platform}/{category}")
