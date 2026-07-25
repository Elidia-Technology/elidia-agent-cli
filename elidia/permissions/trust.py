import logging
from dataclasses import dataclass

from elidia.config.settings import PermissionConfig

logger = logging.getLogger(__name__)


@dataclass
class TrustStats:
    action: str
    approved_count: int = 0
    denied_count: int = 0
    auto_promoted: bool = False


class TrustEngine:
    """Tracks approval patterns and auto-promotes frequently-approved actions."""

    def __init__(self, config: PermissionConfig) -> None:
        logger.debug("Entered into TrustEngine.__init__")
        self._config = config
        self._stats: dict[str, TrustStats] = {}
        self._promoted: set[str] = set()

    def record_decision(self, action: str, approved: bool) -> None:
        logger.debug(f"Entered into record_decision: action={action}, approved={approved}")
        if not self._config.progressive_trust:
            return

        if action not in self._stats:
            self._stats[action] = TrustStats(action=action)

        stats = self._stats[action]
        if approved:
            stats.approved_count += 1
        else:
            stats.denied_count += 1

        threshold = self._config.trust_threshold
        if (
            stats.approved_count >= threshold
            and stats.denied_count == 0
            and not stats.auto_promoted
        ):
            stats.auto_promoted = True
            self._promoted.add(action)
            logger.info(
                f"Trust promoted '{action}' to auto-approve after {stats.approved_count} approvals"
            )

    def is_promoted(self, action: str) -> bool:
        logger.debug(f"Entered into is_promoted: action={action}")
        return action in self._promoted

    def get_stats(self) -> dict[str, TrustStats]:
        logger.debug("Entered into get_stats")
        return dict(self._stats)

    def reset(self) -> None:
        logger.debug("Entered into reset")
        self._stats.clear()
        self._promoted.clear()
