__author__ = "furylemons"

"""Seam tests for tools/extract_protuf.py."""
import sys
import unittest
from pathlib import Path

from google.protobuf import descriptor_pb2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import extract_protuf as ep


def sample_descriptor() -> bytes:
    fd = descriptor_pb2.FileDescriptorProto()
    fd.name = "offline_ticket.proto"
    fd.syntax = "proto2"
    msg = fd.message_type.add()
    msg.name = "Offline_Ticket"
    field = msg.field.add()
    field.name = "encrypted_ticket"
    field.number = 1
    field.label = 1
    field.type = 12
    return fd.SerializeToString()


class TestExtract(unittest.TestCase):
    def test_finds_framed_descriptor(self):
        blob = sample_descriptor()
        buf = b"\x00" * 16 + blob + b"\x00" * 8
        found = ep.parse_descriptors(buf)
        self.assertIn("offline_ticket.proto", found)
        self.assertEqual(
            found["offline_ticket.proto"].message_type[0].name, "Offline_Ticket")

    def test_render_matches_schema(self):
        fd = descriptor_pb2.FileDescriptorProto()
        fd.ParseFromString(sample_descriptor())
        text = ep.render_proto(fd)
        self.assertIn('syntax = "proto2";', text)
        self.assertIn("message Offline_Ticket {", text)
        self.assertIn("optional bytes encrypted_ticket = 1;", text)

    def test_ignores_garbage(self):
        self.assertEqual(ep.parse_descriptors(b"\n\x07.proto2" + b"\xff" * 40), {})


if __name__ == "__main__":
    unittest.main()
