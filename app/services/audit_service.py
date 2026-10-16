"""
Audit service — fail-safe audit log writer.

Requirement 7.5: If the audit log write fails, the error MUST NOT propagate
to the caller. Instead, a structured entry is written to the Python logging
module so operators can detect failures without impacting the validation
response path.
"""

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)


async def write_audit_log(
    db: AsyncSession,
    voucher_code: str,
    client_mac: Optional[str],
    outcome: str,  # 'accepted' or 'rejected'
    rejection_reason: Optional[str],
    source: str = "rlm_rest",
) -> None:
    """
    Persist a single audit log entry and commit the session.

    Requirements 7.1, 7.2 — write an entry for every validation attempt
    regardless of outcome.

    Requirement 7.5 — any database failure is caught, logged at ERROR level
    with all relevant fields, and swallowed so the caller still receives its
    validation result.

    Args:
        db:               The async SQLAlchemy session to use.
        voucher_code:     The voucher code exactly as submitted.
        client_mac:       The client MAC address from the RADIUS request, or None.
        outcome:          Either 'accepted' or 'rejected'.
        rejection_reason: The reason for rejection, or None when accepted.
        source:           The integration path ('rlm_rest' or 'rlm_sql').
    """
    try:
        log_entry = AuditLog(
            voucher_code=voucher_code,
            client_mac=client_mac,
            outcome=outcome,
            rejection_reason=rejection_reason,
            source=source,
        )
        db.add(log_entry)
        await db.commit()
    except Exception as exc:
        # Requirement 7.5: log the failure without re-raising so the
        # validation response is always returned to the caller.
        logger.error(
            "Audit log write failed — entry not persisted",
            extra={
                "voucher_code": voucher_code,
                "client_mac": client_mac,
                "outcome": outcome,
                "rejection_reason": rejection_reason,
                "source": source,
                "error": str(exc),
            },
        )
