"""Streaming, *non-executing* parser for MySQL / MariaDB dump files.

Handles the output of ``mysqldump``, phpMyAdmin and Adminer. The SQL is treated as
untrusted text: nothing is ever executed, evaluated or passed to a database. Only three
statement families are interpreted:

* ``CREATE TABLE``  -> :class:`TableEvent` (column names/types, primary + unique keys)
* ``INSERT`` / ``REPLACE ... VALUES`` -> one :class:`RowEvent` per row, streamed
* ``ALTER TABLE ... ADD PRIMARY KEY / ADD UNIQUE`` -> updated :class:`TableEvent`

Everything else (``SET``, ``LOCK TABLES``, ``DROP``, conditional comments, ``DELIMITER``
blocks with procedures/triggers, ...) is skipped.

Memory is bounded: the file is read in binary chunks (1 MiB by default), rows are yielded
as soon as they are complete and no statement's value list is ever accumulated. The only
data that must fit in memory at once is a single value literal, capped by
``max_value_bytes`` (raw, still-escaped size in the file).

Error messages / warnings never contain data from the file, only byte offsets and codes.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import BinaryIO

from .errors import SqlParseError
from .progress import CancelToken, ProgressFn

__all__ = [
    "Column",
    "DumpReader",
    "RowEvent",
    "SqlValue",
    "TableDef",
    "TableEvent",
    "WarningEvent",
    "scan_schema",
]

SqlValue = None | str | int | Decimal | bytes

_CHUNK = 1024 * 1024
_MAX_STATEMENT = 16 * 1024 * 1024  # CREATE/ALTER statements are collected in memory; cap them


# --------------------------------------------------------------------------- data model


@dataclass(frozen=True)
class Column:
    name: str
    type: str  # raw type text, lowercased, e.g. "bigint(20) unsigned", "varchar(500)"
    nullable: bool
    default: str | None  # raw SQL text of the DEFAULT expression; None if absent or DEFAULT NULL
    charset: str | None = None  # column-level CHARACTER SET / CHARSET clause, lowercased
    collation: str | None = None  # column-level COLLATE clause, lowercased


@dataclass(frozen=True)
class TableDef:
    name: str
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...]
    unique_keys: tuple[tuple[str, ...], ...]
    # Table options after the column list (``ENGINE=… DEFAULT CHARSET=… COLLATE=…``), as dumped
    # (engine verbatim, charset/collation lowercased); None when the dump does not state them.
    engine: str | None = None
    charset: str | None = None
    collation: str | None = None

    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)


@dataclass(frozen=True, slots=True)
class RowEvent:
    table: str
    columns: tuple[str, ...]
    values: tuple[SqlValue, ...]
    statement_index: int
    row_index: int


@dataclass(frozen=True)
class TableEvent:
    table: TableDef


@dataclass(frozen=True)
class WarningEvent:
    code: str
    message: str
    offset: int


Event = TableEvent | RowEvent | WarningEvent


# --------------------------------------------------------------------------- regexes

_SQ = rb"[^'\\]*+(?:(?:\\.|'')[^'\\]*+)*+"
_DQ = rb'[^"\\]*+(?:(?:\\.|"")[^"\\]*+)*+'
_BQ = rb"[^`]*+(?:``[^`]*+)*+"

# One value literal followed by its separator ("," or ")"). Each alternative has exactly one
# capture group that closes last, so ``m.lastindex`` identifies the literal kind.
_VALUE_RE = re.compile(
    rb"[ \t\r\n]*+(?:"
    rb"'(" + _SQ + rb")'"  # 1  'string'
    rb"|([-+]?[0-9]+)(?![0-9.eExXbB])"  # 2  integer
    rb"|([Nn][Uu][Ll][Ll])"  # 3  NULL
    rb"|([-+]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?)"  # 4  decimal / float
    rb'|"(' + _DQ + rb')"'  # 5  "string"
    rb"|0x([0-9a-fA-F]*)"  # 6  0xCAFE
    rb"|[xX]'([0-9a-fA-F]*)'"  # 7  X'CAFE'
    rb"|_([A-Za-z0-9]+)[ \t\r\n]*'(" + _SQ + rb")'"  # 8,9 _charset'string'
    rb"|[bB]'([01]*)'"  # 10 b'0101'
    rb"|0b([01]+)"  # 11 0b0101
    rb"|[Nn]'(" + _SQ + rb")'"  # 12 N'string'
    rb"|([Tt][Rr][Uu][Ee]|[Ff][Aa][Ll][Ss][Ee])"  # 13 TRUE / FALSE
    rb")[ \t\r\n]*+[,)]",
    re.S,
)
# Bulk path: a run of complete rows (each followed by ',') is validated by one regex and then
# tokenized by ``findall``; both run in C. Literal alternatives are ordered so the first
# alternative that matches is the only valid one (hex/bit literals before plain numbers).
_LIT = (
    rb"'" + _SQ + rb"'"
    rb"|0x[0-9a-fA-F]*|0b[01]+"
    rb"|[-+]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?"
    rb"|[Nn][Uu][Ll][Ll]"
    rb'|"' + _DQ + rb'"'
    rb"|[xX]'[0-9a-fA-F]*'"
    rb"|_[A-Za-z0-9]+[ \t\r\n]*'" + _SQ + rb"'"
    rb"|[bB]'[01]*'"
    rb"|[Nn]'" + _SQ + rb"'"
    rb"|[Tt][Rr][Uu][Ee]|[Ff][Aa][Ll][Ss][Ee]"
)
_W = rb"[ \t\r\n]*+"
_BULK_RE = re.compile(
    rb"(?:"
    + _W
    + rb"\("
    + _W
    + rb"(?:(?>"
    + _LIT
    + rb")"
    + _W
    + rb"(?:,"
    + _W
    + rb"(?>"
    + _LIT
    + rb")"
    + _W
    + rb")*+)?\)"
    + _W
    + rb",)*+",
    re.S,
)
# Consumes the separators itself so matches are back-to-back (no failed search positions).
_LIT_FINDALL_RE = re.compile(rb"[ \t\r\n,(]*+(?:(" + _LIT + rb")|\))", re.S)
_LIT_FINDALL_STR_RE = re.compile(_LIT_FINDALL_RE.pattern.decode("ascii"), re.S)
_NUM_START = frozenset("0123456789-+.")
_ROW_START_RE = re.compile(rb"[ \t\r\n]*+\((?:[ \t\r\n]*+\))?")
_ROW_END_RE = re.compile(rb"[ \t\r\n]*+([,;])")

_TOKEN_RE = re.compile(
    rb"(?P<ws>[ \t\r\n\f\v]+)"
    rb"|(?P<cmt>(?:--(?=[ \t\r\n\f\v])|\#)[^\n]*)"
    rb"|(?P<bcmt>/\*.*?\*/)"
    rb"|(?P<sq>'" + _SQ + rb"')"
    rb'|(?P<dq>"' + _DQ + rb'")'
    rb"|(?P<bq>`" + _BQ + rb"`)"
    rb"|(?P<num>[0-9]+\.[0-9]*(?:[eE][-+]?[0-9]+)?|\.[0-9]+(?:[eE][-+]?[0-9]+)?)"
    rb"|(?P<word>[A-Za-z0-9_$\x80-\xff]+)"
    rb"|(?P<open>['\"`]|/\*)"
    rb"|(?P<p>.)",
    re.S,
)
_SKIP_TOKENS = frozenset(("ws", "cmt", "bcmt"))

# Fast skipping of an INSERT body (schema-only scan): strings/identifiers are atomic units.
_SKIP_BODY_RE = re.compile(rb"(?:[^'\"`;]++|'" + _SQ + rb"'|\"" + _DQ + rb"\"|`" + _BQ + rb"`)*+", re.S)
_DELIM_RESTORE_RE = re.compile(rb"^[ \t]*[Dd][Ee][Ll][Ii][Mm][Ii][Tt][Ee][Rr][ \t]+;[ \t]*\r?$", re.M)

_ESC = {
    b"0": b"\x00",
    b"'": b"'",
    b'"': b'"',
    b"b": b"\x08",
    b"n": b"\n",
    b"r": b"\r",
    b"t": b"\t",
    b"Z": b"\x1a",
    b"\\": b"\\",
    b"%": b"\\%",  # MySQL keeps the backslash for \% and \_
    b"_": b"\\_",
}
_ESC_SQ_RE = re.compile(rb"\\(.)|''", re.S)
_ESC_DQ_RE = re.compile(rb'\\(.)|""', re.S)


def _esc_repl(m: re.Match[bytes]) -> bytes:
    g = m.group(1)
    if g is None:
        return m.group(0)[:1]
    return _ESC.get(g, g)


def _unescape_sq(body: bytes) -> bytes:
    return _ESC_SQ_RE.sub(_esc_repl, body)


def _unescape_dq(body: bytes) -> bytes:
    return _ESC_DQ_RE.sub(_esc_repl, body)


_ESC_STR = {k.decode("latin-1"): v.decode("latin-1") for k, v in _ESC.items()}
_ESC_SQ_STR_RE = re.compile(r"\\(.)|''", re.S)


def _esc_str_repl(m: re.Match[str]) -> str:
    g = m.group(1)
    if g is None:
        return "'"
    return _ESC_STR.get(g, g)


def _unescape_sq_str(body: str) -> str:
    return _ESC_SQ_STR_RE.sub(_esc_str_repl, body)


def _bits_to_bytes(bits: bytes) -> bytes:
    if not bits:
        return b""
    return int(bits, 2).to_bytes((len(bits) + 7) // 8, "big")


def _error(code: str, what: str, offset: int, message_fa: str | None = None) -> SqlParseError:
    err = SqlParseError(message_fa, code=code, detail=f"{what} (byte offset {offset})")
    err.offset = offset  # type: ignore[attr-defined]
    return err


_MSG_MISMATCH = "تعداد مقادیر یک ردیف با تعداد ستون‌های جدول برابر نیست؛ فایل SQL خراب است."
_MSG_TRUNCATED = "فایل SQL ناقص است (به‌صورت ناگهانی تمام شده است)."
_MSG_TOO_LARGE = "یکی از مقادیر فایل SQL از حد مجاز بزرگ‌تر است."
_MSG_ENCODING = "فایل SQL شامل متن با رمزگذاری نامعتبر (غیر UTF-8) است."

Token = tuple[str, bytes, int]  # (kind, raw bytes, absolute offset)


# --------------------------------------------------------------------------- reader


class DumpReader:
    """Stream events out of a SQL dump. ``events()`` may be iterated once."""

    def __init__(
        self,
        path: Path,
        *,
        cancel: CancelToken | None = None,
        progress: ProgressFn | None = None,
        max_value_bytes: int = 16 * 1024**2,
        encoding_errors: str = "strict",
        _chunk_size: int = _CHUNK,
        _schema_only: bool = False,
    ) -> None:
        self.path = Path(path)
        self.tables: dict[str, TableDef] = {}
        self._cancel = cancel
        self._progress = progress
        self._maxv = int(max_value_bytes)
        self._errors = encoding_errors
        self._chunk = max(1, int(_chunk_size))
        self._schema_only = _schema_only
        self._f: BinaryIO | None = None
        self._buf = b""
        self._pos = 0
        self._base = 0  # absolute file offset of self._buf[0]
        self._eof = False
        self._bytes_read = 0
        self._size: int | None = None
        self._started = False

    @property
    def bytes_read(self) -> int:
        return self._bytes_read

    # ------------------------------------------------------------------ buffering

    def _fill(self, want: int | None = None) -> bool:
        """Read at least ``want`` more bytes (default: one chunk). False if nothing was read."""
        if self._eof or self._f is None:
            return False
        want = want or self._chunk
        parts: list[bytes] = []
        got = 0
        while got < want:
            if self._cancel is not None:
                self._cancel.check()
            data = self._f.read(self._chunk)
            if not data:
                self._eof = True
                break
            parts.append(data)
            got += len(data)
            self._bytes_read += len(data)
            if self._progress is not None:
                self._progress(self._bytes_read, self._size)
        buf = self._buf
        if self._pos:
            self._base += self._pos
            buf = buf[self._pos :]
            self._pos = 0
        self._buf = buf + b"".join(parts) if parts else buf
        return got > 0

    def _grow(self) -> bool:
        return self._fill(max(self._chunk, len(self._buf) - self._pos))

    def _abs(self, pos: int | None = None) -> int:
        return self._base + (self._pos if pos is None else pos)

    # ------------------------------------------------------------------ tokenizer

    def _tok(self) -> Token | None:
        """Next significant token (whitespace / comments skipped), or None at EOF."""
        while True:
            buf = self._buf
            pos = self._pos
            n = len(buf)
            if pos >= n:
                if not self._fill():
                    return None
                continue
            m = _TOKEN_RE.match(buf, pos)
            assert m is not None  # noqa: S101 - '.' alternative always matches
            kind = m.lastgroup or "p"
            end = m.end()
            if kind == "open":
                if self._eof:
                    raise _error(
                        "sql.truncated",
                        "unterminated string, identifier or comment",
                        self._abs(pos),
                        _MSG_TRUNCATED,
                    )
                if n - pos > self._maxv + 1024:
                    raise _error(
                        "sql.value_too_large",
                        "string/comment literal exceeds the size limit",
                        self._abs(pos),
                        _MSG_TOO_LARGE,
                    )
                self._grow()
                continue
            if end > n - 3 and not self._eof:
                # token might continue (or a comment opener might be split): need lookahead
                self._grow()
                continue
            self._pos = end
            if kind in _SKIP_TOKENS:
                continue
            return kind, m.group(), self._base + pos

    def _skip_statement(self) -> None:
        while True:
            t = self._tok()
            if t is None or (t[0] == "p" and t[1] == b";"):
                return

    def _collect_statement(self, first: Token | None = None) -> list[Token]:
        toks: list[Token] = []
        size = 0
        t = first if first is not None else self._tok()
        while t is not None and not (t[0] == "p" and t[1] == b";"):
            toks.append(t)
            size += len(t[1])
            if size > _MAX_STATEMENT:
                raise _error("sql.statement_too_large", "schema statement exceeds the size limit", t[2])
            t = self._tok()
        return toks

    # ------------------------------------------------------------------ main loop

    def events(self) -> Iterator[Event]:
        if self._started:
            raise RuntimeError("DumpReader.events() can only be iterated once")
        self._started = True
        with open(self.path, "rb") as f:
            self._f = f
            try:
                self._size = os.fstat(f.fileno()).st_size
            except OSError:  # pragma: no cover
                self._size = None
            self._fill(65536)  # enough to sniff BOM / binary content even with tiny chunks
            head = self._buf[:65536]
            if head.startswith(b"\xef\xbb\xbf"):
                self._pos = 3
            elif head.startswith(b"\x1f\x8b"):
                raise _error("sql.compressed", "file is gzip-compressed, not plain SQL", 0)
            elif head.startswith((b"PK\x03\x04", b"\xff\xfe", b"\xfe\xff")) or b"\x00" in head:
                raise _error("sql.not_text", "file is not a plain-text SQL dump", 0)
            stmt_index = -1
            recognized = 0
            other = 0
            while True:
                t = self._tok()
                if t is None:
                    break
                kind, raw, start = t
                if kind == "p" and raw == b";":
                    continue
                stmt_index += 1
                word = raw.upper() if kind == "word" else b""
                if word in (b"INSERT", b"REPLACE"):
                    recognized += 1
                    if self._schema_only:
                        self._skip_insert_body(start)
                    else:
                        yield from self._insert(word, start, stmt_index)
                elif word == b"CREATE":
                    ev = self._create(start)
                    if ev is not None:
                        recognized += 1
                        yield ev
                elif word == b"ALTER":
                    yield from self._alter(start)
                elif word == b"DELIMITER":
                    yield from self._delimiter(start)
                else:
                    other += 1
                    self._skip_statement()
            if recognized == 0 and other:
                yield WarningEvent(
                    "no_dump_statements",
                    "no CREATE TABLE or INSERT statements were found; this may not be a SQL dump",
                    0,
                )
        self._f = None

    # ------------------------------------------------------------------ DELIMITER

    def _delimiter(self, start: int) -> Iterator[Event]:
        # read the rest of the line raw
        while True:
            nl = self._buf.find(b"\n", self._pos)
            if nl >= 0 or self._eof:
                break
            if len(self._buf) - self._pos > 4096:
                raise _error("sql.malformed", "malformed DELIMITER line", start)
            self._grow()
        end = nl if nl >= 0 else len(self._buf)
        delim = self._buf[self._pos : end].strip()
        self._pos = end + 1 if nl >= 0 else end
        if delim in (b";", b""):
            return
        yield WarningEvent(
            "delimiter_block",
            "DELIMITER block (stored procedure/trigger/event) skipped; it is not converted",
            start,
        )
        while True:
            m = _DELIM_RESTORE_RE.search(self._buf, self._pos)
            if m is not None:
                self._pos = m.end()
                return
            last_nl = self._buf.rfind(b"\n", self._pos)
            if last_nl >= 0:
                self._pos = last_nl + 1
            if not self._fill():
                self._pos = len(self._buf)
                yield WarningEvent("delimiter_unterminated", "DELIMITER was never restored to ';'", start)
                return

    # ------------------------------------------------------------------ identifiers

    def _ident(self, t: Token | None) -> str:
        if t is None:
            raise _error("sql.truncated", "unexpected end of file", self._abs(), _MSG_TRUNCATED)
        kind, raw, off = t
        try:
            if kind == "bq":
                return raw[1:-1].replace(b"``", b"`").decode("utf-8")
            if kind == "word":
                return raw.decode("utf-8")
            if kind == "dq":
                return _unescape_dq(raw[1:-1]).decode("utf-8")
            if kind == "sq":
                return _unescape_sq(raw[1:-1]).decode("utf-8")
        except UnicodeDecodeError:
            raise _error("sql.encoding", "identifier is not valid UTF-8", off, _MSG_ENCODING) from None
        raise _error("sql.malformed", "expected an identifier", off)

    def _qualified_name(self, t: Token | None) -> tuple[str, Token | None]:
        name = self._ident(t)
        nxt = self._tok()
        while nxt is not None and nxt[0] == "p" and nxt[1] == b".":
            name = self._ident(self._tok())
            nxt = self._tok()
        return name, nxt

    # ------------------------------------------------------------------ INSERT

    def _skip_insert_body(self, start: int) -> None:
        while True:
            m = _SKIP_BODY_RE.match(self._buf, self._pos)
            assert m is not None  # noqa: S101 - pattern can match empty
            self._pos = m.end()
            if self._pos < len(self._buf):
                if self._buf[self._pos] == 0x3B:  # ';'
                    self._pos += 1
                    return
                # stopped at an unterminated quote
                if self._eof:
                    raise _error(
                        "sql.truncated", "unterminated string in INSERT", self._abs(), _MSG_TRUNCATED
                    )
                if len(self._buf) - self._pos > self._maxv + 1024:
                    raise _error(
                        "sql.value_too_large", "value exceeds the size limit", self._abs(), _MSG_TOO_LARGE
                    )
                self._grow()
            elif not self._fill():
                return

    def _insert(self, verb: bytes, start: int, sidx: int) -> Iterator[Event]:
        t = self._tok()
        while (
            t is not None
            and t[0] == "word"
            and t[1].upper() in (b"LOW_PRIORITY", b"DELAYED", b"HIGH_PRIORITY", b"IGNORE")
        ):
            t = self._tok()
        if t is not None and t[0] == "word" and t[1].upper() == b"INTO":
            t = self._tok()
        table, t = self._qualified_name(t)
        if t is not None and t[0] == "word" and t[1].upper() == b"PARTITION":
            t = self._tok()
            depth = 0
            while t is not None:
                if t[0] == "p" and t[1] == b"(":
                    depth += 1
                elif t[0] == "p" and t[1] == b")":
                    depth -= 1
                    if depth <= 0:
                        break
                t = self._tok()
            t = self._tok()
        explicit: tuple[str, ...] | None = None
        if t is not None and t[0] == "p" and t[1] == b"(":
            cols: list[str] = []
            nt = self._tok()
            while not (nt is not None and nt[0] == "p" and nt[1] == b")" and not cols):  # "()" = no columns
                cols.append(self._ident(nt))
                sep = self._tok()
                if sep is None:
                    raise _error("sql.truncated", "unexpected end of file in INSERT", start, _MSG_TRUNCATED)
                if sep[0] == "p" and sep[1] == b")":
                    break
                if not (sep[0] == "p" and sep[1] == b","):
                    raise _error("sql.malformed_insert", "malformed INSERT column list", sep[2])
                nt = self._tok()
            explicit = tuple(cols)
            t = self._tok()
        if t is None:
            raise _error("sql.truncated", "unexpected end of file in INSERT", start, _MSG_TRUNCATED)
        if not (t[0] == "word" and t[1].upper() in (b"VALUES", b"VALUE")):
            if t[0] == "word" and t[1].upper() in (b"SELECT", b"SET", b"TABLE", b"WITH"):
                yield WarningEvent("unsupported_insert", "INSERT ... SELECT/SET form skipped", start)
                if not (t[0] == "p" and t[1] == b";"):
                    self._skip_statement()
                return
            raise _error("sql.malformed_insert", "expected VALUES in INSERT", t[2])
        if verb == b"REPLACE":
            yield WarningEvent("replace_into", "REPLACE INTO treated as INSERT", start)
        if explicit is not None:
            columns: tuple[str, ...] | None = explicit
        elif table in self.tables:
            columns = self.tables[table].column_names()
        else:
            columns = None
            yield WarningEvent(
                "unknown_table_columns",
                "INSERT into a table without CREATE TABLE and without column list; positional names used",
                start,
            )
        yield from self._rows(table, columns, start, sidx)

    def _rows(self, table: str, columns: tuple[str, ...] | None, start: int, sidx: int) -> Iterator[Event]:
        vmatch = _VALUE_RE.match
        rsmatch = _ROW_START_RE.match
        rematch = _ROW_END_RE.match
        maxv = self._maxv
        errs = self._errors
        ncols = len(columns) if columns is not None else -1
        row_index = 0
        buf = self._buf
        pos = self._pos
        bulk = _BULK_RE.match
        findall = _LIT_FINDALL_STR_RE.findall
        numstart = _NUM_START
        unesc_str = _unescape_sq_str
        lit_value = self._lit_value
        try:
            while True:
                # ---- fast path: a run of complete rows, each followed by ',' ----
                bend = bulk(buf, pos).end()  # type: ignore[union-attr]
                if bend > pos:
                    # Decode the whole validated run once (C speed); per-value work is then on str.
                    try:
                        region = buf[pos:bend].decode("utf-8")
                        raw_bytes = False
                    except UnicodeDecodeError:
                        region = buf[pos:bend].decode("utf-8", "surrogateescape")
                        raw_bytes = True
                    big = bend - pos > maxv  # only then can a single literal exceed the limit
                    row = []
                    append = row.append
                    for lit in findall(region):
                        if not lit:  # ')' -> row complete
                            nv = len(row)
                            if nv != ncols:
                                if ncols < 0:
                                    columns = tuple(f"c{i}" for i in range(nv))
                                    ncols = nv
                                else:
                                    raise _error(
                                        "sql.column_mismatch",
                                        f"row {row_index} has {nv} values but {ncols} columns are expected",
                                        self._abs(pos),
                                        _MSG_MISMATCH,
                                    )
                            yield RowEvent(table, columns, tuple(row), sidx, row_index)  # type: ignore[arg-type]
                            row_index += 1
                            row = []
                            append = row.append
                            continue
                        c = lit[0]
                        if c == "'":
                            s = lit[1:-1]
                            if big and len(lit) > maxv:
                                raise _error(
                                    "sql.value_too_large",
                                    "value exceeds the size limit",
                                    self._abs(pos),
                                    _MSG_TOO_LARGE,
                                )
                            if "\\" in s:
                                t = s.replace("\\'", "'")
                                s = unesc_str(s) if ("\\" in t or "''" in t) else t
                            elif "''" in s:
                                s = s.replace("''", "'")
                            if raw_bytes:
                                s = s.encode("utf-8", "surrogateescape").decode("utf-8", errs)
                            append(s)
                        elif c in numstart:
                            if lit.isdigit() or (c == "-" and lit[1:].isdigit()):
                                append(int(lit))
                            elif c == "0" and lit[1:2] in ("x", "b"):  # 0x.. / 0b..
                                append(lit_value(lit.encode("ascii"), start))
                            else:
                                append(Decimal(lit))
                        elif (c == "N" or c == "n") and len(lit) == 4 and lit[1] != "'":  # NULL
                            append(None)
                        else:
                            if big and len(lit) > maxv:
                                raise _error(
                                    "sql.value_too_large",
                                    "value exceeds the size limit",
                                    self._abs(pos),
                                    _MSG_TOO_LARGE,
                                )
                            append(lit_value(lit.encode("utf-8", "surrogateescape"), start))
                    pos = bend
                # ---- slow path: one row, with refills; '(' ----
                m = rsmatch(buf, pos)
                if m is None:
                    self._pos = pos
                    if len(buf) - pos < 64 and self._fill():
                        buf, pos = self._buf, self._pos
                        continue
                    raise self._row_error(pos, start, expect_row=True)
                pos = m.end()
                row: list[SqlValue] = []
                if buf[pos - 1] != 0x29:  # not an empty "()" row
                    append = row.append
                    while True:
                        m = vmatch(buf, pos)
                        if m is None:
                            self._pos = pos
                            if self._value_needs_more(pos, start):
                                buf, pos = self._buf, self._pos
                                continue
                            raise self._row_error(pos, start, expect_row=False)
                        end = m.end()
                        li = m.lastindex
                        if li == 1:
                            s = m.group(1)
                            if end - pos > maxv:
                                raise _error(
                                    "sql.value_too_large",
                                    "value exceeds the size limit",
                                    self._abs(pos),
                                    _MSG_TOO_LARGE,
                                )
                            if b"\\" in s or b"''" in s:
                                s = _unescape_sq(s)
                            append(s.decode("utf-8", errs))
                        elif li == 2:
                            append(int(m.group(2)))
                        elif li == 3:
                            append(None)
                        else:
                            if end - pos > maxv:
                                raise _error(
                                    "sql.value_too_large",
                                    "value exceeds the size limit",
                                    self._abs(pos),
                                    _MSG_TOO_LARGE,
                                )
                            append(self._slow_value(m, li, pos))
                        pos = end
                        if buf[end - 1] == 0x29:  # ')'
                            break
                # ---- row complete ----
                if ncols < 0:
                    columns = tuple(f"c{i}" for i in range(len(row)))
                    ncols = len(row)
                if len(row) != ncols:
                    raise _error(
                        "sql.column_mismatch",
                        f"row {row_index} has {len(row)} values but {ncols} columns are expected",
                        self._abs(pos),
                        _MSG_MISMATCH,
                    )
                yield RowEvent(table, columns, tuple(row), sidx, row_index)  # type: ignore[arg-type]
                row_index += 1
                # ---- ',' or ';' ----
                while True:
                    m = rematch(buf, pos)
                    if m is not None:
                        break
                    self._pos = pos
                    if len(buf) - pos < 64 and self._fill():
                        buf, pos = self._buf, self._pos
                        continue
                    break
                if m is None:
                    self._pos = pos
                    yield from self._statement_tail(pos, start)
                    return
                pos = m.end()
                if m.group(1) == b";":
                    self._pos = pos
                    return
        except UnicodeDecodeError:
            raise _error("sql.encoding", "string value is not valid UTF-8", start, _MSG_ENCODING) from None
        finally:
            # keep the reader consistent if the consumer stops early
            if self._buf is buf and self._pos < pos:
                self._pos = pos

    def _statement_tail(self, pos: int, start: int) -> Iterator[Event]:
        rest = self._buf[pos:].lstrip()
        if not rest and self._eof:
            yield WarningEvent("missing_semicolon", "last INSERT statement is not terminated by ';'", start)
            return
        t = self._tok()
        if t is not None and t[0] == "word" and t[1].upper() == b"ON":
            yield WarningEvent("on_duplicate_key", "ON DUPLICATE KEY UPDATE clause ignored", start)
            self._skip_statement()
            return
        if t is None:
            yield WarningEvent("missing_semicolon", "last INSERT statement is not terminated by ';'", start)
            return
        raise _error("sql.malformed_insert", "expected ',' or ';' after a row", t[2])

    def _value_needs_more(self, pos: int, start: int) -> bool:
        """Called when the value regex failed at ``pos``. Reads more data if that may help."""
        if self._eof:
            return False
        buf = self._buf
        remaining = len(buf) - pos
        stripped = buf[pos : pos + 256].lstrip()
        first = stripped[:1]
        could_be_long = first in (b"", b"'", b'"', b"_", b"n", b"N", b"x", b"X", b"b", b"B", b"0")
        if remaining > 256 and not could_be_long:
            return False
        if remaining > self._maxv + 1024:
            raise _error(
                "sql.value_too_large", "value exceeds the size limit", self._abs(pos), _MSG_TOO_LARGE
            )
        return self._grow()

    def _row_error(self, pos: int, start: int, *, expect_row: bool) -> SqlParseError:
        rest = self._buf[pos:].lstrip()
        off = self._abs(pos)
        if self._eof and (not rest or rest[:1] in (b"'", b'"') or rest[:1].isalnum() or rest[:1] == b"_"):
            # data ended inside a row: truncated file
            if not rest or b")" not in rest or rest[:1] in (b"'", b'"'):
                return _error("sql.truncated", "file ends in the middle of an INSERT", off, _MSG_TRUNCATED)
        if expect_row:
            return _error("sql.malformed_insert", "expected '(' to start a row", off)
        return _error("sql.malformed_insert", "unsupported or malformed value in INSERT", off)

    def _lit_value(self, lit: bytes, start: int) -> SqlValue:
        """Convert a raw literal token from the bulk path (all but plain strings/ints/NULL)."""
        c = lit[:1]
        if c in (b"-", b"+", b".") or c.isdigit():
            if lit[:2] in (b"0x", b"0b"):
                body = lit[2:]
                if lit[1:2] == b"b":
                    return _bits_to_bytes(body)
                if len(body) % 2:
                    body = b"0" + body
                return bytes.fromhex(body.decode("ascii"))
            if lit[1:].isdigit():
                return int(lit)
            return Decimal(lit.decode("ascii"))
        if c == b'"':
            g = lit[1:-1]
            if b"\\" in g or b'""' in g:
                g = _unescape_dq(g)
            return g.decode("utf-8", self._errors)
        if c == b"_":
            q = lit.index(b"'")
            charset = lit[1:q].rstrip().lower()
            g = lit[q + 1 : -1]
            if b"\\" in g or b"''" in g:
                g = _unescape_sq(g)
            return g if charset == b"binary" else g.decode("utf-8", self._errors)
        if c in (b"x", b"X"):
            body = lit[2:-1]
            if len(body) % 2:
                body = b"0" + body
            return bytes.fromhex(body.decode("ascii"))
        if c in (b"b", b"B"):
            return _bits_to_bytes(lit[2:-1])
        if c in (b"N", b"n"):
            g = lit[2:-1]
            if b"\\" in g or b"''" in g:
                g = _unescape_sq(g)
            return g.decode("utf-8", self._errors)
        if c in (b"T", b"t"):
            return 1
        if c in (b"F", b"f"):
            return 0
        raise _error("sql.malformed_insert", "unsupported value", start)  # pragma: no cover

    def _slow_value(self, m: re.Match[bytes], li: int, pos: int) -> SqlValue:
        g = m.group(li)
        if li == 4:
            try:
                return Decimal(g.decode("ascii"))
            except InvalidOperation:  # pragma: no cover - regex only admits valid numerals
                raise _error("sql.malformed_insert", "invalid numeric literal", self._abs(pos)) from None
        if li == 5:
            if b"\\" in g or b'""' in g:
                g = _unescape_dq(g)
            return g.decode("utf-8", self._errors)
        if li in (6, 7):
            if len(g) % 2:
                g = b"0" + g
            return bytes.fromhex(g.decode("ascii"))
        if li == 9:
            charset = m.group(8).lower()
            if b"\\" in g or b"''" in g:
                g = _unescape_sq(g)
            if charset == b"binary":
                return g
            return g.decode("utf-8", self._errors)
        if li in (10, 11):
            return _bits_to_bytes(g)
        if li == 12:
            if b"\\" in g or b"''" in g:
                g = _unescape_sq(g)
            return g.decode("utf-8", self._errors)
        if li == 13:
            return 1 if g[:1] in (b"T", b"t") else 0
        raise _error("sql.malformed_insert", "unsupported value", self._abs(pos))  # pragma: no cover

    # ------------------------------------------------------------------ CREATE TABLE

    def _create(self, start: int) -> TableEvent | None:
        t = self._tok()
        while t is not None and t[0] == "word" and t[1].upper() in (b"OR", b"REPLACE", b"TEMPORARY"):
            t = self._tok()
        if t is None:
            return None
        if not (t[0] == "word" and t[1].upper() == b"TABLE"):
            if not (t[0] == "p" and t[1] == b";"):
                self._skip_statement()
            return None
        toks = self._collect_statement()
        i = 0
        if (
            len(toks) >= 3
            and toks[0][0] == "word"
            and toks[0][1].upper() == b"IF"
            and toks[1][1].upper() == b"NOT"
            and toks[2][1].upper() == b"EXISTS"
        ):
            i = 3
        if i >= len(toks):
            raise _error("sql.malformed", "malformed CREATE TABLE", start)
        name = self._ident(toks[i])
        i += 1
        while i + 1 < len(toks) and toks[i][0] == "p" and toks[i][1] == b".":
            name = self._ident(toks[i + 1])
            i += 2
        if i < len(toks) and toks[i][0] == "word" and toks[i][1].upper() == b"LIKE":
            src = self._ident(toks[-1])
            if src in self.tables:
                tdef = replace(self.tables[src], name=name)
                self.tables[name] = tdef
                return TableEvent(tdef)
            return None
        if i >= len(toks) or not (toks[i][0] == "p" and toks[i][1] == b"("):
            return None  # CREATE TABLE ... AS SELECT etc.
        close = _match_paren(toks, i)
        if close < 0:
            raise _error("sql.malformed", "unbalanced parentheses in CREATE TABLE", start)
        columns: list[Column] = []
        pk: tuple[str, ...] = ()
        uniques: list[tuple[str, ...]] = []
        for part in _split_commas(toks, i + 1, close):
            if not part:
                continue
            head = part[0][1].upper() if part[0][0] == "word" else b""
            j = 0
            if head == b"CONSTRAINT":
                j = 1
                if j < len(part) and not (
                    part[j][0] == "word"
                    and part[j][1].upper() in (b"PRIMARY", b"UNIQUE", b"FOREIGN", b"CHECK")
                ):
                    j += 1
                head = part[j][1].upper() if j < len(part) and part[j][0] == "word" else b""
            if head == b"PRIMARY":
                pk = self._key_columns(part, j)
            elif head == b"UNIQUE":
                cols = self._key_columns(part, j)
                if cols and cols not in uniques:
                    uniques.append(cols)
            elif head in (b"KEY", b"INDEX", b"FULLTEXT", b"SPATIAL", b"FOREIGN", b"CHECK", b"PERIOD"):
                continue
            else:
                col, is_pk, is_unique = self._column(part)
                columns.append(col)
                if is_pk:
                    pk = (col.name,)
                if is_unique and (col.name,) not in uniques:
                    uniques.append((col.name,))
        if pk:
            pkset = set(pk)
            columns = [replace(c, nullable=False) if c.name in pkset else c for c in columns]
        engine, charset, collation = _table_options(toks[close + 1 :])
        tdef = TableDef(name, tuple(columns), pk, tuple(uniques), engine, charset, collation)
        self.tables[name] = tdef
        return TableEvent(tdef)

    def _key_columns(self, part: list[Token], j: int) -> tuple[str, ...]:
        k = j
        while k < len(part) and not (part[k][0] == "p" and part[k][1] == b"("):
            k += 1
        if k >= len(part):
            return ()
        close = _match_paren(part, k)
        if close < 0:
            return ()
        names: list[str] = []
        for p in _split_commas(part, k + 1, close):
            if not p or p[0][0] == "p":  # functional key part "((expr))": unsupported
                return ()
            names.append(self._ident(p[0]))
        return tuple(names)

    def _column(self, part: list[Token]) -> tuple[Column, bool, bool]:
        name = self._ident(part[0])
        if len(part) < 2:
            raise _error("sql.malformed", "column definition without type", part[0][2])
        type_text = part[1][1].decode("utf-8", "replace").lower()
        i = 2
        if i < len(part) and part[i][0] == "p" and part[i][1] == b"(":
            close = _match_paren(part, i)
            if close < 0:
                raise _error("sql.malformed", "unbalanced parentheses in column type", part[i][2])
            type_text += _raw_join(part[i : close + 1])
            i = close + 1
        while (
            i < len(part)
            and part[i][0] == "word"
            and part[i][1].upper() in (b"UNSIGNED", b"SIGNED", b"ZEROFILL")
        ):
            type_text += " " + part[i][1].decode("ascii").lower()
            i += 1
        nullable = True
        default: str | None = None
        is_pk = False
        is_unique = False
        charset: str | None = None
        collation: str | None = None
        n = len(part)
        while i < n:
            kind, raw, _ = part[i]
            up = raw.upper() if kind == "word" else b""
            if up in (b"CHARSET", b"CHARACTER", b"COLLATE"):
                j = i + 2 if up == b"CHARACTER" and i + 1 < n and part[i + 1][1].upper() == b"SET" else i + 1
                if up == b"CHARACTER" and j == i + 1:
                    i += 1
                    continue
                value, i = _option_value(part, j)
                if up == b"COLLATE":
                    collation = value
                else:
                    charset = value
                continue
            if up == b"NOT" and i + 1 < n and part[i + 1][1].upper() == b"NULL":
                nullable = False
                i += 2
                continue
            if up == b"NULL":
                nullable = True
            elif up == b"DEFAULT" and i + 1 < n:
                default, i = _default_expr(part, i + 1)
                continue
            elif up == b"PRIMARY":
                is_pk = True
                nullable = False
            elif up == b"UNIQUE":
                is_unique = True
            elif kind == "p" and raw == b"(":
                close = _match_paren(part, i)
                i = close + 1 if close >= 0 else n
                continue
            i += 1
        return Column(name, type_text, nullable, default, charset, collation), is_pk, is_unique

    # ------------------------------------------------------------------ ALTER TABLE

    def _alter(self, start: int) -> Iterator[Event]:
        t = self._tok()
        while t is not None and t[0] == "word" and t[1].upper() in (b"ONLINE", b"OFFLINE", b"IGNORE"):
            t = self._tok()
        if t is None:
            return
        if not (t[0] == "word" and t[1].upper() == b"TABLE"):
            if not (t[0] == "p" and t[1] == b";"):
                self._skip_statement()
            return
        nt = self._tok()
        if nt is not None and nt[0] == "word" and nt[1].upper() == b"IF":  # IF EXISTS
            self._tok()
            nt = self._tok()
        name, t = self._qualified_name(nt)
        if t is None or (t[0] == "p" and t[1] == b";"):
            return
        toks = self._collect_statement(t)
        tdef = self.tables.get(name)
        if tdef is None:
            return
        pk = tdef.primary_key
        uniques = list(tdef.unique_keys)
        for part in _split_commas(toks, 0, len(toks)):
            if len(part) < 2 or part[0][0] != "word":
                continue
            action = part[0][1].upper()
            j = 1
            head = part[j][1].upper() if part[j][0] == "word" else b""
            if action == b"ADD":
                if head == b"CONSTRAINT":
                    j += 1
                    if j < len(part) and not (
                        part[j][0] == "word"
                        and part[j][1].upper() in (b"PRIMARY", b"UNIQUE", b"FOREIGN", b"CHECK")
                    ):
                        j += 1
                    head = part[j][1].upper() if j < len(part) and part[j][0] == "word" else b""
                if head == b"PRIMARY":
                    pk = self._key_columns(part, j) or pk
                elif head == b"UNIQUE":
                    cols = self._key_columns(part, j)
                    if cols and cols not in uniques:
                        uniques.append(cols)
            elif action == b"DROP" and head == b"PRIMARY":
                pk = ()
        if pk != tdef.primary_key or tuple(uniques) != tdef.unique_keys:
            pkset = set(pk)
            cols2 = tuple(replace(c, nullable=False) if c.name in pkset else c for c in tdef.columns)
            new = replace(tdef, columns=cols2, primary_key=pk, unique_keys=tuple(uniques))
            self.tables[name] = new
            yield TableEvent(new)


# --------------------------------------------------------------------------- helpers


def _match_paren(toks: list[Token], i: int) -> int:
    depth = 0
    for k in range(i, len(toks)):
        kind, raw, _ = toks[k]
        if kind == "p":
            if raw == b"(":
                depth += 1
            elif raw == b")":
                depth -= 1
                if depth == 0:
                    return k
    return -1


def _split_commas(toks: list[Token], lo: int, hi: int) -> list[list[Token]]:
    parts: list[list[Token]] = [[]]
    depth = 0
    for k in range(lo, hi):
        t = toks[k]
        if t[0] == "p":
            if t[1] == b"(":
                depth += 1
            elif t[1] == b")":
                depth -= 1
            elif t[1] == b"," and depth == 0:
                parts.append([])
                continue
        parts[-1].append(t)
    return parts


def _raw_join(toks: list[Token]) -> str:
    out = []
    for kind, raw, _ in toks:
        text = raw.decode("utf-8", "replace")
        out.append(text if kind in ("sq", "dq", "bq") else text.lower())
    return "".join(out)


def _option_value(toks: list[Token], i: int) -> tuple[str | None, int]:
    """Value of ``OPTION [=] value`` starting at ``toks[i]`` (lowercased, quotes removed)."""
    if i < len(toks) and toks[i][0] == "p" and toks[i][1] == b"=":
        i += 1
    if i >= len(toks):
        return None, i
    kind, raw, _ = toks[i]
    if kind in ("sq", "dq", "bq"):
        raw = raw[1:-1]
    elif kind != "word":
        return None, i + 1
    return raw.decode("utf-8", "replace").lower(), i + 1


def _table_options(toks: list[Token]) -> tuple[str | None, str | None, str | None]:
    """``(engine, charset, collation)`` from the table options after ``CREATE TABLE … (…)``."""
    engine = charset = collation = None
    i, n = 0, len(toks)
    while i < n:
        kind, raw, _ = toks[i]
        up = raw.upper() if kind == "word" else b""
        if up in (b"PARTITION", b"AS", b"SELECT"):
            break
        if up in (b"ENGINE", b"TYPE"):
            j = i + 1
            if j < n and toks[j][0] == "p" and toks[j][1] == b"=":
                j += 1
            if j < n and toks[j][0] in ("word", "sq", "dq", "bq"):
                r = toks[j][1]
                engine = (r[1:-1] if toks[j][0] != "word" else r).decode("utf-8", "replace")
            i = j + 1
            continue
        if up == b"CHARSET" or (up == b"CHARACTER" and i + 1 < n and toks[i + 1][1].upper() == b"SET"):
            charset, i = _option_value(toks, i + (2 if up == b"CHARACTER" else 1))
            continue
        if up == b"COLLATE":
            collation, i = _option_value(toks, i + 1)
            continue
        i += 1
    return engine, charset, collation


def _default_expr(part: list[Token], i: int) -> tuple[str | None, int]:
    n = len(part)
    kind, raw, _ = part[i]
    if kind == "p" and raw in (b"-", b"+") and i + 1 < n:
        return raw.decode() + part[i + 1][1].decode("utf-8", "replace"), i + 2
    if kind == "p" and raw == b"(":
        close = _match_paren(part, i)
        end = close + 1 if close >= 0 else n
        return _raw_join(part[i:end]), end
    if kind == "word" and raw.upper() == b"NULL":
        return None, i + 1
    text = raw.decode("utf-8", "replace")
    if kind == "word" and i + 1 < n and part[i + 1][0] == "p" and part[i + 1][1] == b"(":
        close = _match_paren(part, i + 1)
        end = close + 1 if close >= 0 else n
        return text + _raw_join(part[i + 1 : end]), end
    return text, i + 1


def scan_schema(
    path: Path, cancel: CancelToken | None = None, progress: ProgressFn | None = None
) -> dict[str, TableDef]:
    """Fast streaming pass returning only the table definitions (INSERT bodies are skipped)."""
    reader = DumpReader(Path(path), cancel=cancel, progress=progress, _schema_only=True)
    for _ in reader.events():
        pass
    return reader.tables
