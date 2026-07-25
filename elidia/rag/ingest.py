import hashlib
import logging
import mimetypes
from pathlib import Path

from elidia.rag.engine import RagEngine

logger = logging.getLogger(__name__)

CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".c", ".cpp", ".h",
    ".rb", ".php", ".swift", ".kt", ".scala", ".sh", ".bash", ".zsh",
    ".sql", ".yaml", ".yml", ".toml", ".json", ".xml", ".html", ".css", ".scss",
}

TEXT_EXTENSIONS = {
    ".txt", ".md", ".rst", ".org", ".tex", ".log", ".csv", ".tsv",
    ".cfg", ".ini", ".conf", ".env.example", ".gitignore", ".dockerignore",
    ".makefile", ".dockerfile",
}

SKIP_DIRS = {
    ".git", ".svn", ".hg", "__pycache__", "node_modules", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".next", ".nuxt", ".cache", "coverage", ".idea", ".vscode",
}

MAX_FILE_SIZE = 1024 * 1024  # 1MB


class FileIngestPipeline:
    """Ingests files from disk into the RAG engine."""

    def __init__(self, rag: RagEngine) -> None:
        logger.debug("Entered into FileIngestPipeline.__init__")
        self._rag = rag

    async def ingest_file(
        self,
        path: Path,
        project_path: str = "",
        chunk_size: int = 512,
    ) -> list[str]:
        logger.debug(f"Entered into ingest_file: path={path}")
        path = path.resolve()

        if not path.exists():
            logger.warning(f"File not found: {path}")
            return []
        if not path.is_file():
            logger.warning(f"Not a file: {path}")
            return []
        if path.stat().st_size > MAX_FILE_SIZE:
            logger.warning(f"File too large ({path.stat().st_size} bytes): {path}")
            return []

        content_type = self._detect_content_type(path)
        if content_type is None:
            logger.info(f"Skipping unsupported file type: {path}")
            return []

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.warning(f"Could not read {path}: {e}")
            return []

        if not text.strip():
            return []

        file_hash = hashlib.sha256(text.encode()).hexdigest()[:16]

        return await self._rag.ingest(
            text=text,
            source=str(path),
            content_type=content_type,
            project_path=project_path,
            file_hash=file_hash,
            chunk_size=chunk_size,
        )

    async def ingest_directory(
        self,
        directory: Path,
        project_path: str = "",
        chunk_size: int = 512,
        recursive: bool = True,
    ) -> dict[str, int]:
        logger.debug(f"Entered into ingest_directory: dir={directory}, recursive={recursive}")
        directory = directory.resolve()

        if not directory.exists() or not directory.is_dir():
            return {"files": 0, "chunks": 0, "skipped": 0}

        files_ingested = 0
        total_chunks = 0
        skipped = 0

        if recursive:
            file_iter = self._walk_files(directory)
        else:
            file_iter = (f for f in sorted(directory.iterdir()) if f.is_file())

        for file_path in file_iter:
            if self._should_skip(file_path):
                skipped += 1
                continue

            ids = await self.ingest_file(
                file_path,
                project_path=project_path or str(directory),
                chunk_size=chunk_size,
            )
            if ids:
                files_ingested += 1
                total_chunks += len(ids)

        logger.info(f"Ingested {files_ingested} files, {total_chunks} chunks, skipped {skipped}")
        return {"files": files_ingested, "chunks": total_chunks, "skipped": skipped}

    def _walk_files(self, directory: Path):
        for item in sorted(directory.iterdir()):
            if item.is_dir():
                if item.name in SKIP_DIRS:
                    continue
                yield from self._walk_files(item)
            elif item.is_file():
                yield item

    def _should_skip(self, path: Path) -> bool:
        if any(part in SKIP_DIRS for part in path.parts):
            return True
        if path.name.startswith(".") and path.suffix not in {".env.example"}:
            return True
        if path.stat().st_size > MAX_FILE_SIZE:
            return True
        return self._detect_content_type(path) is None

    def _detect_content_type(self, path: Path) -> str | None:
        suffix = path.suffix.lower()
        name = path.name.lower()

        if suffix in CODE_EXTENSIONS or name in {"makefile", "dockerfile", "rakefile", "gemfile"}:
            return "code"
        if suffix in {".md", ".rst"}:
            return "markdown"
        if suffix in TEXT_EXTENSIONS:
            return "text"

        mime, _ = mimetypes.guess_type(str(path))
        if mime and mime.startswith("text/"):
            return "text"

        return None
