"""Extract PDF pages using the installed Poppler reader inside the source worker."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from .contracts import KnowledgeError, MAX_FILE, checkpoint


def pages(data, cancelled, deadline):
    executable = shutil.which("pdftotext")
    if not executable:
        raise KnowledgeError("dependency_missing", "PDF 文本提取需要 Poppler pdftotext")
    checkpoint(cancelled, deadline)
    with tempfile.TemporaryDirectory(prefix="sico-knowledge-pdf-") as temporary:
        source, target = Path(temporary) / "input.pdf", Path(temporary) / "output.txt"
        source.write_bytes(data)
        try:
            result = subprocess.run([executable, "-enc", "UTF-8", "-layout", str(source), str(target)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=max(0.1, min(30, deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            raise KnowledgeError("timeout", "PDF 提取超时，上一索引保留") from None
        checkpoint(cancelled, deadline)
        if result.returncode or not target.exists():
            raise KnowledgeError("invalid_pdf", "PDF 加密或无法提取文本，上一索引保留")
        with target.open("rb") as stream:
            raw = stream.read(MAX_FILE + 1)
        if len(raw) > MAX_FILE:
            raise KnowledgeError("file_limit", "PDF 提取文本超过 4 MiB")
        values = raw.decode("utf-8").split("\f")
        if values and not values[-1].strip():
            values.pop()
        if len(values) > 1000:
            raise KnowledgeError("file_limit", "PDF 超过 1000 页")
        return values
