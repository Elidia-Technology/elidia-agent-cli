import asyncio
import hashlib
import logging
from pathlib import Path

from elidia.rag.engine import RagEngine
from elidia.rag.ingest import FileIngestPipeline

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 30
DEBOUNCE_SECONDS = 2


class FileWatcher:
    """Watches a directory for file changes and re-indexes incrementally."""

    def __init__(
        self,
        rag: RagEngine,
        pipeline: FileIngestPipeline,
        project_path: str = "",
    ) -> None:
        logger.debug(f"Entered into FileWatcher.__init__: project_path={project_path}")
        self._rag = rag
        self._pipeline = pipeline
        self._project_path = project_path
        self._file_hashes: dict[str, str] = {}
        self._running = False

    async def scan_once(self, directory: Path) -> dict[str, int]:
        logger.debug(f"Entered into scan_once: dir={directory}")
        directory = directory.resolve()
        new_hashes: dict[str, str] = {}
        changed_files: list[Path] = []
        deleted_files: list[str] = []

        for file_path in self._pipeline._walk_files(directory):
            if self._pipeline._should_skip(file_path):
                continue

            str_path = str(file_path)
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                file_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
                new_hashes[str_path] = file_hash

                if str_path not in self._file_hashes or self._file_hashes[str_path] != file_hash:
                    changed_files.append(file_path)
            except Exception as e:
                logger.debug(f"Could not hash {file_path}: {e}")

        for old_path in self._file_hashes:
            if old_path not in new_hashes:
                deleted_files.append(old_path)

        re_indexed = 0
        re_chunked = 0
        removed = 0

        for del_path in deleted_files:
            count = self._rag.delete_source(del_path)
            removed += count
            logger.info(f"Removed {count} chunks for deleted file: {del_path}")

        for changed_path in changed_files:
            self._rag.delete_source(str(changed_path))
            ids = await self._pipeline.ingest_file(
                changed_path,
                project_path=self._project_path or str(directory),
            )
            if ids:
                re_indexed += 1
                re_chunked += len(ids)

        self._file_hashes = new_hashes

        return {"changed": re_indexed, "chunks": re_chunked, "removed": removed}

    async def watch(self, directory: Path, interval: int = DEFAULT_POLL_INTERVAL) -> None:
        logger.debug(f"Entered into watch: dir={directory}, interval={interval}")
        self._running = True

        initial = await self.scan_once(directory)
        logger.info(f"Initial scan: {initial}")

        while self._running:
            await asyncio.sleep(interval)
            try:
                result = await self.scan_once(directory)
                if result["changed"] > 0 or result["removed"] > 0:
                    logger.info(f"Watcher update: {result}")
            except Exception as e:
                logger.warning(f"Watcher scan error: {e}")

    def stop(self) -> None:
        logger.debug("Entered into FileWatcher.stop")
        self._running = False
