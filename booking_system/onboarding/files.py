"""Uploaded files as data: a table (CSV, XLSX: headers and rows of text) for imports, or a document (PDF via pypdf, DOCX via
python-docx, Markdown, text) for SOPs, read on the server into sections. Readers are chosen by the file extension; any other
extension is refused, and so is a PDF without a text layer."""

import base64
import csv
import io
import os
import zipfile
from datetime import date, timedelta
from xml.etree import ElementTree

_SHEET = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
# Built-in Excel number formats that show a date.
_DATE_FORMATS = {14, 15, 16, 17, 22, 27, 30, 36, 45, 46, 47, 50, 57}
_EXCEL_EPOCH = date(1899, 12, 30)
MAX_ROWS = 5000


class Unreadable(Exception):
    pass


def decode(content_base64):
    try:
        return base64.b64decode(str(content_base64 or ""), validate=True)
    except ValueError:
        raise Unreadable("the file content is not base64")


def extension(file_name):
    return os.path.splitext(str(file_name or ""))[1].lower()


def _utf8(data):
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise Unreadable("the file is not UTF-8 text; export it again as UTF-8")


def _table(grid):
    """(headers, rows) from a list of cell lists: the first non-empty row names the columns."""
    filled = [[str(cell or "").strip() for cell in line] for line in grid if any(str(cell or "").strip() for cell in line)]
    if not filled:
        raise Unreadable("the file has no rows")
    width = max(len(line) for line in filled)
    headers = []
    for index, name in enumerate(filled[0] + [""] * (width - len(filled[0]))):
        headers.append(name if name and name not in headers else "column_" + str(index + 1))
    body = filled[1:]
    if len(body) > MAX_ROWS:
        raise Unreadable("the file has " + str(len(body)) + " rows; split it into files of at most " + str(MAX_ROWS))
    return headers, [dict(zip(headers, line + [""] * (width - len(line)))) for line in body]


def _csv(data):
    text = _utf8(data)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        raise Unreadable("the column separator could not be told; export with commas between columns")
    return _table(list(csv.reader(io.StringIO(text), dialect)))


def _column(reference):
    letters = "".join(ch for ch in reference if ch.isalpha())
    index = 0
    for ch in letters.upper():
        index = index * 26 + ord(ch) - 64
    return index - 1


def _date_styles(book):
    """Indexes of cell styles whose number format shows a date."""
    if "xl/styles.xml" not in book.namelist():
        return set()
    root = ElementTree.fromstring(book.read("xl/styles.xml"))
    custom = {int(item.get("numFmtId")): item.get("formatCode", "").lower() for item in root.iter(_SHEET + "numFmt")}
    dated = set(_DATE_FORMATS) | {key for key, code in custom.items() if "y" in code or "d" in code}
    styles = root.find(_SHEET + "cellXfs")
    return {index for index, xf in enumerate(styles if styles is not None else []) if int(xf.get("numFmtId", "0")) in dated}


def _first_sheet(book):
    workbook = ElementTree.fromstring(book.read("xl/workbook.xml"))
    sheet = workbook.find(_SHEET + "sheets")[0]
    rels = ElementTree.fromstring(book.read("xl/_rels/workbook.xml.rels"))
    target = next(rel.get("Target") for rel in rels if rel.get("Id") == sheet.get(_REL + "id"))
    return target.lstrip("/") if target.startswith("/xl/") else "xl/" + target


def _xlsx(data):
    try:
        book = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise Unreadable("the file is not an XLSX workbook")
    shared = []
    if "xl/sharedStrings.xml" in book.namelist():
        for item in ElementTree.fromstring(book.read("xl/sharedStrings.xml")).iter(_SHEET + "si"):
            shared.append("".join(node.text or "" for node in item.iter(_SHEET + "t")))
    dated = _date_styles(book)
    grid = []
    for row in ElementTree.fromstring(book.read(_first_sheet(book))).iter(_SHEET + "row"):
        line = []
        for cell in row.iter(_SHEET + "c"):
            index = _column(cell.get("r", ""))
            line.extend([""] * (index - len(line) + 1))
            kind = cell.get("t", "")
            value = cell.find(_SHEET + "v")
            raw = value.text if value is not None and value.text is not None else ""
            text = {"s": lambda: shared[int(raw)] if raw else "",
                    "inlineStr": lambda: "".join(node.text or "" for node in cell.iter(_SHEET + "t")),
                    "b": lambda: "TRUE" if raw == "1" else "FALSE"}.get(kind, lambda: raw)()
            if not kind and raw and int(cell.get("s", "0")) in dated:
                text = (_EXCEL_EPOCH + timedelta(days=float(raw))).isoformat()
            if not kind and raw.endswith(".0"):
                text = raw[:-2]
            line[index] = text
        grid.append(line)
    return _table(grid)


TABLE_READERS = {".csv": _csv, ".txt": _csv, ".tsv": _csv, ".xlsx": _xlsx}


def read_table(file_name, data):
    reader = TABLE_READERS.get(extension(file_name))
    if not reader:
        raise Unreadable("imports read " + ", ".join(sorted(TABLE_READERS)) + " files")
    return reader(data)


# ---------- documents (SOPs): blocks of (heading or "", text) ----------

def _pdf(data):
    """Each page's text layer. A PDF without one (a scan or a photo) is refused: there is no OCR."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise Unreadable("the PDF is password-protected; export it without a password")
        pages = [page.extract_text() or "" for page in reader.pages]
    except PdfReadError:
        raise Unreadable("the file is not a readable PDF")
    if not "".join(pages).strip():
        raise Unreadable("this PDF has no text layer (it looks scanned or photographed), so its text could not be read; "
                         "send it as a DOCX, a text PDF, or paste the text")
    return [("", page) for page in pages if page.strip()]


def _docx(data):
    """Paragraphs and tables in order; a paragraph styled as a heading or title starts a section."""
    from docx import Document
    from docx.opc.exceptions import PackageNotFoundError
    try:
        document = Document(io.BytesIO(data))
    except (PackageNotFoundError, KeyError, zipfile.BadZipFile):
        raise Unreadable("the file is not a DOCX document")
    blocks = []
    for paragraph in document.paragraphs:
        style = str(paragraph.style.name if paragraph.style is not None else "")
        text = paragraph.text.strip()
        if text and (style.startswith("Heading") or style == "Title"):
            blocks.append((text, ""))
        elif text:
            blocks.append(("", text))
    for table in document.tables:
        rows = [" | ".join(cell.text.strip() for cell in row.cells) for row in table.rows]
        blocks.append(("", "\n".join(row for row in rows if row.strip(" |"))))
    if not [text for heading, text in blocks if (heading or text).strip()]:
        raise Unreadable("the DOCX has no text")
    return blocks


def _markdown(data):
    """A line starting with # starts a section."""
    blocks = []
    for line in _utf8(data).splitlines():
        stripped = line.strip()
        blocks.append((stripped.lstrip("#").strip(), "") if stripped.startswith("#") else ("", line))
    return blocks


DOCUMENT_READERS = {".pdf": _pdf, ".docx": _docx, ".md": _markdown, ".txt": _markdown}
DOCUMENT_MIME = {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                 ".md": "text/markdown", ".txt": "text/plain"}
SECTION_CHARS = 6000
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024


def _pieces(part):
    """A paragraph longer than one section, in order, with nothing dropped."""
    return [part[start:start + SECTION_CHARS] for start in range(0, len(part), SECTION_CHARS)]


def sections(blocks):
    """[{'title', 'text'}]: a section per heading; a section longer than SECTION_CHARS is cut at paragraph ends, then within a paragraph."""
    found = [{"title": "", "parts": []}]
    for heading, text in blocks:
        if heading:
            found.append({"title": heading, "parts": []})
        if text.strip():
            found[-1]["parts"].append(text.strip())
    cut = []
    for item in found:
        current = []
        for part in item["parts"]:
            for piece in _pieces(part):
                if current and len("\n\n".join(current + [piece])) > SECTION_CHARS:
                    cut.append({"title": item["title"], "text": "\n\n".join(current)})
                    current = []
                current.append(piece)
        if current:
            cut.append({"title": item["title"], "text": "\n\n".join(current)})
    return [item for item in cut if item["text"].strip()]


def read_document(file_name, data):
    """{'text', 'sections', 'mime'} of an SOP file, read on the server."""
    kind = extension(file_name)
    reader = DOCUMENT_READERS.get(kind)
    if not reader:
        raise Unreadable("SOP files can be " + ", ".join(sorted(DOCUMENT_READERS)))
    if len(data) > MAX_DOCUMENT_BYTES:
        raise Unreadable("the file is larger than " + str(MAX_DOCUMENT_BYTES // (1024 * 1024)) + " MB")
    found = sections(reader(data))
    if not found:
        raise Unreadable("the file has no text")
    return {"text": "\n\n".join((item["title"] + "\n" if item["title"] else "") + item["text"] for item in found), "sections": found,
            "mime": DOCUMENT_MIME[kind]}


def text_document(text):
    """A procedure pasted in chat, read like a markdown file."""
    found = sections(_markdown(str(text or "").encode("utf-8")))
    return {"text": str(text or "").strip(), "sections": found, "mime": "text/plain"}
