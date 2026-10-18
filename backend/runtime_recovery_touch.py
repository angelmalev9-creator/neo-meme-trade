"""Deployment-only recovery marker.

This module is intentionally not imported. Its presence causes the VPS pull-based
deployer to classify the commit as a backend runtime change and safely restart
the dedicated PAPER engine after archiving its ledger.
"""

RECOVERY_REVISION = "2026-10-18_HTTP_502_ENGINE_RECOVERY"
