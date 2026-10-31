"""Supplement retained config/dotfiles with explicit encoding and secret masking."""

from __future__ import annotations

import re

from .course_tabular_resources import index_audited_resources

ASSIGNMENT = re.compile(r"^(\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*)(.*)$")
SENSITIVE = re.compile(
    r"key|token|secret|password|passwd|credential|authorization|private|(?:^|_)pwd(?:_|$)",
    re.I,
)


def decode_text(raw):
    if len(raw) > 16 * 1024**2:
        raise ValueError("Supplemental text byte bound exceeded; raw retained")
    encoding = "utf-8-sig"
    if raw.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        encoding = "utf-32"
    elif raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding = "utf-16"
    text = raw.decode(encoding, errors="strict")
    if any(ord(char) < 32 and char not in "\t\r\n" for char in text):
        raise ValueError("Supplemental resource contains binary controls; raw retained")
    return text, encoding


def redact_credentials(text):
    lines = text.splitlines(keepends=True)
    output = []
    redacted = 0
    unterminated = 0
    i = 0

    def close_quote(value, delimiter):
        escaped = False
        for offset, char in enumerate(value):
            if not escaped and value.startswith(delimiter, offset):
                return True
            if char == "\\" and not escaped:
                escaped = True
            else:
                escaped = False
        return False

    while i < len(lines):
        line = lines[i]
        i += 1
        match = ASSIGNMENT.match(line.rstrip("\r\n"))
        if not match or not SENSITIVE.search(match[2]):
            output.append(line)
            continue
        redacted += 1
        value = match[3].lstrip()
        delimiter = value[:1] if value.startswith(("'", '"')) else ""
        if delimiter and value.startswith(delimiter * 3):
            delimiter *= 3
        if delimiter and not close_quote(value[len(delimiter) :], delimiter):
            while i < len(lines):
                continuation = lines[i]
                i += 1
                if close_quote(continuation, delimiter):
                    break
            else:
                unterminated += 1
        output.append(
            match[1] + '"[REDACTED]"' + ("\n" if line.endswith(("\n", "\r")) else "")
        )
    return "".join(output), redacted, unterminated


def text_chunks(raw, checkpoint=lambda: None):
    text, encoding = decode_text(raw)
    text, masked, unterminated = redact_credentials(text)
    chunks = []
    for offset in range(0, len(text), 3000):
        checkpoint()
        chunks.append(("text chars " + str(offset), text[offset : offset + 3000]))
    return chunks, {
        "encoding": encoding,
        "characters_indexed": len(text),
        "credential_assignments_redacted": masked,
        "unterminated_secret_values_masked_through_eof": unterminated,
        "source_code_executed": False,
        "raw_source_retained": True,
    }


def index_text_resources(store, audit_path):
    return index_audited_resources(
        store,
        audit_path,
        parser=text_chunks,
        kind="supplemental-text",
        extractor="extensionless-text-v1/strict-unicode-credential-mask",
        max_bytes=16 * 1024**2,
    )
