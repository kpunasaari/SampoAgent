"""Optional local OCR providers for scanned CV PDFs."""

from pathlib import Path
import os
import re
import shutil
from typing import Protocol


class OCRProviderUnavailable(RuntimeError):
    """A selected OCR backend or one of its local dependencies is unavailable."""


class OCRProvider(Protocol):
    name: str

    def extract_pdf(self, path: Path) -> str: ...


class TesseractOCRProvider:
    name = "tesseract"

    def __init__(self, *, languages: str = "eng+fin+swe", executable: str = "") -> None:
        if not re.fullmatch(r"[a-z]{3}(?:\+[a-z]{3})*", languages):
            raise OCRProviderUnavailable("OCR languages must be three-letter Tesseract language codes joined with '+'.")
        self.languages = languages
        self.executable = executable

    def extract_pdf(self, path: Path) -> str:
        executable = self.executable or os.environ.get("SAMPOAGENT_TESSERACT_CMD", "tesseract")
        if not shutil.which(executable) and not Path(executable).is_file():
            raise OCRProviderUnavailable("Local Tesseract is not installed or SAMPOAGENT_TESSERACT_CMD does not point to it. Install Tesseract and the selected language packs, or enter CV text manually.")
        try:
            import pypdfium2 as pdfium
            import pytesseract
        except ImportError as exc:
            raise OCRProviderUnavailable("Install SampoAgent's optional OCR dependencies with `pip install -e .[ocr]`, then install local Tesseract.") from exc

        pytesseract.pytesseract.tesseract_cmd = executable
        try:
            document = pdfium.PdfDocument(str(path))
            if len(document) > 50:
                raise OCRProviderUnavailable("CV OCR is limited to 50 PDF pages; select a shorter document or enter the relevant text manually.")
            pages: list[str] = []
            for page_index in range(len(document)):
                bitmap = document[page_index].render(scale=2.0)
                text = pytesseract.image_to_string(bitmap.to_pil(), lang=self.languages, timeout=35)
                pages.append(text)
            result = "\f".join(pages)
        except OCRProviderUnavailable:
            raise
        except Exception as exc:
            raise OCRProviderUnavailable("Local OCR could not read this PDF. Check the Tesseract language packs or enter the CV text manually.") from exc
        if not result.replace("\f", "").strip():
            raise OCRProviderUnavailable("OCR found no readable text. Check scan quality or enter the CV text manually.")
        return result


def get_ocr_provider(provider: str | None = None) -> OCRProvider | None:
    """Select no OCR, an environment-configured provider, or a local backend."""
    selected = (provider if provider is not None else os.environ.get("SAMPOAGENT_OCR_PROVIDER", "none")).strip().casefold()
    if selected in {"", "none", "off"}:
        return None
    if selected == "environment":
        selected = os.environ.get("SAMPOAGENT_OCR_PROVIDER", "none").strip().casefold()
        if selected in {"", "none", "off", "environment"}:
            return None
    if selected != "tesseract":
        raise OCRProviderUnavailable("Unsupported OCR provider. Choose none, environment, or tesseract.")
    languages = os.environ.get("SAMPOAGENT_OCR_LANGUAGES", "eng+fin+swe")
    executable = os.environ.get("SAMPOAGENT_TESSERACT_CMD", "")
    return TesseractOCRProvider(languages=languages, executable=executable)
