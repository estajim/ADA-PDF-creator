import socket
from pathlib import Path

from ada_pdf.utils.logging import get_logger

try:
    import magic as _magic
    _MAGIC_AVAILABLE = True
except (ImportError, OSError):
    _magic = None  # type: ignore[assignment]
    _MAGIC_AVAILABLE = False

logger = get_logger(__name__)

ALLOWED_MIME_TYPES = {"application/pdf"}
PDF_MAGIC_BYTES = b"%PDF"


class FileValidationError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def validate_pdf_bytes(data: bytes, filename: str, max_bytes: int) -> None:
    if len(data) > max_bytes:
        raise FileValidationError(
            "file_too_large",
            f"File exceeds maximum size of {max_bytes // (1024 * 1024)} MB",
        )

    if not data.startswith(PDF_MAGIC_BYTES):
        raise FileValidationError("not_a_pdf", "File does not appear to be a PDF")

    if _MAGIC_AVAILABLE:
        try:
            mime = _magic.from_buffer(data[:2048], mime=True)
            if mime not in ALLOWED_MIME_TYPES:
                raise FileValidationError("not_a_pdf", f"Unexpected MIME type: {mime}")
        except FileValidationError:
            raise
        except Exception:
            pass  # libmagic unavailable at runtime — magic bytes check already done above


def scan_with_clamav(data: bytes, socket_path: str) -> None:
    """Send file to ClamAV via Unix socket. Raises FileValidationError if virus found."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(socket_path)
            sock.sendall(b"zINSTREAM\0")
            chunk_size = len(data)
            sock.sendall(chunk_size.to_bytes(4, "big") + data)
            sock.sendall(b"\x00\x00\x00\x00")
            response = sock.recv(1024).decode().strip()
        if "FOUND" in response:
            raise FileValidationError("virus_detected", f"ClamAV: {response}")
    except FileValidationError:
        raise
    except Exception as exc:
        logger.warning("clamav_unavailable", error=str(exc))


def validate_upload(data: bytes, filename: str, max_bytes: int, clamav_socket: str | None = None) -> None:
    validate_pdf_bytes(data, filename, max_bytes)
    if clamav_socket:
        scan_with_clamav(data, clamav_socket)
