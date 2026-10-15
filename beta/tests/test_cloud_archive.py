"""Bounded ZIP parsing regressions, including dishonest sizes and directory counts."""

import io
import struct
import unittest
import zipfile
import zlib
from unittest.mock import patch

from makeghrepo_beta import cloud_contract as contract


def archive_bytes(contents, compression=zipfile.ZIP_DEFLATED, *, zip64=False, stream=False):
    class Streaming(io.BytesIO):
        def seekable(self):
            return False

        def seek(self, *args):
            raise OSError("streaming writer")

    output = Streaming() if stream else io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=compression) as archive:
        for name, content in contents.items():
            entry = zipfile.ZipInfo(name)
            entry.compress_type = compression
            with archive.open(entry, "w", force_zip64=zip64) as member:
                member.write(content)
    return output.getvalue()


def forge_size(data, size, crc):
    data = bytearray(data)
    directory = data.index(b"PK\x01\x02")
    struct.pack_into("<I", data, 14, crc)
    struct.pack_into("<I", data, 22, size)
    struct.pack_into("<I", data, directory + 16, crc)
    struct.pack_into("<I", data, directory + 24, size)
    return bytes(data)


class CloudArchiveTests(unittest.TestCase):
    def test_forged_expansion_is_not_silently_truncated(self):
        payload = b"A" * 1_000_000
        for compression in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            original = archive_bytes({"payload": payload}, compression)
            for size in (0, 1, 4096):
                forged = forge_size(original, size, zlib.crc32(payload[:size]))
                with self.subTest(compression=compression, size=size):
                    # ZipExtFile also truncates in chunked mode, with a matching
                    # forged prefix CRC. It is not an expanded-size validator.
                    with zipfile.ZipFile(io.BytesIO(forged)) as archive, archive.open("payload") as member:
                        chunks = iter(lambda member=member: member.read(257), b"")
                        self.assertEqual(b"".join(chunks), payload[:size])
                    with self.assertRaisesRegex(ValueError, "size"):
                        contract.read_archive(forged)

    def test_honest_and_forged_directory_floods_fail_before_zipfile_allocation(self):
        data = archive_bytes({f"file-{i}": b"" for i in range(contract.MAX_FILES + 1)})
        forged = bytearray(data)
        end = len(data) - 22
        struct.pack_into("<2H", forged, end + 8, 1, 1)
        for variant in (data, bytes(forged)):
            with (
                self.subTest(forged=variant != data),
                patch.object(zipfile, "ZipFile", side_effect=AssertionError("allocated entries")),
                self.assertRaisesRegex(ValueError, "file count"),
            ):
                contract.read_archive(variant)

    def test_directory_byte_limit_precedes_zipfile_allocation(self):
        data = archive_bytes({"payload": b"ok"})
        with (
            patch.object(contract, "MAX_DIRECTORY_BYTES", 46),
            patch.object(zipfile, "ZipFile", side_effect=AssertionError("allocated entries")),
            self.assertRaisesRegex(ValueError, "directory exceeds"),
        ):
            contract.read_archive(data)

    def test_compressed_input_limit_precedes_directory_parsing(self):
        data = archive_bytes({"payload": b"ok"})
        with (
            patch.object(contract, "MAX_BYTES", len(data) - 1),
            patch.object(zipfile, "ZipFile", side_effect=AssertionError("allocated entries")),
            self.assertRaisesRegex(ValueError, "archive exceeds"),
        ):
            contract.read_archive(data)
        with patch.object(contract, "MAX_BYTES", len(data)):
            self.assertEqual(contract.read_archive(data)["payload"][0], b"ok")

    def test_member_and_aggregate_expansion_limits(self):
        one = archive_bytes({"payload": b"A" * 2048})
        with (
            patch.object(contract, "MAX_MEMBER_BYTES", 1024),
            self.assertRaisesRegex(ValueError, "member exceeds"),
        ):
            contract.read_archive(one)
        two = archive_bytes({"first": b"A" * 1024, "second": b"B" * 1024})
        with (
            patch.object(contract, "MAX_MEMBER_BYTES", 1024),
            patch.object(contract, "MAX_BYTES", 2047),
            self.assertRaisesRegex(ValueError, "expanded archive too large"),
        ):
            contract.read_archive(two)
        with (
            patch.object(contract, "MAX_MEMBER_BYTES", 1024),
            patch.object(contract, "MAX_BYTES", 2048),
        ):
            self.assertEqual(sum(len(v[0]) for v in contract.read_archive(two).values()), 2048)

    def test_decompressor_outputs_are_bounded_even_with_forged_size(self):
        payload = b"A" * 1_000_000
        data = forge_size(archive_bytes({"payload": payload}), 1, zlib.crc32(b"A"))
        real_decoder = zlib.decompressobj
        observed = []

        class BoundedDecoder:
            def __init__(self, *args):
                self.inner = real_decoder(*args)

            def __getattr__(self, name):
                return getattr(self.inner, name)

            def decompress(self, block, max_length=0):
                self_test.assertGreater(max_length, 0)
                self_test.assertLessEqual(max_length, contract.ZIP_CHUNK)
                output = self.inner.decompress(block, max_length)
                observed.append(len(output))
                return output

            def flush(self, *args):
                raise AssertionError("unbounded decompressor flush")

        self_test = self
        with (
            patch.object(zlib, "decompressobj", BoundedDecoder),
            self.assertRaisesRegex(ValueError, "size"),
        ):
            contract.read_archive(data)
        self.assertEqual(observed, [2])

    def test_corrupt_truncated_and_trailing_deflate_data_fail(self):
        original = archive_bytes({"payload": b"A" * 8192})
        directory = original.index(b"PK\x01\x02")
        compressed = struct.unpack_from("<I", original, directory + 20)[0]
        for delta in (-1, 1):
            altered = bytearray(original)
            struct.pack_into("<I", altered, directory + 20, compressed + delta)
            with self.subTest(delta=delta), self.assertRaises(ValueError):
                contract.read_archive(bytes(altered))
        corrupted = bytearray(original)
        corrupted[14] ^= 1
        corrupted[directory + 16] ^= 1
        with self.assertRaisesRegex(ValueError, "CRC"):
            contract.read_archive(bytes(corrupted))
        # Include a trailing byte inside the declared compressed member range.
        trailing = bytearray(original[:directory] + b"x" + original[directory:])
        struct.pack_into("<I", trailing, directory + 1 + 20, compressed + 1)
        struct.pack_into("<I", trailing, len(trailing) - 22 + 16, directory + 1)
        with self.assertRaisesRegex(ValueError, "trailing compressed"):
            contract.read_archive(bytes(trailing))

    def test_directory_bounds_counts_and_variable_fields_are_checked(self):
        data = archive_bytes({"payload": b"ok"})
        directory = data.index(b"PK\x01\x02")
        end = len(data) - 22
        for offset, fmt, value in (
            (end + 16, "I", directory + 1),
            (end + 8, "2H", (2, 2)),
            (directory + 28, "H", 65535),
        ):
            altered = bytearray(data)
            values = value if isinstance(value, tuple) else (value,)
            struct.pack_into("<" + fmt, altered, offset, *values)
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                contract.read_archive(bytes(altered))
        for altered in (data[:-1], data + b"junk", b"prefix" + data):
            with self.subTest(data=altered), self.assertRaises(ValueError):
                contract.read_archive(altered)

    def test_streaming_zip64_local_headers_and_bounded_multichunk_members(self):
        payload = bytes(range(256)) * 1024
        for compression in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            for zip64 in (False, True):
                for stream in (False, True):
                    with self.subTest(compression=compression, zip64=zip64, stream=stream):
                        data = archive_bytes(
                            {"payload": payload}, compression, zip64=zip64, stream=stream
                        )
                        self.assertEqual(contract.read_archive(data)["payload"][0], payload)

    def test_zip64_directory_counts_are_bounded(self):
        ordinary = archive_bytes({"payload": b"ok"})
        end = len(ordinary) - 22
        size, offset = struct.unpack_from("<2I", ordinary, end + 12)
        record = struct.pack("<4sQ2H2I4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, 1, 1, size, offset)
        locator = struct.pack("<4sIQI", b"PK\x06\x07", 0, end, 1)
        data = ordinary[:end] + record + locator + ordinary[end:]
        self.assertEqual(contract.read_archive(data)["payload"][0], b"ok")
        altered = bytearray(data)
        struct.pack_into("<2Q", altered, end + 24, contract.MAX_FILES + 1, contract.MAX_FILES + 1)
        struct.pack_into("<2H", altered, len(altered) - 22 + 8, 65535, 65535)
        with (
            patch.object(zipfile, "ZipFile", side_effect=AssertionError("allocated entries")),
            self.assertRaisesRegex(ValueError, "file count"),
        ):
            contract.read_archive(bytes(altered))


if __name__ == "__main__":
    unittest.main()
