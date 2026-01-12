"""
Storage functions for CRM tracker items.

Handles CRUD operations for the Kanban-style deal pipeline tracker.
"""

import logging
from datetime import datetime, date, timezone
from typing import Optional, List

from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from .models import TrackerItem, Deal, PortfolioCompany

logger = logging.getLogger(__name__)


# Valid status values for Kanban columns
VALID_STATUSES = {"watching", "reached_out", "in_conversation", "closing_spv", "spv_complete", "spv_rejected"}


async def get_tracker_items(
    session: AsyncSession,
    status: Optional[str] = None,
) -> List[TrackerItem]:
    """
    Get all tracker items, optionally filtered by status.

    Items are ordered by status (for grouping) then by position (for ordering within column).

    Args:
        session: Database session
        status: Optional status filter (watching, reached_out, in_conversation, closing_spv)

    Returns:
        List of TrackerItem records
    """
    stmt = select(TrackerItem).order_by(TrackerItem.status, TrackerItem.position)

    if status:
        if status not in VALID_STATUSES:
            logger.warning(f"Invalid status filter: {status}")
            return []
        stmt = stmt.where(TrackerItem.status == status)

    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_tracker_item(
    session: AsyncSession,
    item_id: int,
) -> Optional[TrackerItem]:
    """
    Get a single tracker item by ID.

    Args:
        session: Database session
        item_id: The tracker item ID

    Returns:
        TrackerItem if found, None otherwise
    """
    stmt = select(TrackerItem).where(TrackerItem.id == item_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def create_tracker_item(
    session: AsyncSession,
    company_name: str,
    status: str = "watching",
    round_type: Optional[str] = None,
    amount: Optional[str] = None,
    lead_investor: Optional[str] = None,
    website: Optional[str] = None,
    notes: Optional[str] = None,
    deal_id: Optional[int] = None,
) -> TrackerItem:
    """
    Create a new tracker item.

    Automatically assigns position at the end of the target column.

    Args:
        session: Database session
        company_name: Name of the company
        status: Initial Kanban column (default: watching)
        round_type: Funding round type (seed, series_a, etc.)
        amount: Funding amount ("$50M")
        lead_investor: Lead investor name
        website: Company website URL
        notes: User notes
        deal_id: Optional link to existing Deal record

    Returns:
        Created TrackerItem
    """
    if status not in VALID_STATUSES:
        logger.warning(f"Invalid status '{status}', defaulting to 'watching'")
        status = "watching"

    # Get max position in target column to append at end
    stmt = (
        select(func.max(TrackerItem.position))
        .where(TrackerItem.status == status)
    )
    result = await session.execute(stmt)
    max_pos = result.scalar_one_or_none() or -1

    item = TrackerItem(
        company_name=company_name,
        status=status,
        round_type=round_type,
        amount=amount,
        lead_investor=lead_investor,
        website=website,
        notes=notes,
        deal_id=deal_id,
        position=max_pos + 1,
    )
    session.add(item)
    await session.flush()

    logger.info(f"Created tracker item: {company_name} in '{status}' at position {item.position}")
    return item


async def update_tracker_item(
    session: AsyncSession,
    item_id: int,
    **kwargs,
) -> Optional[TrackerItem]:
    """
    Update a tracker item's fields.

    Does NOT handle status changes (use move_tracker_item for that).

    Args:
        session: Database session
        item_id: The tracker item ID
        **kwargs: Fields to update (company_name, notes, last_contact_date, next_step, etc.)

    Returns:
        Updated TrackerItem if found, None otherwise
    """
    stmt = select(TrackerItem).where(TrackerItem.id == item_id)
    result = await session.execute(stmt)
    item = result.scalar_one_or_none()

    if not item:
        logger.warning(f"Tracker item not found: {item_id}")
        return None

    # Update allowed fields
    allowed_fields = {
        "company_name", "round_type", "amount", "lead_investor",
        "website", "notes", "last_contact_date", "next_step"
    }

    for key, value in kwargs.items():
        if key in allowed_fields and hasattr(item, key):
            setattr(item, key, value)

    item.updated_at = datetime.now(timezone.utc)
    await session.flush()

    logger.info(f"Updated tracker item: {item_id}")
    return item


async def move_tracker_item(
    session: AsyncSession,
    item_id: int,
    new_status: str,
    new_position: int,
) -> Optional[TrackerItem]:
    """
    Move a tracker item to a new status/position (for drag-drop).

    Handles reordering of other items in both source and target columns.

    Args:
        session: Database session
        item_id: The tracker item ID
        new_status: Target Kanban column
        new_position: Target position within column (0-indexed)

    Returns:
        Moved TrackerItem if found, None otherwise
    """
    if new_status not in VALID_STATUSES:
        logger.warning(f"Invalid status: {new_status}")
        return None

    stmt = select(TrackerItem).where(TrackerItem.id == item_id)
    result = await session.execute(stmt)
    item = result.scalar_one_or_none()

    if not item:
        logger.warning(f"Tracker item not found: {item_id}")
        return None

    old_status = item.status
    old_position = item.position

    # If moving within same column
    if old_status == new_status:
        if old_position == new_position:
            # No change needed
            return item

        if old_position < new_position:
            # Moving down: shift items between old and new positions up
            await session.execute(
                update(TrackerItem)
                .where(TrackerItem.status == old_status)
                .where(TrackerItem.position > old_position)
                .where(TrackerItem.position <= new_position)
                .values(position=TrackerItem.position - 1)
            )
        else:
            # Moving up: shift items between new and old positions down
            await session.execute(
                update(TrackerItem)
                .where(TrackerItem.status == old_status)
                .where(TrackerItem.position >= new_position)
                .where(TrackerItem.position < old_position)
                .values(position=TrackerItem.position + 1)
            )
    else:
        # Moving to different column

        # Close gap in old column
        await session.execute(
            update(TrackerItem)
            .where(TrackerItem.status == old_status)
            .where(TrackerItem.position > old_position)
            .values(position=TrackerItem.position - 1)
        )

        # Make room in new column
        await session.execute(
            update(TrackerItem)
            .where(TrackerItem.status == new_status)
            .where(TrackerItem.position >= new_position)
            .values(position=TrackerItem.position + 1)
        )

    # Update the item itself
    item.status = new_status
    item.position = new_position
    item.updated_at = datetime.now(timezone.utc)

    await session.flush()

    logger.info(
        f"Moved tracker item {item_id}: '{old_status}'[{old_position}] -> '{new_status}'[{new_position}]"
    )
    return item


async def delete_tracker_item(
    session: AsyncSession,
    item_id: int,
) -> bool:
    """
    Delete a tracker item.

    Reorders remaining items in the column to close the gap.

    Args:
        session: Database session
        item_id: The tracker item ID

    Returns:
        True if deleted, False if not found
    """
    stmt = select(TrackerItem).where(TrackerItem.id == item_id)
    result = await session.execute(stmt)
    item = result.scalar_one_or_none()

    if not item:
        logger.warning(f"Tracker item not found for deletion: {item_id}")
        return False

    status = item.status
    position = item.position

    # Delete the item
    await session.delete(item)

    # Close gap in column
    await session.execute(
        update(TrackerItem)
        .where(TrackerItem.status == status)
        .where(TrackerItem.position > position)
        .values(position=TrackerItem.position - 1)
    )

    await session.flush()

    logger.info(f"Deleted tracker item: {item_id}")
    return True


async def create_tracker_from_deal(
    session: AsyncSession,
    deal_id: int,
    status: str = "watching",
) -> Optional[TrackerItem]:
    """
    Create a tracker item from an existing deal.

    Copies deal info (company name, round, amount, lead investor, website)
    and links the tracker item to the deal.

    Args:
        session: Database session
        deal_id: The Deal ID to create tracker from
        status: Initial Kanban column (default: watching)

    Returns:
        Created TrackerItem, or None if:
        - Deal not found
        - Deal already tracked (deal_id already in tracker_items)
    """
    # Check if deal is already tracked
    existing_stmt = select(TrackerItem).where(TrackerItem.deal_id == deal_id)
    existing_result = await session.execute(existing_stmt)
    if existing_result.scalar_one_or_none():
        logger.warning(f"Deal {deal_id} is already tracked")
        return None

    # Get deal with company info
    deal_stmt = select(Deal).where(Deal.id == deal_id)
    deal_result = await session.execute(deal_stmt)
    deal = deal_result.scalar_one_or_none()

    if not deal:
        logger.warning(f"Deal not found: {deal_id}")
        return None

    # Get company name
    company_stmt = select(PortfolioCompany).where(PortfolioCompany.id == deal.company_id)
    company_result = await session.execute(company_stmt)
    company = company_result.scalar_one_or_none()

    if not company:
        logger.warning(f"Company not found for deal: {deal_id}")
        return None

    # Create tracker item with deal info
    return await create_tracker_item(
        session=session,
        company_name=company.name,
        status=status,
        round_type=deal.round_type,
        amount=deal.amount,
        lead_investor=deal.lead_partner_name,
        website=company.website,
        deal_id=deal_id,
    )


async def get_tracker_items_count(
    session: AsyncSession,
    status: Optional[str] = None,
) -> int:
    """
    Get count of tracker items, optionally filtered by status.

    Args:
        session: Database session
        status: Optional status filter

    Returns:
        Count of matching items
    """
    stmt = select(func.count(TrackerItem.id))

    if status:
        if status not in VALID_STATUSES:
            return 0
        stmt = stmt.where(TrackerItem.status == status)

    result = await session.execute(stmt)
    return result.scalar_one() or 0


async def get_tracker_stats(
    session: AsyncSession,
) -> dict:
    """
    Get statistics for the tracker dashboard.

    Returns:
        Dict with counts per status and total
    """
    stats = {"total": 0}

    for status in VALID_STATUSES:
        count = await get_tracker_items_count(session, status)
        stats[status] = count
        stats["total"] += count

    return stats
