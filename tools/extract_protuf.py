__author__ = "furylemons"

"""Extract embedded protobuf FileDescriptorProto blobs from a Steam DLL.

Steam ships serialized FileDescriptorProto messages inside steamclient64.dll
and SteamUI.dll. Each blob is published as <name>.proto under
protuf/<side>/, matching the steam-monitor protobuf branch layout.

Usage:
    python3 tools/extract_protuf.py --dll <dll> --side steamclient --out-dir protuf/
"""

import argparse
import sys
from pathlib import Path

from google.protobuf import descriptor_pb2
from google.protobuf.internal.decoder import _DecodeVarint

sys.path.insert(0, str(Path(__file__).parent))
import importlib.util

spec = importlib.util.spec_from_file_location(
    "mp", Path(__file__).parent / "migrate_patterns.py")
mp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mp)

NAME_MARK = b".proto"
MIN_SIZE = 32
MAX_SPAN = 256 * 1024
LABEL = {1: "optional ", 2: "required ", 3: "repeated "}
TYPES = {
    1: "double", 2: "float", 3: "int64", 4: "uint64", 5: "int32",
    6: "fixed64", 7: "fixed32", 8: "bool", 9: "string", 12: "bytes",
    13: "uint32", 15: "sfixed32", 16: "sfixed64", 17: "sint32", 18: "sint64",
}


def _varint(buf: bytes, pos: int):
    try:
        return _DecodeVarint(buf, pos)
    except Exception:
        return None


def _field_ends(buf: bytes, start: int) -> list[int]:
    ends = []
    pos = start
    while pos < len(buf):
        decoded = _varint(buf, pos)
        if decoded is None:
            break
        key, nxt = decoded
        wire = key & 7
        if wire == 0:
            decoded = _varint(buf, nxt)
            if decoded is None:
                break
            _, nxt = decoded
        elif wire == 1:
            nxt += 8
        elif wire == 2:
            decoded = _varint(buf, nxt)
            if decoded is None:
                break
            size, nxt = decoded
            nxt += size
        elif wire == 5:
            nxt += 4
        else:
            break
        if nxt > len(buf):
            break
        ends.append(nxt)
        pos = nxt
    return ends


def iter_descriptors(buf: bytes):
    start = 0
    while True:
        i = buf.find(NAME_MARK, start)
        if i < 0:
            return
        start = i + 1
        end = i + len(NAME_MARK)
        name_start = i
        while name_start > 0 and 32 <= buf[name_start - 1] < 127:
            name_start -= 1
        name_len = end - name_start
        if name_len <= len(NAME_MARK) or name_start < 2:
            continue
        decoded_len = _varint(buf, name_start - 1)
        if decoded_len is None or decoded_len[0] != name_len or decoded_len[1] != name_start:
            continue
        varint_at = name_start - 1
        tag = varint_at - (decoded_len[1] - varint_at)
        if tag < 0 or buf[tag] != 0x0A:
            continue
        best = None
        for stop in _field_ends(buf, tag):
            if stop - tag < MIN_SIZE:
                continue
            if stop - tag > MAX_SPAN:
                break
            fd = descriptor_pb2.FileDescriptorProto()
            try:
                fd.ParseFromString(buf[tag:stop])
            except Exception:
                continue
            if fd.name.endswith(".proto") and fd.syntax:
                best = fd
        if best is not None:
            yield best


def parse_descriptors(buf: bytes) -> dict[str, descriptor_pb2.FileDescriptorProto]:
    found: dict[str, descriptor_pb2.FileDescriptorProto] = {}
    for fd in iter_descriptors(buf):
        prev = found.get(fd.name)
        if prev is None or len(fd.SerializeToString()) > len(prev.SerializeToString()):
            found[fd.name] = fd
    return found


def _render_enum(enum, indent: int) -> list[str]:
    pad = " " * indent
    lines = [f"{pad}enum {enum.name} {{"]
    for value in enum.value:
        lines.append(f"{pad} {value.name} = {value.number};")
    lines.append(f"{pad}}}")
    return lines


def _render_message(message, indent: int) -> list[str]:
    pad = " " * indent
    lines = [f"{pad}message {message.name} {{"]
    for enum in message.enum_type:
        lines += _render_enum(enum, indent + 1)
    for nested in message.nested_type:
        lines += _render_message(nested, indent + 1)
    for field in message.field:
        if field.type in (11, 14):
            kind = field.type_name.lstrip(".")
        else:
            kind = TYPES.get(field.type, "bytes")
        lines.append(
            f"{pad} {LABEL.get(field.label, '')}{kind} {field.name} = {field.number};")
    lines.append(f"{pad}}}")
    return lines


def render_proto(fd: descriptor_pb2.FileDescriptorProto) -> str:
    lines = [f'syntax = "{fd.syntax}";', ""]
    if fd.package:
        lines += [f"package {fd.package};", ""]
    for dep in fd.dependency:
        lines.append(f'import "{dep}";')
    if fd.dependency:
        lines.append("")
    for opt in fd.options.ListFields():
        value = opt[1]
        rendered = "true" if isinstance(value, bool) else f'"{value}"' if isinstance(value, str) else value
        lines.append(f"option {opt[0].name} = {rendered};")
    if fd.options.ListFields():
        lines.append("")
    for enum in fd.enum_type:
        lines += _render_enum(enum, 0)
        lines.append("")
    for message in fd.message_type:
        lines += _render_message(message, 0)
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


def extract(dll: Path, side: str, out_dir: Path) -> int:
    img = mp.Image(dll)
    found = parse_descriptors(img.buf)
    if not found:
        print(f"FAIL {side}: no protobuf descriptors")
        return 1
    root = out_dir / side
    root.mkdir(parents=True, exist_ok=True)
    for name, fd in sorted(found.items()):
        dest = root / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(render_proto(fd), encoding="utf-8", newline="\n")
        print(f"{side}/{name}")
    print(f"wrote {len(found)} protos to {root}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dll", required=True)
    ap.add_argument("--side", required=True, choices=("steamclient", "steamui"))
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    return extract(Path(args.dll), args.side, Path(args.out_dir))


if __name__ == "__main__":
    sys.exit(main())
