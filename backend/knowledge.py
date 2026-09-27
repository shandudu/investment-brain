"""Local, source-traceable knowledge store for Investment Brain.

The module deliberately keeps ingestion deterministic and read-only with respect
to source documents.  Source files are opened for reading only; all derived data
is written to SQLite.  The public functions are usable both from ``app.py`` and
from the small command-line interface at the bottom of this file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence


SUPPORTED_EXTENSIONS = frozenset({".docx", ".pdf", ".md", ".txt"})
DEFAULT_EXCLUDED_DIRECTORIES = frozenset(
    {
        ".git",
        ".agents",
        ".codex",
        ".pip-cache",
        ".transcribe",
        ".whisper-models",
        ".venv",
        "__pycache__",
        "node_modules",
        "pnpm-store",
        "pnpm-home",
        "dist",
        "build",
        "venv",
    }
)


@dataclass(frozen=True)
class DocumentBlock:
    """One source-addressable unit extracted from a document."""

    text: str
    locator: str
    block_type: str = "paragraph"
    heading_path: tuple[str, ...] = ()
    page_number: int | None = None
    paragraph_index: int | None = None
    table_index: int | None = None
    row_index: int | None = None

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["heading_path"] = list(self.heading_path)
        return value


@dataclass(frozen=True)
class DocumentExtraction:
    """Normalized output of a supported document parser."""

    path: str
    file_type: str
    title: str
    source_date: str
    blocks: tuple[DocumentBlock, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "\n\n".join(block.text for block in self.blocks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "file_type": self.file_type,
            "title": self.title,
            "source_date": self.source_date,
            "blocks": [block.as_dict() for block in self.blocks],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class KnowledgeChunk:
    """A retrieval-sized group of adjacent source blocks."""

    ordinal: int
    content: str
    start_locator: str
    end_locator: str
    block_type: str
    heading_path: tuple[str, ...]
    source_date: str = ""

    @property
    def locator(self) -> str:
        if self.start_locator == self.end_locator:
            return self.start_locator
        return f"{self.start_locator}..{self.end_locator}"

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["heading_path"] = list(self.heading_path)
        value["locator"] = self.locator
        return value


class UnsupportedDocumentError(ValueError):
    pass


def scan_documents(
    root: str | os.PathLike[str],
    extensions: Iterable[str] | None = None,
    exclude_directories: Iterable[str] | None = None,
) -> list[Path]:
    """Return supported source files below *root* in deterministic order.

    Directory symlinks are never followed.  Common application/build folders
    and Office lock files are ignored.  Merely scanning does not open or mutate
    any source file.
    """

    base = Path(root).expanduser()
    if not base.exists():
        raise FileNotFoundError(f"知识库目录不存在：{base}")
    allowed = {
        extension.lower() if str(extension).startswith(".") else f".{str(extension).lower()}"
        for extension in (extensions or SUPPORTED_EXTENSIONS)
    }
    excluded = set(DEFAULT_EXCLUDED_DIRECTORIES)
    if exclude_directories is not None:
        excluded.update(str(item) for item in exclude_directories)

    if base.is_file():
        if base.suffix.lower() in allowed and not base.name.startswith("~$"):
            return [base.resolve()]
        return []
    if not base.is_dir():
        raise ValueError(f"知识库路径不是普通目录或文件：{base}")

    found: list[Path] = []
    for current, directories, files in os.walk(base, topdown=True, followlinks=False):
        directories[:] = sorted(
            directory
            for directory in directories
            if directory not in excluded and not directory.startswith(".")
        )
        for filename in sorted(files):
            if filename.startswith("~$"):
                continue
            candidate = Path(current) / filename
            if candidate.suffix.lower() in allowed and candidate.is_file():
                found.append(candidate.resolve())
    return sorted(found, key=lambda item: str(item).casefold())


def sha256_file(path: str | os.PathLike[str], buffer_size: int = 1024 * 1024) -> str:
    """Hash a file without changing its timestamps or contents."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while True:
            part = source.read(buffer_size)
            if not part:
                break
            digest.update(part)
    return digest.hexdigest()


def extract_document(path: str | os.PathLike[str]) -> DocumentExtraction:
    """Extract text blocks and stable source locators from a supported file."""

    source = Path(path).expanduser().resolve()
    extension = source.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise UnsupportedDocumentError(f"暂不支持 {extension or '无扩展名'} 文件：{source.name}")
    if not source.is_file():
        raise FileNotFoundError(source)

    if extension == ".docx":
        title, blocks, metadata_date, metadata = _extract_docx(source)
    elif extension == ".pdf":
        title, blocks, metadata_date, metadata = _extract_pdf(source)
    else:
        title, blocks, metadata_date, metadata = _extract_text_document(source, extension)

    sample = "\n".join(block.text for block in blocks[:30])
    source_date = _detect_source_date(source, sample, metadata_date)
    if not title:
        title = next((block.text for block in blocks if block.block_type == "heading"), source.stem)
    return DocumentExtraction(
        path=str(source),
        file_type=extension.lstrip("."),
        title=_clean_text(title) or source.stem,
        source_date=source_date,
        blocks=tuple(blocks),
        metadata=metadata,
    )


def _extract_docx(
    path: Path,
) -> tuple[str, list[DocumentBlock], date | datetime | str | None, dict[str, Any]]:
    try:
        from docx import Document  # type: ignore
        from docx.table import Table  # type: ignore
        from docx.text.paragraph import Paragraph  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on installation
        raise RuntimeError("读取 Word 需要安装 python-docx") from exc

    document = Document(str(path))
    headings: list[str] = []
    blocks: list[DocumentBlock] = []
    paragraph_number = 0
    table_number = 0

    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph_number += 1
            paragraph = Paragraph(child, document)
            text = _clean_text(paragraph.text)
            if not text:
                continue
            level = _docx_heading_level(paragraph)
            block_type = "heading" if level is not None else "paragraph"
            if level is not None:
                _set_heading(headings, level, text)
            blocks.append(
                DocumentBlock(
                    text=text,
                    locator=f"docx:p:{paragraph_number:06d}",
                    block_type=block_type,
                    heading_path=tuple(headings),
                    paragraph_index=paragraph_number,
                )
            )
        elif tag == "tbl":
            table_number += 1
            table = Table(child, document)
            for row_number, row in enumerate(table.rows, start=1):
                values = [_clean_text(cell.text) for cell in row.cells]
                text = " | ".join(value for value in values if value)
                if not text:
                    continue
                blocks.append(
                    DocumentBlock(
                        text=text,
                        locator=f"docx:t:{table_number:04d}:r:{row_number:04d}",
                        block_type="table",
                        heading_path=tuple(headings),
                        table_index=table_number,
                        row_index=row_number,
                    )
                )

    properties = document.core_properties
    title = _clean_text(properties.title or "")
    metadata = {
        "author": _clean_text(properties.author or ""),
        "subject": _clean_text(properties.subject or ""),
        "created": _iso_value(properties.created),
        "modified": _iso_value(properties.modified),
    }
    return title, blocks, properties.created, metadata


def _docx_heading_level(paragraph: Any) -> int | None:
    style_name = _clean_text(getattr(getattr(paragraph, "style", None), "name", "") or "")
    match = re.search(r"(?:Heading|标题)\s*([1-9])", style_name, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    try:
        outline = paragraph._p.pPr.outlineLvl
        if outline is not None:
            return int(outline.val) + 1
    except (AttributeError, TypeError, ValueError):
        pass
    return None


def _extract_pdf(
    path: Path,
) -> tuple[str, list[DocumentBlock], date | datetime | str | None, dict[str, Any]]:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on installation
        raise RuntimeError("读取 PDF 需要安装 pypdf") from exc

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:
            raise RuntimeError(f"PDF 已加密，无法读取：{path.name}") from exc
    raw_metadata = reader.metadata or {}
    title = _clean_text(_metadata_value(raw_metadata, "/Title"))
    created = _metadata_value(raw_metadata, "/CreationDate")
    headings: list[str] = []
    blocks: list[DocumentBlock] = []
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:
            raise RuntimeError(f"PDF 第 {page_number} 页解析失败：{exc}") from exc
        groups = _pdf_text_groups(page_text)
        for block_number, text in enumerate(groups, start=1):
            level = _plain_heading_level(text)
            block_type = "heading" if level is not None else ("table" if _looks_like_table(text) else "paragraph")
            if level is not None:
                _set_heading(headings, level, text)
            blocks.append(
                DocumentBlock(
                    text=text,
                    locator=f"pdf:p:{page_number:05d}:b:{block_number:05d}",
                    block_type=block_type,
                    heading_path=tuple(headings),
                    page_number=page_number,
                    paragraph_index=block_number,
                )
            )
    metadata = {
        "pages": len(reader.pages),
        "author": _clean_text(_metadata_value(raw_metadata, "/Author")),
        "created": created,
    }
    return title, blocks, created, metadata


def _metadata_value(metadata: Any, key: str) -> str:
    try:
        value = metadata.get(key, "")
    except AttributeError:
        return ""
    return str(value or "")


def _pdf_text_groups(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = [_clean_text(part) for part in re.split(r"\n\s*\n", normalized)]
    if len([item for item in paragraphs if item]) > 1:
        return [item for item in paragraphs if item]
    # Many PDFs do not retain blank lines.  Line-level locators are less pretty,
    # but are deterministic and much safer than inventing paragraph boundaries.
    return [_clean_text(line) for line in normalized.splitlines() if _clean_text(line)]


def _extract_text_document(
    path: Path, extension: str
) -> tuple[str, list[DocumentBlock], str | None, dict[str, Any]]:
    text, encoding = _read_text(path)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    title = ""
    frontmatter_date: str | None = None
    start_line = 0
    if extension == ".md" and lines and lines[0].strip() == "---":
        for index in range(1, min(len(lines), 100)):
            if lines[index].strip() == "---":
                values = _parse_simple_frontmatter(lines[1:index])
                title = values.get("title", "").strip(" \"'")
                frontmatter_date = values.get("date", "").strip(" \"'") or None
                start_line = index + 1
                break

    blocks: list[DocumentBlock] = []
    headings: list[str] = []
    paragraph_lines: list[str] = []
    paragraph_start = 0

    def flush_paragraph(end_line: int) -> None:
        nonlocal paragraph_lines, paragraph_start
        value = _clean_text(" ".join(part.strip() for part in paragraph_lines))
        if value:
            locator = f"{extension.lstrip('.')}:l:{paragraph_start:06d}-{end_line:06d}"
            block_type = "table" if _looks_like_table(value) else "paragraph"
            blocks.append(
                DocumentBlock(
                    text=value,
                    locator=locator,
                    block_type=block_type,
                    heading_path=tuple(headings),
                    paragraph_index=paragraph_start,
                )
            )
        paragraph_lines = []
        paragraph_start = 0

    index = start_line
    while index < len(lines):
        raw = lines[index]
        line_number = index + 1
        stripped = raw.strip()
        markdown_heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", stripped) if extension == ".md" else None
        setext = (
            extension == ".md"
            and stripped
            and index + 1 < len(lines)
            and re.fullmatch(r"\s*(=+|-+)\s*", lines[index + 1]) is not None
        )
        plain_level = _plain_heading_level(stripped) if extension == ".txt" else None
        if markdown_heading or setext or plain_level is not None:
            flush_paragraph(line_number - 1)
            if markdown_heading:
                level = len(markdown_heading.group(1))
                heading_text = _clean_text(markdown_heading.group(2))
                locator = f"md:l:{line_number:06d}"
            elif setext:
                level = 1 if lines[index + 1].strip().startswith("=") else 2
                heading_text = _clean_text(stripped)
                locator = f"md:l:{line_number:06d}-{line_number + 1:06d}"
                index += 1
            else:
                level = plain_level or 1
                heading_text = _clean_text(stripped.rstrip("：:"))
                locator = f"txt:l:{line_number:06d}"
            _set_heading(headings, level, heading_text)
            blocks.append(
                DocumentBlock(
                    text=heading_text,
                    locator=locator,
                    block_type="heading",
                    heading_path=tuple(headings),
                    paragraph_index=line_number,
                )
            )
            if not title and level == 1:
                title = heading_text
        elif not stripped:
            flush_paragraph(line_number - 1)
        elif extension == ".md" and _looks_like_markdown_table_row(stripped):
            flush_paragraph(line_number - 1)
            if not _is_markdown_table_separator(stripped):
                blocks.append(
                    DocumentBlock(
                        text=_clean_text(stripped.strip("|")),
                        locator=f"md:l:{line_number:06d}",
                        block_type="table",
                        heading_path=tuple(headings),
                        table_index=line_number,
                        row_index=line_number,
                    )
                )
        else:
            if not paragraph_lines:
                paragraph_start = line_number
            paragraph_lines.append(stripped)
        index += 1
    flush_paragraph(len(lines))
    return title, blocks, frontmatter_date, {"encoding": encoding}


def _read_text(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


def _parse_simple_frontmatter(lines: Sequence[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*?)\s*$", line)
        if match:
            values[match.group(1).lower()] = match.group(2)
    return values


def _plain_heading_level(text: str) -> int | None:
    if not text or len(text) > 100 or "\n" in text:
        return None
    if re.match(r"^第[一二三四五六七八九十百零〇0-9]+[章节篇部]", text):
        return 1
    match = re.match(r"^(\d+(?:\.\d+){0,5})[、.．)）\s]+\S", text)
    if match:
        return min(match.group(1).count(".") + 1, 6)
    if re.match(r"^[一二三四五六七八九十]+[、.．]\s*\S", text):
        return 2
    return None


def _looks_like_markdown_table_row(text: str) -> bool:
    return text.count("|") >= 2


def _is_markdown_table_separator(text: str) -> bool:
    cells = [cell.strip() for cell in text.strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _looks_like_table(text: str) -> bool:
    return text.count("|") >= 2 or ("\t" in text and len(text.split("\t")) >= 3)


def _set_heading(headings: list[str], level: int, text: str) -> None:
    level = max(1, min(int(level), 9))
    del headings[level - 1 :]
    while len(headings) < level - 1:
        headings.append("")
    headings.append(text)


def _clean_text(value: Any) -> str:
    text = str(value or "").replace("\u00a0", " ").replace("\x00", "")
    return re.sub(r"[ \t\f\v]+", " ", text).strip()


def _iso_value(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value or "")


_DATE_PATTERNS = (
    re.compile(r"(?<!\d)(20\d{2})[-/.年](0?[1-9]|1[0-2])[-/.月](0?[1-9]|[12]\d|3[01])日?(?!\d)"),
    re.compile(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)"),
)


def _parse_date(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    pdf_match = re.search(r"D:(20\d{2})(\d{2})(\d{2})", text)
    if pdf_match:
        text = "-".join(pdf_match.groups())
    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                return date(*(int(item) for item in match.groups())).isoformat()
            except ValueError:
                continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except (ValueError, TypeError):
        return None


def _detect_source_date(path: Path, sample: str, metadata_date: Any = None) -> str:
    # A date intentionally encoded in the filename is generally a publication
    # date and therefore more meaningful than an Office copy timestamp.
    for candidate in (path.stem, sample[:3000], metadata_date):
        parsed = _parse_date(candidate)
        if parsed:
            return parsed
    return datetime.fromtimestamp(path.stat().st_mtime).date().isoformat()


def chunk_blocks(
    blocks: Sequence[DocumentBlock],
    max_chars: int = 1600,
    overlap_blocks: int = 1,
    source_date: str = "",
    **options: Any,
) -> list[KnowledgeChunk]:
    """Group adjacent blocks while retaining exact start/end locators.

    ``chunk_size`` is accepted as an alias for callers that prefer that name.
    Long individual blocks are split deterministically and receive ``#part``
    locator suffixes.
    """

    if "chunk_size" in options:
        max_chars = int(options["chunk_size"])
    if max_chars < 100:
        raise ValueError("max_chars 不能小于 100")
    overlap_blocks = max(0, int(overlap_blocks))
    normalized: list[DocumentBlock] = []
    for block in blocks:
        if len(block.text) <= max_chars:
            normalized.append(block)
            continue
        parts = _split_long_text(block.text, max_chars)
        for part_number, part in enumerate(parts, start=1):
            normalized.append(
                DocumentBlock(
                    text=part,
                    locator=f"{block.locator}#part={part_number:03d}",
                    block_type=block.block_type,
                    heading_path=block.heading_path,
                    page_number=block.page_number,
                    paragraph_index=block.paragraph_index,
                    table_index=block.table_index,
                    row_index=block.row_index,
                )
            )

    groups: list[list[DocumentBlock]] = []
    current: list[DocumentBlock] = []
    current_chars = 0
    for block in normalized:
        extra = len(block.text) + (2 if current else 0)
        # Keep a heading with the following prose where possible, but do not let
        # chunks drift across a new top-level section after they are sizeable.
        starts_new_section = block.block_type == "heading" and current_chars >= max_chars // 2
        if current and (current_chars + extra > max_chars or starts_new_section):
            groups.append(current)
            overlap = current[-overlap_blocks:] if overlap_blocks else []
            current = list(overlap)
            current_chars = sum(len(item.text) for item in current) + max(0, len(current) - 1) * 2
            if current and current_chars + extra > max_chars:
                current = []
                current_chars = 0
        current.append(block)
        current_chars += len(block.text) + (2 if len(current) > 1 else 0)
    if current:
        groups.append(current)

    chunks: list[KnowledgeChunk] = []
    for ordinal, group in enumerate(groups):
        types = {block.block_type for block in group}
        heading_path = next((block.heading_path for block in reversed(group) if block.heading_path), ())
        chunks.append(
            KnowledgeChunk(
                ordinal=ordinal,
                content="\n\n".join(block.text for block in group),
                start_locator=group[0].locator,
                end_locator=group[-1].locator,
                block_type=next(iter(types)) if len(types) == 1 else "mixed",
                heading_path=heading_path,
                source_date=source_date,
            )
        )
    return chunks


def _split_long_text(text: str, max_chars: int) -> list[str]:
    parts: list[str] = []
    remaining = text.strip()
    while len(remaining) > max_chars:
        window = remaining[: max_chars + 1]
        boundaries = [window.rfind(mark) for mark in ("。", "！", "？", ";", "；", ". ", "\n")]
        split_at = max(boundaries)
        if split_at < max_chars // 2:
            split_at = max_chars
        else:
            split_at += 1
        parts.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        parts.append(remaining)
    return parts


def get_connection(db_path: str | os.PathLike[str]) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def init_database(db_path: str | os.PathLike[str] | sqlite3.Connection) -> None:
    """Create or upgrade the local knowledge schema and search index."""

    owns_connection = not isinstance(db_path, sqlite3.Connection)
    connection = get_connection(db_path) if owns_connection else db_path
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS knowledge_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS libraries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'custom',
                enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                library_id INTEGER REFERENCES libraries(id) ON DELETE SET NULL,
                source_path TEXT NOT NULL,
                relative_path TEXT NOT NULL DEFAULT '',
                file_name TEXT NOT NULL,
                file_type TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                modified_at TEXT NOT NULL DEFAULT '',
                source_date TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'ready',
                duplicate_of_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                is_current INTEGER NOT NULL DEFAULT 1 CHECK(is_current IN (0, 1)),
                block_count INTEGER NOT NULL DEFAULT 0,
                chunk_count INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',
                imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source_path, content_hash)
            );

            CREATE TABLE IF NOT EXISTS library_documents (
                library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(library_id, document_id)
            );

            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL,
                content TEXT NOT NULL,
                start_locator TEXT NOT NULL,
                end_locator TEXT NOT NULL,
                locator TEXT NOT NULL,
                block_type TEXT NOT NULL DEFAULT 'paragraph',
                heading_path TEXT NOT NULL DEFAULT '[]',
                source_date TEXT NOT NULL DEFAULT '',
                char_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(document_id, ordinal)
            );

            CREATE TABLE IF NOT EXISTS import_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                root_path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                discovered INTEGER NOT NULL DEFAULT 0,
                imported INTEGER NOT NULL DEFAULT 0,
                duplicates INTEGER NOT NULL DEFAULT 0,
                skipped INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at TEXT,
                error TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS import_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES import_runs(id) ON DELETE CASCADE,
                source_path TEXT NOT NULL,
                document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                content_hash TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS doctrine_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'principle',
                confidence TEXT NOT NULL DEFAULT 'medium',
                applicability TEXT NOT NULL DEFAULT '',
                conditions_json TEXT NOT NULL DEFAULT '[]',
                exceptions_json TEXT NOT NULL DEFAULT '[]',
                tags_json TEXT NOT NULL DEFAULT '[]',
                industries_json TEXT NOT NULL DEFAULT '[]',
                cycles_json TEXT NOT NULL DEFAULT '[]',
                metrics_json TEXT NOT NULL DEFAULT '[]',
                thresholds_json TEXT NOT NULL DEFAULT '[]',
                counter_evidence_json TEXT NOT NULL DEFAULT '[]',
                source_document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
                source_chunk_id INTEGER REFERENCES chunks(id) ON DELETE SET NULL,
                source_locator TEXT NOT NULL DEFAULT '',
                extraction_method TEXT NOT NULL DEFAULT 'manual',
                candidate_hash TEXT NOT NULL DEFAULT '',
                current_version INTEGER NOT NULL DEFAULT 1,
                review_status TEXT NOT NULL DEFAULT 'pending' CHECK(review_status IN ('pending', 'approved', 'rejected')),
                enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0, 1)),
                reviewed_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS doctrine_card_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id INTEGER NOT NULL REFERENCES doctrine_cards(id) ON DELETE CASCADE,
                version_no INTEGER NOT NULL,
                change_kind TEXT NOT NULL DEFAULT 'edit',
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(card_id, version_no)
            );

            CREATE TABLE IF NOT EXISTS doctrine_generation_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                library_id INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'running',
                scanned_chunks INTEGER NOT NULL DEFAULT 0,
                candidates_created INTEGER NOT NULL DEFAULT 0,
                duplicates INTEGER NOT NULL DEFAULT 0,
                extraction_method TEXT NOT NULL,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                completed_at TEXT,
                error TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(content_hash);
            CREATE INDEX IF NOT EXISTS idx_documents_current_status ON documents(is_current, status);
            CREATE INDEX IF NOT EXISTS idx_documents_library ON documents(library_id);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_one_current_path
                ON documents(source_path) WHERE is_current = 1;
            CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id, ordinal);
            CREATE INDEX IF NOT EXISTS idx_import_items_run ON import_items(run_id, id);
            CREATE INDEX IF NOT EXISTS idx_doctrine_library ON doctrine_cards(library_id, enabled);
            CREATE INDEX IF NOT EXISTS idx_doctrine_versions ON doctrine_card_versions(card_id, version_no);
            CREATE INDEX IF NOT EXISTS idx_doctrine_generation_library ON doctrine_generation_runs(library_id, id);
            """
        )
        card_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(doctrine_cards)")
        }
        if "review_status" not in card_columns:
            connection.execute(
                "ALTER TABLE doctrine_cards ADD COLUMN review_status TEXT NOT NULL DEFAULT 'pending' "
                "CHECK(review_status IN ('pending', 'approved', 'rejected'))"
            )
        if "reviewed_at" not in card_columns:
            connection.execute("ALTER TABLE doctrine_cards ADD COLUMN reviewed_at TEXT")
        card_column_defaults = {
            "industries_json": "TEXT NOT NULL DEFAULT '[]'",
            "cycles_json": "TEXT NOT NULL DEFAULT '[]'",
            "metrics_json": "TEXT NOT NULL DEFAULT '[]'",
            "thresholds_json": "TEXT NOT NULL DEFAULT '[]'",
            "counter_evidence_json": "TEXT NOT NULL DEFAULT '[]'",
            "source_chunk_id": "INTEGER REFERENCES chunks(id) ON DELETE SET NULL",
            "extraction_method": "TEXT NOT NULL DEFAULT 'manual'",
            "candidate_hash": "TEXT NOT NULL DEFAULT ''",
            "current_version": "INTEGER NOT NULL DEFAULT 1",
        }
        for column, definition in card_column_defaults.items():
            if column not in card_columns:
                connection.execute(f"ALTER TABLE doctrine_cards ADD COLUMN {column} {definition}")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_doctrine_review "
            "ON doctrine_cards(review_status, enabled, library_id)"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_doctrine_candidate_hash "
            "ON doctrine_cards(candidate_hash) WHERE candidate_hash <> ''"
        )
        for card_row in connection.execute(
            "SELECT dc.* FROM doctrine_cards dc LEFT JOIN doctrine_card_versions v ON v.card_id=dc.id WHERE v.id IS NULL"
        ).fetchall():
            _record_card_version(connection, card_row, "migration_snapshot")
        # Preserve an explicit opt-out (useful on SQLite builds where FTS is
        # administratively disabled); otherwise probe/create the index.
        prior = connection.execute(
            "SELECT value FROM knowledge_meta WHERE key='fts5_enabled'"
        ).fetchone()
        fts_enabled = False
        if prior is None or prior[0] != "0":
            try:
                connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts "
                    "USING fts5(content, heading_path, locator UNINDEXED, document_id UNINDEXED, tokenize='unicode61')"
                )
                # Backfill only missing rowids, so init is safe and inexpensive.
                connection.execute(
                    """INSERT INTO chunks_fts(rowid, content, heading_path, locator, document_id)
                       SELECT c.id, c.content, c.heading_path, c.locator, c.document_id
                       FROM chunks c LEFT JOIN chunks_fts f ON f.rowid=c.id
                       WHERE f.rowid IS NULL"""
                )
                fts_enabled = True
            except sqlite3.OperationalError:
                fts_enabled = False
        connection.execute(
            """INSERT INTO knowledge_meta(key, value, updated_at)
               VALUES('fts5_enabled', ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP""",
            ("1" if fts_enabled else "0",),
        )
        connection.commit()
    finally:
        if owns_connection:
            connection.close()


@contextmanager
def _using_connection(
    target: str | os.PathLike[str] | sqlite3.Connection,
    *,
    initialize: bool = True,
) -> Iterator[sqlite3.Connection]:
    if isinstance(target, sqlite3.Connection):
        target.row_factory = sqlite3.Row
        target.execute("PRAGMA foreign_keys=ON")
        if initialize:
            init_database(target)
        yield target
        return
    if initialize:
        init_database(target)
    connection = get_connection(target)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def import_documents(
    db_path: str | os.PathLike[str],
    root: str | os.PathLike[str],
    library_id: int | None = None,
    *,
    chunk_size: int = 1600,
    overlap_blocks: int = 1,
    extensions: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Scan and import documents, returning a durable import-run summary."""

    source_root = Path(root).expanduser().resolve()
    files = scan_documents(source_root, extensions=extensions)
    # Legacy binary Word documents cannot be parsed reliably without an
    # external Office conversion step.  Discover them explicitly so a scan
    # never silently looks "complete" while omitting part of the user's
    # research library.  Source files remain untouched.
    legacy_word_files = scan_documents(source_root, extensions={".doc"})
    init_database(db_path)
    connection = get_connection(db_path)
    try:
        with connection:
            if library_id is not None:
                _require_library(connection, library_id)
            cursor = connection.execute(
                "INSERT INTO import_runs(root_path, discovered) VALUES (?, ?)",
                (str(source_root), len(files)),
            )
            run_id = int(cursor.lastrowid)
    finally:
        connection.close()

    counts = {"imported": 0, "duplicates": 0, "skipped": 0, "failed": 0}
    errors: list[dict[str, str]] = []
    base_for_relative = source_root if source_root.is_dir() else source_root.parent

    for path in files:
        try:
            content_hash = sha256_file(path)
            stat = path.stat()
            source_path = str(path)
            relative_path = str(path.relative_to(base_for_relative))
            modified_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
            connection = get_connection(db_path)
            try:
                with connection:
                    current = connection.execute(
                    "SELECT * FROM documents WHERE source_path=? AND is_current=1",
                    (source_path,),
                    ).fetchone()
                    if current is not None and current["content_hash"] == content_hash:
                        if library_id is not None:
                            _assign_document(connection, int(current["id"]), library_id)
                        _record_import_item(
                            connection, run_id, source_path, int(current["id"]), content_hash, "skipped", "内容未变化"
                        )
                        counts["skipped"] += 1
                        continue

                    historical = connection.execute(
                    "SELECT * FROM documents WHERE source_path=? AND content_hash=? ORDER BY id DESC LIMIT 1",
                    (source_path, content_hash),
                    ).fetchone()
                    if historical is not None:
                        connection.execute(
                            "UPDATE documents SET is_current=0, updated_at=CURRENT_TIMESTAMP WHERE source_path=? AND is_current=1",
                            (source_path,),
                        )
                        connection.execute(
                            "UPDATE documents SET is_current=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                            (historical["id"],),
                        )
                        if library_id is not None:
                            _assign_document(connection, int(historical["id"]), library_id)
                        _record_import_item(
                            connection,
                            run_id,
                            source_path,
                            int(historical["id"]),
                            content_hash,
                            "restored",
                            "恢复已导入的相同版本",
                        )
                        counts["skipped"] += 1
                        continue

                    canonical = connection.execute(
                    """SELECT * FROM documents
                       WHERE content_hash=? AND status='ready'
                       ORDER BY is_current DESC, id ASC LIMIT 1""",
                    (content_hash,),
                    ).fetchone()
                    if canonical is not None:
                        connection.execute(
                            "UPDATE documents SET is_current=0, updated_at=CURRENT_TIMESTAMP WHERE source_path=? AND is_current=1",
                            (source_path,),
                        )
                        cursor = connection.execute(
                        """INSERT INTO documents(
                               library_id, source_path, relative_path, file_name, file_type,
                               content_hash, size_bytes, modified_at, source_date, title, status,
                               duplicate_of_id, is_current, block_count, chunk_count)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'duplicate', ?, 1, ?, 0)""",
                        (
                            library_id,
                            source_path,
                            relative_path,
                            path.name,
                            path.suffix.lower().lstrip("."),
                            content_hash,
                            stat.st_size,
                            modified_at,
                            canonical["source_date"],
                            canonical["title"],
                            canonical["id"],
                            canonical["block_count"],
                        ),
                        )
                        document_id = int(cursor.lastrowid)
                        if library_id is not None:
                            _assign_document(connection, int(canonical["id"]), library_id)
                            _assign_document(connection, document_id, library_id)
                        _record_import_item(
                            connection,
                            run_id,
                            source_path,
                            document_id,
                            content_hash,
                            "duplicate",
                            f"与文档 {canonical['id']} 内容相同，未重复解析",
                        )
                        counts["duplicates"] += 1
                        continue
            finally:
                connection.close()

            # Extraction can be relatively expensive, so it happens outside a
            # write transaction.  The source is still only read.
            extraction = extract_document(path)
            chunks = chunk_blocks(
                extraction.blocks,
                max_chars=chunk_size,
                overlap_blocks=overlap_blocks,
                source_date=extraction.source_date,
            )
            warning = "" if chunks else "文件中没有可提取的文本（可能是扫描版 PDF）"
            connection = get_connection(db_path)
            try:
                with connection:
                    connection.execute(
                    "UPDATE documents SET is_current=0, updated_at=CURRENT_TIMESTAMP WHERE source_path=? AND is_current=1",
                    (source_path,),
                    )
                    cursor = connection.execute(
                    """INSERT INTO documents(
                           library_id, source_path, relative_path, file_name, file_type,
                           content_hash, size_bytes, modified_at, source_date, title, status,
                           is_current, block_count, chunk_count, error)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', 1, ?, ?, ?)""",
                    (
                        library_id,
                        source_path,
                        relative_path,
                        path.name,
                        extraction.file_type,
                        content_hash,
                        stat.st_size,
                        modified_at,
                        extraction.source_date,
                        extraction.title,
                        len(extraction.blocks),
                        len(chunks),
                        warning,
                    ),
                    )
                    document_id = int(cursor.lastrowid)
                    if library_id is not None:
                        _assign_document(connection, document_id, library_id)
                    fts_enabled = _fts_enabled(connection)
                    for chunk in chunks:
                        chunk_cursor = connection.execute(
                        """INSERT INTO chunks(
                               document_id, ordinal, content, start_locator, end_locator,
                               locator, block_type, heading_path, source_date, char_count)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            document_id,
                            chunk.ordinal,
                            chunk.content,
                            chunk.start_locator,
                            chunk.end_locator,
                            chunk.locator,
                            chunk.block_type,
                            json.dumps(chunk.heading_path, ensure_ascii=False),
                            chunk.source_date,
                            len(chunk.content),
                        ),
                        )
                        if fts_enabled:
                            connection.execute(
                            """INSERT INTO chunks_fts(rowid, content, heading_path, locator, document_id)
                               VALUES (?, ?, ?, ?, ?)""",
                            (
                                chunk_cursor.lastrowid,
                                chunk.content,
                                " / ".join(chunk.heading_path),
                                chunk.locator,
                                document_id,
                            ),
                            )
                    _record_import_item(
                        connection, run_id, source_path, document_id, content_hash, "imported", warning
                    )
            finally:
                connection.close()
            counts["imported"] += 1
        except Exception as exc:  # Continue importing independent documents.
            counts["failed"] += 1
            error = f"{type(exc).__name__}: {exc}"
            errors.append({"source_path": str(path), "error": error})
            try:
                connection = get_connection(db_path)
                try:
                    with connection:
                        _record_import_item(connection, run_id, str(path), None, "", "failed", error[:2000])
                finally:
                    connection.close()
            except sqlite3.Error:
                pass

    status = "completed" if counts["failed"] == 0 else ("failed" if not files or counts["failed"] == len(files) else "partial")
    connection = get_connection(db_path)
    try:
        with connection:
            connection.execute(
            """UPDATE import_runs SET status=?, imported=?, duplicates=?, skipped=?, failed=?,
                   completed_at=CURRENT_TIMESTAMP, error=? WHERE id=?""",
            (
                status,
                counts["imported"],
                counts["duplicates"],
                counts["skipped"],
                counts["failed"],
                json.dumps(errors, ensure_ascii=False),
                run_id,
            ),
            )
    finally:
        connection.close()
    return {
        "run_id": run_id,
        "root": str(source_root),
        "status": status,
        "discovered": len(files),
        "legacy_word_documents": len(legacy_word_files),
        **counts,
        "errors": errors,
    }


def _record_import_item(
    connection: sqlite3.Connection,
    run_id: int,
    source_path: str,
    document_id: int | None,
    content_hash: str,
    status: str,
    message: str,
) -> None:
    connection.execute(
        """INSERT INTO import_items(run_id, source_path, document_id, content_hash, status, message)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (run_id, source_path, document_id, content_hash, status, message),
    )


def _fts_enabled(connection: sqlite3.Connection) -> bool:
    row = connection.execute("SELECT value FROM knowledge_meta WHERE key='fts5_enabled'").fetchone()
    return bool(row and row[0] == "1")


def list_documents(
    db_path: str | os.PathLike[str] | sqlite3.Connection,
    limit: int = 100,
    *,
    library_id: int | None = None,
    include_history: bool = False,
    trusted_only: bool = False,
) -> list[dict[str, Any]]:
    limit = max(0, min(int(limit), 1000))
    if limit == 0:
        return []
    where = [] if include_history else ["d.is_current=1"]
    parameters: list[Any] = []
    if trusted_only:
        where.append(_assigned_document_clause())
    if library_id is not None:
        where.append(
            "(d.library_id=? OR EXISTS(SELECT 1 FROM library_documents ld WHERE ld.document_id=d.id AND ld.library_id=?))"
        )
        parameters.extend((library_id, library_id))
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    with _using_connection(db_path) as connection:
        rows = connection.execute(
            f"""SELECT d.*, l.name AS library_name
                FROM documents d LEFT JOIN libraries l ON l.id=d.library_id
                {clause}
                ORDER BY d.imported_at DESC, d.id DESC LIMIT ?""",
            (*parameters, limit),
        ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            value = dict(row)
            value["library_ids"] = [
                int(item[0])
                for item in connection.execute(
                    "SELECT library_id FROM library_documents WHERE document_id=? ORDER BY library_id", (row["id"],)
                ).fetchall()
            ]
            results.append(value)
        return results


def get_document(
    db_path: str | os.PathLike[str] | sqlite3.Connection, document_id: int
) -> dict[str, Any] | None:
    with _using_connection(db_path) as connection:
        row = connection.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["chunks"] = [dict(item) for item in connection.execute(
            "SELECT * FROM chunks WHERE document_id=? ORDER BY ordinal", (document_id,)
        ).fetchall()]
        for chunk in value["chunks"]:
            chunk["heading_path"] = _json_list(chunk.get("heading_path"))
        return value


def get_import_status(
    db_path: str | os.PathLike[str] | sqlite3.Connection, run_id: int | None = None
) -> dict[str, Any] | None:
    with _using_connection(db_path) as connection:
        if run_id is None:
            row = connection.execute("SELECT * FROM import_runs ORDER BY id DESC LIMIT 1").fetchone()
        else:
            row = connection.execute("SELECT * FROM import_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["items"] = [dict(item) for item in connection.execute(
            "SELECT * FROM import_items WHERE run_id=? ORDER BY id", (row["id"],)
        ).fetchall()]
        try:
            value["errors"] = json.loads(value.pop("error") or "[]")
        except json.JSONDecodeError:
            value["errors"] = [{"error": value.pop("error", "")}]
        return value


def list_import_runs(
    db_path: str | os.PathLike[str] | sqlite3.Connection, limit: int = 20
) -> list[dict[str, Any]]:
    with _using_connection(db_path) as connection:
        return [dict(row) for row in connection.execute(
            "SELECT * FROM import_runs ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 200)),)
        ).fetchall()]


def search_knowledge(
    db_path: str | os.PathLike[str] | sqlite3.Connection,
    query: str,
    limit: int = 20,
    library_id: int | None = None,
    *,
    trusted_only: bool = False,
    enabled_libraries_only: bool = False,
) -> list[dict[str, Any]]:
    """Search chunks with FTS5 and transparently fall back to LIKE.

    LIKE is also used to supplement empty FTS results.  This matters for Chinese
    substring queries because the standard SQLite unicode tokenizer has no word
    segmentation dictionary.
    """

    query = _clean_text(query)
    if not query or int(limit) <= 0:
        return []
    limit = min(int(limit), 200)
    with _using_connection(db_path) as connection:
        rows: list[sqlite3.Row] = []
        mode = "like"
        if _fts_enabled(connection):
            expression = _fts_expression(query)
            if expression:
                try:
                    rows = _search_fts(
                        connection,
                        expression,
                        limit,
                        library_id,
                        trusted_only,
                        enabled_libraries_only,
                    )
                    mode = "fts5"
                except sqlite3.OperationalError:
                    rows = []
        if len(rows) < limit:
            like_rows = _search_like(
                connection,
                query,
                limit,
                library_id,
                trusted_only,
                enabled_libraries_only,
            )
            existing = {int(row["chunk_id"]) for row in rows}
            rows.extend(row for row in like_rows if int(row["chunk_id"]) not in existing)
            rows = rows[:limit]
            if not existing:
                mode = "like"

        results: list[dict[str, Any]] = []
        for row in rows:
            value = dict(row)
            value["heading_path"] = _json_list(value.get("heading_path"))
            value["match_mode"] = mode if "search_mode" not in value else value.pop("search_mode")
            value["source"] = {
                "path": value["source_path"],
                "file_name": value["file_name"],
                "title": value["title"],
                "date": value["source_date"],
                "locator": value["locator"],
            }
            results.append(value)
        return results


def _active_document_clause(alias: str = "d") -> str:
    return (
        f"({alias}.is_current=1 OR EXISTS(SELECT 1 FROM documents active_dup "
        f"WHERE active_dup.duplicate_of_id={alias}.id AND active_dup.is_current=1))"
    )


def _library_clause(alias: str = "d") -> str:
    return (
        f"({alias}.library_id=? OR EXISTS(SELECT 1 FROM library_documents ld "
        f"WHERE ld.document_id={alias}.id AND ld.library_id=?))"
    )


def _assigned_document_clause(alias: str = "d") -> str:
    return (
        f"({alias}.library_id IS NOT NULL OR EXISTS(SELECT 1 FROM library_documents assigned_ld "
        f"WHERE assigned_ld.document_id={alias}.id))"
    )


def _enabled_library_document_clause(alias: str = "d") -> str:
    return (
        f"(EXISTS(SELECT 1 FROM libraries enabled_primary "
        f"WHERE enabled_primary.id={alias}.library_id AND enabled_primary.enabled=1) "
        f"OR EXISTS(SELECT 1 FROM library_documents enabled_ld "
        f"JOIN libraries enabled_library ON enabled_library.id=enabled_ld.library_id "
        f"WHERE enabled_ld.document_id={alias}.id AND enabled_library.enabled=1))"
    )


def _search_select(enabled_libraries_only: bool = False) -> str:
    # If the original canonical path changed, cite an active byte-identical
    # duplicate rather than a stale path.
    if enabled_libraries_only:
        library_fields = """COALESCE(
                  (SELECT enabled_primary.id FROM libraries enabled_primary
                   WHERE enabled_primary.id=d.library_id AND enabled_primary.enabled=1),
                  (SELECT enabled_library.id FROM library_documents enabled_ld
                   JOIN libraries enabled_library ON enabled_library.id=enabled_ld.library_id
                   WHERE enabled_ld.document_id=d.id AND enabled_library.enabled=1
                   ORDER BY enabled_library.id LIMIT 1)
              ) AS library_id,
              COALESCE(
                  (SELECT enabled_primary.name FROM libraries enabled_primary
                   WHERE enabled_primary.id=d.library_id AND enabled_primary.enabled=1),
                  (SELECT enabled_library.name FROM library_documents enabled_ld
                   JOIN libraries enabled_library ON enabled_library.id=enabled_ld.library_id
                   WHERE enabled_ld.document_id=d.id AND enabled_library.enabled=1
                   ORDER BY enabled_library.id LIMIT 1)
              ) AS library_name"""
    else:
        library_fields = "d.library_id, l.name AS library_name"
    return f"""SELECT c.id AS chunk_id, c.document_id, c.content, c.start_locator,
              c.end_locator, c.locator, c.block_type, c.heading_path,
              COALESCE(active_source.source_date, d.source_date, c.source_date) AS source_date,
              COALESCE(active_source.source_path, d.source_path) AS source_path,
              COALESCE(active_source.relative_path, d.relative_path) AS relative_path,
              COALESCE(active_source.file_name, d.file_name) AS file_name,
              d.title, d.file_type, d.content_hash, {library_fields}"""


def _search_joins() -> str:
    return """JOIN documents d ON d.id=c.document_id
              LEFT JOIN documents active_source ON active_source.id=(
                  SELECT active_dup.id FROM documents active_dup
                  WHERE active_dup.duplicate_of_id=d.id AND active_dup.is_current=1
                  ORDER BY active_dup.id LIMIT 1
              )
              LEFT JOIN libraries l ON l.id=d.library_id"""


def _search_fts(
    connection: sqlite3.Connection,
    expression: str,
    limit: int,
    library_id: int | None,
    trusted_only: bool = False,
    enabled_libraries_only: bool = False,
) -> list[sqlite3.Row]:
    filters = ["chunks_fts MATCH ?", "d.status='ready'", _active_document_clause()]
    parameters: list[Any] = [expression]
    if trusted_only:
        filters.append(_assigned_document_clause())
    if enabled_libraries_only:
        filters.append(_enabled_library_document_clause())
    if library_id is not None:
        filters.append(_library_clause())
        parameters.extend((library_id, library_id))
    parameters.append(limit)
    return connection.execute(
        f"""{_search_select(enabled_libraries_only)}, bm25(chunks_fts) AS rank, 'fts5' AS search_mode
            FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.rowid
            {_search_joins()}
            WHERE {' AND '.join(filters)}
            ORDER BY rank, c.id LIMIT ?""",
        parameters,
    ).fetchall()


def _search_like(
    connection: sqlite3.Connection,
    query: str,
    limit: int,
    library_id: int | None,
    trusted_only: bool = False,
    enabled_libraries_only: bool = False,
) -> list[sqlite3.Row]:
    terms = [term for term in re.split(r"\s+", query) if term] or [query]
    filters = ["d.status='ready'", _active_document_clause()]
    parameters: list[Any] = []
    if trusted_only:
        filters.append(_assigned_document_clause())
    if enabled_libraries_only:
        filters.append(_enabled_library_document_clause())
    for term in terms:
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        filters.append("(c.content LIKE ? ESCAPE '\\' OR c.heading_path LIKE ? ESCAPE '\\')")
        parameters.extend((f"%{escaped}%", f"%{escaped}%"))
    if library_id is not None:
        filters.append(_library_clause())
        parameters.extend((library_id, library_id))
    parameters.append(limit)
    return connection.execute(
        f"""{_search_select(enabled_libraries_only)}, 0.0 AS rank, 'like' AS search_mode
            FROM chunks c {_search_joins()}
            WHERE {' AND '.join(filters)}
            ORDER BY CASE WHEN c.content LIKE ? THEN 0 ELSE 1 END, c.id LIMIT ?""",
        (*parameters[:-1], f"%{query}%", parameters[-1]),
    ).fetchall()


def _fts_expression(query: str) -> str:
    terms = re.findall(r"[\w\u3400-\u9fff]+", query, flags=re.UNICODE)
    return " AND ".join(f'"{term.replace(chr(34), chr(34) * 2)}"*' for term in terms[:20])


def create_library(
    db: str | os.PathLike[str] | sqlite3.Connection,
    name: str,
    description: str = "",
    kind: str = "custom",
    *,
    enabled: bool = True,
) -> int:
    name = _clean_text(name)
    if not name:
        raise ValueError("知识流派名称不能为空")
    with _using_connection(db) as connection:
        cursor = connection.execute(
            "INSERT INTO libraries(name, description, kind, enabled) VALUES (?, ?, ?, ?)",
            (name, _clean_text(description), _clean_text(kind) or "custom", int(bool(enabled))),
        )
        return int(cursor.lastrowid)


def get_library(
    db: str | os.PathLike[str] | sqlite3.Connection, library_id: int
) -> dict[str, Any] | None:
    with _using_connection(db) as connection:
        row = connection.execute("SELECT * FROM libraries WHERE id=?", (library_id,)).fetchone()
        return dict(row) if row else None


def list_libraries(db: str | os.PathLike[str] | sqlite3.Connection) -> list[dict[str, Any]]:
    with _using_connection(db) as connection:
        rows = connection.execute(
            """SELECT l.*,
                 (SELECT COUNT(DISTINCT ld.document_id) FROM library_documents ld
                  JOIN documents d ON d.id=ld.document_id
                  WHERE ld.library_id=l.id AND d.is_current=1) AS document_count,
                 (SELECT COUNT(*) FROM doctrine_cards dc WHERE dc.library_id=l.id) AS card_count,
                 (SELECT COUNT(*) FROM doctrine_cards dc
                  WHERE dc.library_id=l.id AND dc.review_status='approved' AND dc.enabled=1) AS approved_card_count,
                 (SELECT COUNT(*) FROM doctrine_cards dc
                  WHERE dc.library_id=l.id AND dc.review_status='pending') AS pending_card_count
               FROM libraries l ORDER BY l.id"""
        ).fetchall()
        return [dict(row) for row in rows]


def update_library(
    db: str | os.PathLike[str] | sqlite3.Connection,
    library_id: int,
    values: Mapping[str, Any] | None = None,
    **changes: Any,
) -> dict[str, Any]:
    payload = dict(values or {})
    payload.update(changes)
    allowed = {"name", "description", "kind", "enabled"}
    payload = {key: value for key, value in payload.items() if key in allowed}
    if "name" in payload:
        payload["name"] = _clean_text(payload["name"])
        if not payload["name"]:
            raise ValueError("知识流派名称不能为空")
    if "enabled" in payload:
        payload["enabled"] = int(bool(payload["enabled"]))
    with _using_connection(db) as connection:
        _require_library(connection, library_id)
        if payload:
            assignments = ", ".join(f"{key}=?" for key in payload)
            connection.execute(
                f"UPDATE libraries SET {assignments}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (*payload.values(), library_id),
            )
        row = connection.execute("SELECT * FROM libraries WHERE id=?", (library_id,)).fetchone()
        return dict(row)


def delete_library(db: str | os.PathLike[str] | sqlite3.Connection, library_id: int) -> bool:
    with _using_connection(db) as connection:
        # Source documents and derived document records remain intact.  Only the
        # user-created library/card grouping is removed.
        cursor = connection.execute("DELETE FROM libraries WHERE id=?", (library_id,))
        return cursor.rowcount > 0


def assign_document_to_library(
    db: str | os.PathLike[str] | sqlite3.Connection, document_id: int, library_id: int
) -> bool:
    with _using_connection(db) as connection:
        _require_library(connection, library_id)
        if connection.execute("SELECT 1 FROM documents WHERE id=?", (document_id,)).fetchone() is None:
            raise KeyError(f"文档不存在：{document_id}")
        _assign_document(connection, document_id, library_id)
        return True


def _assign_document(connection: sqlite3.Connection, document_id: int, library_id: int) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO library_documents(library_id, document_id) VALUES (?, ?)",
        (library_id, document_id),
    )
    connection.execute(
        "UPDATE documents SET library_id=COALESCE(library_id, ?) WHERE id=?",
        (library_id, document_id),
    )


def _require_library(connection: sqlite3.Connection, library_id: int) -> None:
    if connection.execute("SELECT 1 FROM libraries WHERE id=?", (library_id,)).fetchone() is None:
        raise KeyError(f"知识流派不存在：{library_id}")


def create_doctrine_card(
    db: str | os.PathLike[str] | sqlite3.Connection,
    library_id: int,
    title: str | Mapping[str, Any],
    content: str | None = None,
    **fields: Any,
) -> int:
    if isinstance(title, Mapping):
        payload = dict(title)
        title = str(payload.pop("title", ""))
        content = str(payload.pop("content", payload.pop("thesis", "")))
        fields = {**payload, **fields}
    title = _clean_text(title)
    content = _clean_text(content)
    if not title or not content:
        raise ValueError("规则卡标题和内容不能为空")
    values = _normalize_card_fields(fields)
    with _using_connection(db) as connection:
        _require_library(connection, library_id)
        cursor = connection.execute(
            """INSERT INTO doctrine_cards(
                   library_id, title, content, category, confidence, applicability,
                   conditions_json, exceptions_json, tags_json, industries_json, cycles_json,
                   metrics_json, thresholds_json, counter_evidence_json, source_document_id,
                   source_chunk_id, source_locator, extraction_method, candidate_hash,
                   review_status, enabled, reviewed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                library_id,
                title,
                content,
                values["category"],
                values["confidence"],
                values["applicability"],
                values["conditions_json"],
                values["exceptions_json"],
                values["tags_json"],
                values["industries_json"],
                values["cycles_json"],
                values["metrics_json"],
                values["thresholds_json"],
                values["counter_evidence_json"],
                values["source_document_id"],
                values["source_chunk_id"],
                values["source_locator"],
                values["extraction_method"],
                values["candidate_hash"],
                values["review_status"],
                values["enabled"],
                values["reviewed_at"],
            ),
        )
        card_id = int(cursor.lastrowid)
        row = connection.execute("SELECT * FROM doctrine_cards WHERE id=?", (card_id,)).fetchone()
        _record_card_version(connection, row, "created")
        return card_id


def get_doctrine_card(
    db: str | os.PathLike[str] | sqlite3.Connection, card_id: int
) -> dict[str, Any] | None:
    with _using_connection(db) as connection:
        row = connection.execute(
            "SELECT dc.*, (SELECT COUNT(*) FROM doctrine_card_versions v WHERE v.card_id=dc.id) AS version_count FROM doctrine_cards dc WHERE dc.id=?",
            (card_id,),
        ).fetchone()
        return _card_row(row) if row else None


def list_doctrine_cards(
    db: str | os.PathLike[str] | sqlite3.Connection,
    library_id: int | None = None,
    *,
    enabled_only: bool = False,
    review_status: str | None = None,
) -> list[dict[str, Any]]:
    filters: list[str] = []
    parameters: list[Any] = []
    if library_id is not None:
        filters.append("dc.library_id=?")
        parameters.append(library_id)
    if enabled_only:
        filters.append("dc.enabled=1")
        filters.append("dc.review_status='approved'")
        filters.append("l.enabled=1")
    if review_status is not None:
        normalized_status = _normalize_review_status(review_status)
        filters.append("dc.review_status=?")
        parameters.append(normalized_status)
    clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    with _using_connection(db) as connection:
        rows = connection.execute(
            f"""SELECT dc.*, l.name AS library_name,
                       (SELECT COUNT(*) FROM doctrine_card_versions v WHERE v.card_id=dc.id) AS version_count
                FROM doctrine_cards dc
                JOIN libraries l ON l.id=dc.library_id {clause}
                ORDER BY dc.id""",
            parameters,
        ).fetchall()
        return [_card_row(row) for row in rows]


def update_doctrine_card(
    db: str | os.PathLike[str] | sqlite3.Connection,
    card_id: int,
    values: Mapping[str, Any] | None = None,
    **changes: Any,
) -> dict[str, Any]:
    payload = dict(values or {})
    payload.update(changes)
    change_kind = _clean_text(payload.pop("_change_kind", ""))
    aliases = {name: f"{name}_json" for name in ("conditions", "exceptions", "tags", "industries", "cycles", "metrics", "thresholds", "counter_evidence")}
    payload = {aliases.get(key, key): value for key, value in payload.items()}
    allowed = {
        "library_id",
        "title",
        "content",
        "category",
        "confidence",
        "applicability",
        "conditions_json",
        "exceptions_json",
        "tags_json",
        "industries_json",
        "cycles_json",
        "metrics_json",
        "thresholds_json",
        "counter_evidence_json",
        "source_document_id",
        "source_chunk_id",
        "source_locator",
        "extraction_method",
        "review_status",
        "enabled",
    }
    payload = {key: value for key, value in payload.items() if key in allowed}
    for key in ("conditions_json", "exceptions_json", "tags_json", "industries_json", "cycles_json", "metrics_json", "thresholds_json", "counter_evidence_json"):
        if key in payload and not isinstance(payload[key], str):
            payload[key] = json.dumps(_ensure_list(payload[key]), ensure_ascii=False)
    if "enabled" in payload:
        payload["enabled"] = int(bool(payload["enabled"]))
    if "review_status" in payload:
        payload["review_status"] = _normalize_review_status(payload["review_status"])
        payload["reviewed_at"] = (
            None if payload["review_status"] == "pending" else datetime.now(timezone.utc).isoformat()
        )
        if payload["review_status"] != "approved":
            payload["enabled"] = 0
    if "title" in payload:
        payload["title"] = _clean_text(payload["title"])
    if "content" in payload:
        payload["content"] = _clean_text(payload["content"])
    with _using_connection(db) as connection:
        existing = connection.execute("SELECT * FROM doctrine_cards WHERE id=?", (card_id,)).fetchone()
        if existing is None:
            raise KeyError(f"规则卡不存在：{card_id}")
        effective_status = payload.get("review_status", existing["review_status"])
        if payload.get("enabled") and effective_status != "approved":
            raise ValueError("规则卡必须审核通过后才能启用")
        if "library_id" in payload:
            _require_library(connection, int(payload["library_id"]))
        if payload:
            assignments = ", ".join(f"{key}=?" for key in payload)
            connection.execute(
                f"UPDATE doctrine_cards SET {assignments}, current_version=current_version+1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (*payload.values(), card_id),
            )
        row = connection.execute("SELECT * FROM doctrine_cards WHERE id=?", (card_id,)).fetchone()
        if payload:
            _record_card_version(connection, row, change_kind or ("review" if "review_status" in payload or "enabled" in payload else "edit"))
        return _card_row(row)


def delete_doctrine_card(
    db: str | os.PathLike[str] | sqlite3.Connection, card_id: int
) -> bool:
    with _using_connection(db) as connection:
        return connection.execute("DELETE FROM doctrine_cards WHERE id=?", (card_id,)).rowcount > 0


def list_doctrine_card_versions(
    db: str | os.PathLike[str] | sqlite3.Connection, card_id: int
) -> list[dict[str, Any]]:
    with _using_connection(db) as connection:
        rows = connection.execute(
            "SELECT id, card_id, version_no, change_kind, snapshot_json, created_at FROM doctrine_card_versions WHERE card_id=? ORDER BY version_no DESC",
            (card_id,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["snapshot"] = json.loads(item.pop("snapshot_json"))
            except (json.JSONDecodeError, TypeError):
                item["snapshot"] = {}
            result.append(item)
        return result


def _record_card_version(connection: sqlite3.Connection, row: sqlite3.Row, change_kind: str) -> None:
    snapshot = _card_row(row)
    version_no = int(snapshot.get("current_version") or 1)
    connection.execute(
        "INSERT OR REPLACE INTO doctrine_card_versions(card_id, version_no, change_kind, snapshot_json) VALUES (?, ?, ?, ?)",
        (int(snapshot["id"]), version_no, _clean_text(change_kind) or "edit", json.dumps(snapshot, ensure_ascii=False, sort_keys=True)),
    )


_RULE_CUES = (
    "必须", "应当", "不应", "不得", "只有", "如果", "当", "至少", "不超过", "不低于",
    "高于", "低于", "安全边际", "优先", "避免", "警惕", "除非", "否则", "越高", "越低",
)
_FINANCE_METRICS: dict[str, tuple[str, ...]] = {
    "roe": ("ROE", "净资产收益率"),
    "roic": ("ROIC", "投入资本回报率", "资本回报率"),
    "gross_margin": ("毛利率",),
    "net_margin": ("净利率",),
    "revenue_growth": ("营收增长", "收入增长", "营业收入增长"),
    "profit_growth": ("利润增长", "净利润增长", "扣非增长"),
    "operating_cash_flow": ("经营现金流", "经营活动现金流", "CFO"),
    "free_cash_flow": ("自由现金流", "FCF"),
    "cash_profit_ratio": ("现金含量", "现金利润比", "经营现金流/净利润"),
    "dividend_yield": ("股息率", "股息收益率"),
    "dividend_payout": ("分红率", "派息率", "分红覆盖"),
    "debt_ratio": ("资产负债率", "负债率"),
    "net_debt": ("净负债", "净债务"),
    "interest_coverage": ("利息保障倍数", "利息覆盖"),
    "receivables": ("应收账款", "应收"),
    "inventory": ("存货", "库存"),
    "goodwill": ("商誉",),
    "capital_expenditure": ("资本开支", "资本支出", "CAPEX"),
    "pe": ("市盈率", "PE", "P/E"),
    "pb": ("市净率", "PB", "P/B"),
    "ev_ebitda": ("EV/EBITDA", "企业价值倍数"),
    "margin_of_safety": ("安全边际",),
}
_INDUSTRY_TERMS: dict[str, tuple[str, ...]] = {
    "bank": ("银行",),
    "insurance": ("保险",),
    "brokerage": ("券商", "证券公司"),
    "real_estate": ("地产", "房地产"),
    "utility": ("公用事业", "电力", "燃气", "水务"),
    "resource": ("资源股", "煤炭", "有色", "石油", "周期资源"),
    "consumer": ("消费", "白酒", "食品饮料"),
    "manufacturing": ("制造业", "制造企业"),
    "pharma": ("医药",),
    "technology": ("半导体", "软件", "科技"),
}
_CYCLE_TERMS: dict[str, tuple[str, ...]] = {
    "recession": ("衰退", "下行周期", "萧条"),
    "recovery": ("复苏",),
    "expansion": ("扩张周期", "景气上行"),
    "peak": ("周期顶部", "景气高点", "繁荣末期"),
    "full_cycle": ("全周期", "穿越周期", "中周期"),
}


def generate_doctrine_candidates(
    db: str | os.PathLike[str] | sqlite3.Connection,
    library_id: int,
    *,
    max_candidates: int = 120,
    max_chunks: int = 3000,
) -> dict[str, Any]:
    """Deterministically extract source-linked *pending* doctrine candidates.

    This intentionally does not use an LLM and never approves a rule.  It is a
    high-recall review aid: the user remains responsible for interpreting and
    approving the source author's actual doctrine.
    """

    max_candidates = max(1, min(int(max_candidates), 500))
    max_chunks = max(1, min(int(max_chunks), 10000))
    with _using_connection(db) as connection:
        _require_library(connection, library_id)
        run = connection.execute(
            "INSERT INTO doctrine_generation_runs(library_id, extraction_method) VALUES (?, ?)",
            (library_id, "deterministic-rule-v1"),
        )
        run_id = int(run.lastrowid)
        chunks = connection.execute(
            f"""SELECT c.id AS chunk_id, c.document_id, c.content, c.locator,
                       c.heading_path, d.title AS document_title, d.file_name,
                       d.source_path, d.source_date
                FROM chunks c JOIN documents d ON d.id=c.document_id
                WHERE d.status='ready' AND {_active_document_clause()}
                  AND {_library_clause()}
                ORDER BY d.id, c.ordinal LIMIT ?""",
            (library_id, library_id, max_chunks),
        ).fetchall()
        created: list[dict[str, Any]] = []
        duplicates = 0
        try:
            for chunk in chunks:
                for sentence in _candidate_sentences(chunk["content"]):
                    inferred = _infer_candidate_fields(sentence)
                    if inferred is None:
                        continue
                    candidate_hash = hashlib.sha256(
                        f"{library_id}\0{_normalized_candidate_text(sentence)}".encode("utf-8")
                    ).hexdigest()
                    if connection.execute(
                        "SELECT 1 FROM doctrine_cards WHERE candidate_hash=?", (candidate_hash,)
                    ).fetchone():
                        duplicates += 1
                        continue
                    title = _candidate_title(sentence, inferred["category"])
                    card_id = create_doctrine_card(
                        connection,
                        library_id,
                        title,
                        sentence,
                        **inferred,
                        source_document_id=int(chunk["document_id"]),
                        source_chunk_id=int(chunk["chunk_id"]),
                        source_locator=chunk["locator"],
                        extraction_method="deterministic-rule-v1",
                        candidate_hash=candidate_hash,
                        review_status="pending",
                        enabled=False,
                    )
                    created.append({
                        "card_id": card_id,
                        "title": title,
                        "source_document_id": int(chunk["document_id"]),
                        "source_chunk_id": int(chunk["chunk_id"]),
                        "source_locator": chunk["locator"],
                        "source_file": chunk["file_name"],
                    })
                    if len(created) >= max_candidates:
                        break
                if len(created) >= max_candidates:
                    break
            connection.execute(
                """UPDATE doctrine_generation_runs
                   SET status='completed', scanned_chunks=?, candidates_created=?, duplicates=?,
                       completed_at=CURRENT_TIMESTAMP WHERE id=?""",
                (len(chunks), len(created), duplicates, run_id),
            )
        except Exception as exc:
            connection.execute(
                """UPDATE doctrine_generation_runs SET status='failed', scanned_chunks=?,
                       candidates_created=?, duplicates=?, error=?, completed_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (len(chunks), len(created), duplicates, str(exc), run_id),
            )
            raise
        return {
            "run_id": run_id,
            "library_id": library_id,
            "status": "completed",
            "scanned_chunks": len(chunks),
            "candidates_created": len(created),
            "duplicates": duplicates,
            "candidates": created,
            "review_required": True,
            "notice": "候选规则仅供人工审核；未批准前不会参与任何股票分析。",
        }


def list_doctrine_generation_runs(
    db: str | os.PathLike[str] | sqlite3.Connection,
    library_id: int | None = None,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    filters = "WHERE r.library_id=?" if library_id is not None else ""
    parameters: tuple[Any, ...] = (library_id, limit) if library_id is not None else (limit,)
    with _using_connection(db) as connection:
        return [dict(row) for row in connection.execute(
            f"""SELECT r.*, l.name AS library_name FROM doctrine_generation_runs r
                JOIN libraries l ON l.id=r.library_id {filters}
                ORDER BY r.id DESC LIMIT ?""",
            parameters,
        ).fetchall()]


def analyze_doctrine_relationships(
    db: str | os.PathLike[str] | sqlite3.Connection,
) -> dict[str, Any]:
    """Find deterministic cross-library consensus/conflict review leads.

    Only approved, enabled rules from enabled libraries are eligible.  The
    result is deliberately labelled heuristic and never changes a rule.
    """

    cards = list_doctrine_cards(db, enabled_only=True)
    relationships: list[dict[str, Any]] = []
    for index, left in enumerate(cards):
        for right in cards[index + 1:]:
            if int(left["library_id"]) == int(right["library_id"]):
                continue
            anchors = sorted(set(_relationship_anchors(left)) & set(_relationship_anchors(right)))
            if not anchors:
                continue
            left_direction = _rule_direction(left)
            right_direction = _rule_direction(right)
            if left_direction == "neutral" or right_direction == "neutral":
                continue
            kind = "consensus" if left_direction == right_direction else "conflict"
            relationships.append({
                "kind": kind,
                "anchors": anchors,
                "confidence": "medium" if set(left.get("metrics", [])) & set(right.get("metrics", [])) else "low",
                "left": _relationship_card(left, left_direction),
                "right": _relationship_card(right, right_direction),
                "review_status": "heuristic_lead",
                "notice": "系统仅根据指标与措辞发现线索，需回看两条原文后人工判断。",
            })
    relationships.sort(key=lambda item: (item["kind"] != "conflict", -len(item["anchors"])))
    return {
        "summary": {
            "enabled_rules": len(cards),
            "consensus": sum(item["kind"] == "consensus" for item in relationships),
            "conflicts": sum(item["kind"] == "conflict" for item in relationships),
        },
        "relationships": relationships,
        "method": "deterministic-doctrine-relationship-v1",
        "review_required": True,
    }


def _candidate_sentences(content: str) -> list[str]:
    pieces = re.split(r"(?<=[。！？!?；;])\s*|[\r\n]+", _clean_text(content))
    result: list[str] = []
    for piece in pieces:
        sentence = piece.strip(" -—\t")
        if 16 <= len(sentence) <= 500:
            result.append(sentence)
    return result


def _normalized_candidate_text(text: str) -> str:
    return re.sub(r"[\s，。；、,.!?！？:：()（）\-—]+", "", text).lower()


def _infer_candidate_fields(sentence: str) -> dict[str, Any] | None:
    metrics = [name for name, aliases in _FINANCE_METRICS.items() if _contains_alias(sentence, aliases)]
    has_threshold = bool(re.search(r"(?:至少|不超过|不低于|高于|低于|大于|小于|≥|≤|>|<)?\s*-?\d+(?:\.\d+)?\s*(?:%|倍|年|元|亿元|万元)?", sentence))
    if not any(cue in sentence for cue in _RULE_CUES) and not (has_threshold and metrics):
        return None
    finance_context = metrics or any(term in sentence for term in ("估值", "盈利", "现金", "负债", "分红", "公司", "股票", "投资", "财报", "资产"))
    if not finance_context:
        return None
    industries = [name for name, aliases in _INDUSTRY_TERMS.items() if _contains_alias(sentence, aliases)]
    cycles = [name for name, aliases in _CYCLE_TERMS.items() if _contains_alias(sentence, aliases)]
    thresholds = [match.group(0).strip() for match in re.finditer(
        r"(?:至少|不超过|不低于|不得高于|不得低于|高于|低于|大于|小于|≥|≤|>|<)\s*-?\d+(?:\.\d+)?\s*(?:%|倍|年|元|亿元|万元)?",
        sentence,
    )]
    category = _infer_category(sentence)
    counter_evidence = []
    for separator in ("除非", "但", "例外", "反之"):
        if separator in sentence:
            counter_evidence.append(sentence[sentence.index(separator):].strip())
            break
    applicability_parts = industries + cycles
    return {
        "category": category,
        "confidence": "medium" if metrics or thresholds else "low",
        "applicability": "、".join(applicability_parts),
        "conditions": [sentence] if sentence.startswith(("如果", "当", "只有")) else [],
        "exceptions": counter_evidence,
        "counter_evidence": counter_evidence,
        "tags": sorted(set(metrics + [category])),
        "industries": industries,
        "cycles": cycles,
        "metrics": metrics,
        "thresholds": thresholds,
    }


def _contains_alias(text: str, aliases: Sequence[str]) -> bool:
    upper = text.upper()
    return any(alias.upper() in upper for alias in aliases)


def _infer_category(text: str) -> str:
    groups = (
        ("valuation", ("估值", "市盈率", "市净率", "安全边际", "PE", "PB")),
        ("dividend", ("分红", "股息", "派息")),
        ("cash_quality", ("现金流", "现金含量", "自由现金流")),
        ("balance_sheet", ("负债", "偿债", "商誉", "应收", "存货")),
        ("profitability", ("ROE", "ROIC", "利润率", "盈利能力")),
        ("growth", ("增长", "增速")),
        ("governance", ("审计", "关联交易", "治理", "管理层")),
    )
    upper = text.upper()
    for category, cues in groups:
        if any(cue.upper() in upper for cue in cues):
            return category
    return "principle"


def _candidate_title(sentence: str, category: str) -> str:
    concise = sentence.rstrip("。！？!?；;")
    return concise if len(concise) <= 46 else f"{concise[:43]}…"


def _relationship_anchors(card: Mapping[str, Any]) -> list[str]:
    metrics = [f"metric:{item}" for item in card.get("metrics", []) if item]
    if metrics:
        return metrics
    tags = [f"tag:{item}" for item in card.get("tags", []) if item and item != card.get("category")]
    return tags or [f"category:{card.get('category', 'principle')}"]


def _rule_direction(card: Mapping[str, Any]) -> str:
    text = " ".join(
        [str(card.get("content", ""))]
        + [str(value) for value in card.get("thresholds", [])]
    )
    negative = ("不超过", "不得高于", "低于", "小于", "越低", "避免", "降低", "禁止")
    positive = ("至少", "不低于", "高于", "大于", "越高", "提高", "优先", "保持")
    negative_hits = sum(cue in text for cue in negative)
    positive_hits = sum(cue in text for cue in positive)
    if negative_hits == positive_hits:
        return "neutral"
    return "max_or_avoid" if negative_hits > positive_hits else "min_or_prefer"


def _relationship_card(card: Mapping[str, Any], direction: str) -> dict[str, Any]:
    return {
        "card_id": int(card["id"]),
        "library_id": int(card["library_id"]),
        "library_name": card.get("library_name", ""),
        "title": card.get("title", ""),
        "content": card.get("content", ""),
        "direction": direction,
        "metrics": card.get("metrics", []),
        "thresholds": card.get("thresholds", []),
        "source_document_id": card.get("source_document_id"),
        "source_chunk_id": card.get("source_chunk_id"),
        "source_locator": card.get("source_locator", ""),
        "version": int(card.get("current_version") or 1),
    }


def _normalize_card_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    review_status = _normalize_review_status(fields.get("review_status", "pending"))
    enabled = int(bool(fields.get("enabled", False))) if review_status == "approved" else 0
    return {
        "category": _clean_text(fields.get("category", "principle")) or "principle",
        "confidence": _clean_text(fields.get("confidence", "medium")) or "medium",
        "applicability": _clean_text(fields.get("applicability", "")),
        "conditions_json": json.dumps(_ensure_list(fields.get("conditions", [])), ensure_ascii=False),
        "exceptions_json": json.dumps(_ensure_list(fields.get("exceptions", [])), ensure_ascii=False),
        "tags_json": json.dumps(_ensure_list(fields.get("tags", [])), ensure_ascii=False),
        "industries_json": json.dumps(_ensure_list(fields.get("industries", [])), ensure_ascii=False),
        "cycles_json": json.dumps(_ensure_list(fields.get("cycles", [])), ensure_ascii=False),
        "metrics_json": json.dumps(_ensure_list(fields.get("metrics", [])), ensure_ascii=False),
        "thresholds_json": json.dumps(_ensure_list(fields.get("thresholds", [])), ensure_ascii=False),
        "counter_evidence_json": json.dumps(_ensure_list(fields.get("counter_evidence", fields.get("exceptions", []))), ensure_ascii=False),
        "source_document_id": fields.get("source_document_id"),
        "source_chunk_id": fields.get("source_chunk_id"),
        "source_locator": _clean_text(fields.get("source_locator", "")),
        "extraction_method": _clean_text(fields.get("extraction_method", "manual")) or "manual",
        "candidate_hash": _clean_text(fields.get("candidate_hash", "")),
        "review_status": review_status,
        "enabled": enabled,
        "reviewed_at": None if review_status == "pending" else datetime.now(timezone.utc).isoformat(),
    }


def _normalize_review_status(value: Any) -> str:
    status = _clean_text(value or "pending").lower()
    if status not in {"pending", "approved", "rejected"}:
        raise ValueError("规则卡审核状态只能是 pending、approved 或 rejected")
    return status


def _ensure_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else [parsed]
    except (json.JSONDecodeError, TypeError):
        return []


def _card_row(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    for name in ("conditions", "exceptions", "tags", "industries", "cycles", "metrics", "thresholds", "counter_evidence"):
        value[name] = _json_list(value.pop(f"{name}_json", "[]"))
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="本地投资知识库导入与检索")
    parser.add_argument("--db", default=str(Path(__file__).resolve().parents[1] / "data" / "investment_brain.db"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="初始化 SQLite 数据库")
    import_parser = subparsers.add_parser("import", help="只读导入目录中的资料")
    import_parser.add_argument("root")
    import_parser.add_argument("--library-id", type=int)
    search_parser = subparsers.add_parser("search", help="全文检索")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=20)
    search_parser.add_argument("--library-id", type=int)
    subparsers.add_parser("documents", help="列出已导入文档")
    subparsers.add_parser("libraries", help="列出知识流派")
    status_parser = subparsers.add_parser("status", help="查看导入状态")
    status_parser.add_argument("--run-id", type=int)
    args = parser.parse_args(argv)

    if args.command == "init":
        init_database(args.db)
        result: Any = {"ok": True, "db": str(Path(args.db).resolve())}
    elif args.command == "import":
        result = import_documents(args.db, args.root, args.library_id)
    elif args.command == "search":
        result = search_knowledge(args.db, args.query, args.limit, args.library_id)
    elif args.command == "documents":
        result = list_documents(args.db)
    elif args.command == "libraries":
        result = list_libraries(args.db)
    else:
        result = get_import_status(args.db, args.run_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through functions
    raise SystemExit(main())
