"""Preserve AAC track edit lists while leaving every compressed sample unchanged."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

MAX_MOOV = 128 * 1024**2
CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts"}


@dataclass(frozen=True)
class Atom:
    kind: bytes
    raw: bytes
    header: int

    @property
    def payload(self):
        return self.raw[self.header :]

    def replace(self, payload):
        size = len(payload) + self.header
        header = (
            struct.pack(">I4sQ", 1, self.kind, size)
            if self.header == 16
            else struct.pack(">I4s", size, self.kind)
        )
        return Atom(self.kind, header + payload, self.header)


def atoms(data):
    offset = 0
    while offset < len(data):
        if len(data) - offset < 8:
            raise ValueError("Truncated MP4 atom header")
        size, kind = struct.unpack_from(">I4s", data, offset)
        header = 8
        if size == 1:
            if len(data) - offset < 16:
                raise ValueError("Truncated large MP4 atom")
            size = struct.unpack_from(">Q", data, offset + 8)[0]
            header = 16
        elif size == 0:
            size = len(data) - offset
        if size < header or offset + size > len(data):
            raise ValueError("Invalid MP4 atom bounds")
        yield Atom(kind, data[offset : offset + size], header)
        offset += size


def child(atom, kind):
    matches = [a for a in atoms(atom.payload) if a.kind == kind]
    if len(matches) != 1:
        raise ValueError("Required unique MP4 atom missing: " + kind.decode("ascii"))
    return matches[0]


def replace_child(parent, kind, value):
    found = False
    pieces = []
    for atom in atoms(parent.payload):
        if atom.kind == kind:
            if found:
                raise ValueError("Ambiguous duplicate MP4 child")
            found = True
            pieces.append(value.raw)
        else:
            pieces.append(atom.raw)
    if not found:
        pieces.append(value.raw)
    return parent.replace(b"".join(pieces))


def movie(path, checkpoint=lambda: None):
    path = Path(path)
    length = path.stat().st_size
    with path.open("rb") as stream:
        offset = 0
        while offset < length:
            checkpoint()
            stream.seek(offset)
            header = stream.read(8)
            if len(header) != 8:
                raise ValueError("Truncated MP4 top-level atom")
            size, kind = struct.unpack(">I4s", header)
            header_size = 8
            if size == 1:
                large = stream.read(8)
                if len(large) != 8:
                    raise ValueError("Truncated MP4 large size")
                size = struct.unpack(">Q", large)[0]
                header_size = 16
                header += large
            elif size == 0:
                size = length - offset
            if size < header_size or offset + size > length:
                raise ValueError("Invalid MP4 top-level bounds")
            if kind == b"moov":
                if size > MAX_MOOV:
                    raise ValueError("MP4 metadata exceeds safe bound")
                payload = stream.read(size - header_size)
                if len(payload) != size - header_size:
                    raise ValueError("Truncated MP4 movie")
                return offset, size, Atom(kind, header + payload, header_size)
            offset += size
    raise ValueError("MP4 movie atom not found")


def audio_track(moov, *, audio_only=False):
    tracks = [a for a in atoms(moov.payload) if a.kind == b"trak"]
    audio = [
        a for a in tracks if child(child(a, b"mdia"), b"hdlr").payload[8:12] == b"soun"
    ]
    if len(audio) != 1 or (audio_only and len(tracks) != 1):
        raise ValueError("Expected a unique AAC audio track")
    return audio[0]


def clock(atom):
    payload = atom.payload
    version = payload[0]
    if version not in (0, 1):
        raise ValueError("Unsupported MP4 clock version")
    offset = 20 if version else 12
    scale = struct.unpack_from(">I", payload, offset)[0]
    duration = struct.unpack_from(">Q" if version else ">I", payload, offset + 4)[0]
    if not scale:
        raise ValueError("Invalid zero MP4 timescale")
    return scale, duration


def track_duration(atom):
    payload = atom.payload
    if payload[0] not in (0, 1):
        raise ValueError("Unsupported MP4 track version")
    return struct.unpack_from(
        ">Q" if payload[0] else ">I", payload, 28 if payload[0] else 20
    )[0]


def set_clock(atom, scale, duration):
    payload = bytearray(atom.payload)
    if payload[0] == 0 and duration > 0xFFFFFFFF:
        payload = bytearray(
            bytes([1])
            + bytes(payload[1:4])
            + struct.pack(">QQ", *struct.unpack_from(">II", payload, 4))
            + bytes(payload[12:16])
            + struct.pack(">Q", duration)
            + bytes(payload[20:])
        )
    else:
        struct.pack_into(
            ">Q" if payload[0] else ">I", payload, 24 if payload[0] else 16, duration
        )
    struct.pack_into(">I", payload, 20 if payload[0] else 12, scale)
    return atom.replace(bytes(payload))


def set_track_duration(atom, duration):
    payload = bytearray(atom.payload)
    if payload[0] == 0 and duration > 0xFFFFFFFF:
        payload = bytearray(
            bytes([1])
            + bytes(payload[1:4])
            + struct.pack(">QQ", *struct.unpack_from(">II", payload, 4))
            + bytes(payload[12:20])
            + struct.pack(">Q", duration)
            + bytes(payload[24:])
        )
    else:
        struct.pack_into(
            ">Q" if payload[0] else ">I", payload, 28 if payload[0] else 20, duration
        )
    return atom.replace(bytes(payload))


def edit_entries(edts):
    payload = child(edts, b"elst").payload
    version = payload[0]
    if version not in (0, 1):
        raise ValueError("Unsupported MP4 edit version")
    count = struct.unpack_from(">I", payload, 4)[0]
    size = 20 if version else 12
    if count > 10000 or len(payload) != 8 + count * size:
        raise ValueError("Invalid MP4 edit list bounds")
    return [
        struct.unpack_from(">Qqhh" if version else ">Iihh", payload, 8 + i * size)
        for i in range(count)
    ]


def shift_offsets(atom, old_start, old_end, delta):
    if atom.kind in (b"stco", b"co64"):
        payload = bytearray(atom.payload)
        count = struct.unpack_from(">I", payload, 4)[0]
        width = 8 if atom.kind == b"co64" else 4
        fmt = ">Q" if width == 8 else ">I"
        if len(payload) != 8 + width * count:
            raise ValueError("Invalid MP4 chunk offset table")
        for i in range(count):
            offset = struct.unpack_from(fmt, payload, 8 + i * width)[0]
            if old_start <= offset < old_end:
                raise ValueError("Media chunk incorrectly points inside metadata")
            if offset >= old_end:
                offset += delta
                if offset < 0 or offset >= 1 << (width * 8):
                    raise ValueError("MP4 chunk offset overflow")
                struct.pack_into(fmt, payload, 8 + i * width, offset)
        return atom.replace(bytes(payload))
    if atom.kind in CONTAINERS:
        return atom.replace(
            b"".join(
                shift_offsets(a, old_start, old_end, delta).raw
                for a in atoms(atom.payload)
            )
        )
    return atom


def preserve_audio_edits(source, candidate, destination, checkpoint=lambda: None):
    source = Path(source)
    candidate = Path(candidate)
    destination = Path(destination)
    if (
        source.samefile(candidate)
        or destination.resolve() in (source.resolve(), candidate.resolve())
        or destination.exists()
    ):
        raise ValueError("Audio edit repair requires a separate new output")
    _, _, original = movie(source, checkpoint)
    start, size, target = movie(candidate, checkpoint)
    src = audio_track(original)
    dst = audio_track(target, audio_only=True)
    if (
        clock(child(child(src, b"mdia"), b"mdhd"))[0]
        != clock(child(child(dst, b"mdia"), b"mdhd"))[0]
    ):
        raise ValueError("Audio media timescales differ; retain source for review")
    edits = child(src, b"edts")
    entries = edit_entries(edits)
    # Only ordinary encoder trim plus an optional leading empty edit is handled.
    # Complex splices remain untouched even if a decoder happens to accept them.
    if (
        len(entries) not in (1, 2)
        or any(
            rate != 1 or fraction != 0 or duration <= 0
            for duration, media, rate, fraction in entries
        )
        or entries[-1][1] < 0
        or (len(entries) == 2 and entries[0][1] != -1)
    ):
        raise ValueError("Nonstandard audio playback edits require review")
    scale, _ = clock(child(original, b"mvhd"))
    duration = track_duration(child(src, b"tkhd"))
    dst = replace_child(dst, b"edts", edits)
    dst = replace_child(dst, b"tkhd", set_track_duration(child(dst, b"tkhd"), duration))
    rebuilt = replace_child(target, b"trak", dst)
    rebuilt = replace_child(
        rebuilt, b"mvhd", set_clock(child(rebuilt, b"mvhd"), scale, duration)
    )
    delta = len(rebuilt.raw) - size
    shifted = shift_offsets(rebuilt, start, start + size, delta)
    assert len(shifted.raw) == len(rebuilt.raw)
    with candidate.open("rb") as stream, destination.open("xb") as output:
        remaining = start
        while remaining:
            checkpoint()
            block = stream.read(min(8 * 1024**2, remaining))
            if not block:
                raise ValueError("Truncated audio container while copying prefix")
            output.write(block)
            remaining -= len(block)
        output.write(shifted.raw)
        stream.seek(start + size)
        for block in iter(lambda: stream.read(8 * 1024**2), b""):
            checkpoint()
            output.write(block)
    return {
        "method": "source_audio_edits_and_movie_clock",
        "movie_timescale": scale,
        "track_duration": duration,
        "edit_entries": [list(row) for row in entries],
        "metadata_size_delta": delta,
        "compressed_samples_reencoded": False,
    }
