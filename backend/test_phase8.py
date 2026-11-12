"""Tests for Phase 8: PDF upload (text only), document chat, and the citation map."""
import io
import json

import pytest
from fastapi.testclient import TestClient

import main
from test_search_filters import FakeResponse
from test_security import FakeGemini, clean_state, limiter, make_user, use_fake_ai  # noqa: F401 (autouse fixture)


# ------------------------------------------------------------ building real PDFs for the tests

def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[list[str]], title: str | None = None) -> bytes:
    """A real, minimal PDF: one page per list, one text line per string (Helvetica). An empty list is a page
    without any text, like a scanned image."""
    objects = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids = []
    pages_id = len(objects) + 1 + 2 * len(pages)  # the /Pages object is added after every page and its stream
    for lines in pages:
        ops = ["BT /F1 11 Tf 50 780 Td 14 TL"] + [f"({_escape(line)}) Tj T*" for line in lines] + ["ET"]
        if not lines:
            ops = ["0.5 g 50 50 400 600 re f"]  # a grey box, no text
        stream = "\n".join(ops).encode("latin-1")
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page_ids.append(add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 842] /Contents %d 0 R "
                            b"/Resources << /Font << /F1 %d 0 R >> >> >>" % (pages_id, content, font)))
    kids = " ".join(f"{i} 0 R" for i in page_ids).encode()
    assert add(b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % len(page_ids)) == pages_id
    catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)
    info = add(b"<< /Title (" + _escape(title).encode("latin-1") + b") >>") if title else None
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    trailer = b"<< /Size %d /Root %d 0 R" % (len(objects) + 1, catalog)
    if info:
        trailer += b" /Info %d 0 R" % info
    out.write(b"trailer\n" + trailer + b" >>\nstartxref\n%d\n%%%%EOF\n" % xref)
    return out.getvalue()


SENTENCE = "Melatonin given to older adults with insomnia shortened sleep onset by fifteen minutes in this trial."
PAGE_TEXT = [SENTENCE] + [f"Line {i}: the randomised placebo controlled design enrolled two hundred participants." for i in range(30)]


def upload(client, pdf: bytes, name="paper.pdf", content_type="application/pdf"):
    limiter().request_history.clear()
    return client.post("/documents", content=pdf, headers={"Content-Type": content_type, "X-Filename": name})


@pytest.fixture(autouse=True)
def clean_documents():
    yield
    db = main.SessionLocal()
    db.query(main.DocumentChunk).delete()
    db.query(main.UploadedDocument).delete()
    db.commit()
    db.close()


# ------------------------------------------------------------ extraction

def test_extract_reads_text_title_and_pages():
    pdf = make_pdf([PAGE_TEXT, ["Second page about sleep-", "less nights and memory."] + ["filler words here"] * 20],
                   title="Melatonin and insomnia in older adults")
    result = main.extract_pdf(pdf)
    assert result["title"] == "Melatonin and insomnia in older adults"
    assert result["page_count"] == 2 and result["words"] > 100
    assert SENTENCE in result["pages"][0][1]
    assert "sleepless" in result["pages"][1][1]  # word split across lines is joined again


@pytest.mark.parametrize("data,message", [
    (b"not a pdf at all", "not a PDF"),
    (b"%PDF-1.4 broken", "could not be read"),
])
def test_extract_rejects_bad_files(data, message):
    with pytest.raises(main.PdfRejected, match=message):
        main.extract_pdf(data)


def test_scanned_pdf_is_refused():
    with pytest.raises(main.PdfRejected, match="scanned"):
        main.extract_pdf(make_pdf([[], [], ["Page 3"]]))


def test_password_protected_pdf_is_refused():
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(make_pdf([PAGE_TEXT]))).pages:
        writer.add_page(page)
    writer.encrypt("secret-password")
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(main.PdfRejected, match="password"):
        main.extract_pdf(buffer.getvalue())


def test_page_limit(monkeypatch):
    monkeypatch.setattr(main, "MAX_PDF_PAGES", 2)
    with pytest.raises(main.PdfRejected, match="3 pages"):
        main.extract_pdf(make_pdf([PAGE_TEXT] * 3))


def test_chunks_keep_page_numbers_and_size():
    chunks = main.chunk_pages([(1, " ".join([SENTENCE] * 40)), (2, "Short page.")])
    assert all(len(text) <= main.CHUNK_CHARS for _, text in chunks)
    assert chunks[0][0] == 1 and chunks[-1] == (2, "Short page.")


def test_rank_chunks_prefers_matching_sections():
    chunks = [{"document_id": 1, "page": 1, "text": "Climate models of rainfall in Lusaka."},
              {"document_id": 1, "page": 2, "text": "Melatonin shortened sleep onset in older adults."},
              {"document_id": 1, "page": 3, "text": "Funding and acknowledgements."}]
    assert main.rank_chunks("Did melatonin help sleep onset?", chunks, top=1)[0]["page"] == 2
    assert len(main.rank_chunks("zzz qqq", chunks, top=2)) == 2  # no overlap: first sections


# ------------------------------------------------------------ upload API

def test_upload_list_preview_delete():
    c, _, _ = make_user()
    r = upload(c, make_pdf([PAGE_TEXT, PAGE_TEXT], title="Melatonin trial"), name="melatonin.pdf")
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["title"] == "Melatonin trial" and doc["pages"] == 2 and doc["filename"] == "melatonin.pdf"
    assert [d["id"] for d in c.get("/documents").json()["documents"]] == [doc["id"]]
    assert SENTENCE in c.get(f"/documents/{doc['id']}").json()["preview"]
    assert c.delete(f"/documents/{doc['id']}").status_code == 200
    assert c.get("/documents").json()["documents"] == []
    db = main.SessionLocal()
    try:
        assert db.query(main.DocumentChunk).count() == 0  # text removed with the document
    finally:
        db.close()


def test_only_text_is_stored_never_the_file():
    c, _, _ = make_user()
    upload(c, make_pdf([PAGE_TEXT]))
    db = main.SessionLocal()
    try:
        stored = " ".join(chunk.text for chunk in db.query(main.DocumentChunk).all())
    finally:
        db.close()
    assert "%PDF" not in stored and "endobj" not in stored and SENTENCE in stored


def test_upload_needs_verified_account():
    assert TestClient(main.app).post("/documents", content=make_pdf([PAGE_TEXT]),
                                     headers={"Content-Type": "application/pdf"}).status_code == 401
    c, _, _ = make_user(verified=False)
    assert upload(c, make_pdf([PAGE_TEXT])).status_code == 403


def test_upload_refusals_have_clear_messages(monkeypatch):
    c, _, _ = make_user()
    r = upload(c, make_pdf([[], []]))
    assert r.status_code == 422 and "scanned" in r.json()["detail"]
    assert upload(c, b"hello", content_type="text/plain").status_code == 415
    assert upload(c, b"").status_code == 400
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 200)
    r = upload(c, make_pdf([PAGE_TEXT]))
    assert r.status_code == 413


def test_uploads_have_their_own_body_limit(monkeypatch):
    c, _, _ = make_user()
    big = make_pdf([PAGE_TEXT] * 20)
    monkeypatch.setattr(main, "MAX_BODY_BYTES", len(big) // 2)  # smaller than the PDF
    assert upload(c, big).status_code == 200  # /documents uses MAX_UPLOAD_BYTES instead
    r = c.post("/collections", content=b'{"name": "' + b"x" * len(big) + b'"}',
               headers={"Content-Type": "application/json"})
    assert r.status_code == 413  # every other route keeps the general limit


def test_document_limit_and_daily_limit(monkeypatch):
    c, _, _ = make_user()
    monkeypatch.setattr(main, "MAX_DOCUMENTS_PER_USER", 1)
    assert upload(c, make_pdf([PAGE_TEXT])).status_code == 200
    assert upload(c, make_pdf([PAGE_TEXT])).status_code == 400
    monkeypatch.setattr(main, "MAX_DOCUMENTS_PER_USER", 50)
    monkeypatch.setattr(main, "MAX_UPLOADS_PER_DAY", 1)
    assert upload(c, make_pdf([PAGE_TEXT])).status_code == 429


def test_documents_are_private():
    owner, _, _ = make_user()
    other, _, _ = make_user()
    doc_id = upload(owner, make_pdf([PAGE_TEXT])).json()["id"]
    assert other.get(f"/documents/{doc_id}").status_code == 404
    assert other.delete(f"/documents/{doc_id}").status_code == 404
    assert other.post("/documents/ask", json={"question": "x", "document_ids": [doc_id]}).status_code == 404
    assert other.get("/documents").json()["documents"] == []


# ------------------------------------------------------------ document chat

def test_ask_documents_cites_document_and_page(monkeypatch):
    c, _, _ = make_user()
    d1 = upload(c, make_pdf([["Introduction"] + ["Background on sleep research."] * 30, PAGE_TEXT],
                            title="Melatonin trial")).json()["id"]
    d2 = upload(c, make_pdf([["Rainfall in Lusaka was measured daily."] * 30], title="Rainfall study")).json()["id"]
    gemini = FakeGemini(text=json.dumps({"answer": "Sleep onset was 15 minutes faster [1].", "citations": [1, 1, 99]}))
    use_fake_ai(monkeypatch, gemini=gemini)
    r = c.post("/documents/ask", json={"question": "How much faster was sleep onset with melatonin?",
                                       "document_ids": [d1, d2]})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["answer"].startswith("Sleep onset") and len(data["citations"]) == 1
    assert data["citations"][0] == {"n": 1, "document_id": d1, "title": "Melatonin trial", "page": 2}
    prompt = gemini.calls[0]["contents"][0].parts[0].text
    assert "fifteen minutes" in prompt  # the matching section was sent
    c.post("/documents/ask", json={"question": "How much faster was sleep onset with melatonin?", "document_ids": [d1, d2]})
    assert len(gemini.calls) == 1  # cached


def test_ask_documents_limits(monkeypatch):
    c, _, _ = make_user()
    use_fake_ai(monkeypatch, gemini=FakeGemini(text='{"answer": "x", "citations": []}'))
    assert c.post("/documents/ask", json={"question": "x", "document_ids": []}).status_code == 422
    assert c.post("/documents/ask", json={"question": "x", "document_ids": [1, 2, 3, 4, 5, 6]}).status_code == 422
    assert c.post("/documents/ask", json={"question": "x", "document_ids": [987654]}).status_code == 404


# ------------------------------------------------------------ citation map

class GraphClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        GraphClient.calls.append((url, dict(params or {})))
        if "/works/doi:" in url:
            if url.endswith("10.1234/missing"):
                return FakeResponse(status_code=404)
            return FakeResponse({"id": "https://openalex.org/W1", "title": "Seed", "publication_year": 2015,
                                 "cited_by_count": 500, "doi": "https://doi.org/10.1/seed",
                                 "referenced_works": ["https://openalex.org/W2", "https://openalex.org/W3", "bad"]})
        if params and str(params.get("filter", "")).startswith("openalex_id:"):
            return FakeResponse({"results": [{"id": "https://openalex.org/W2", "title": "Old A", "cited_by_count": 10},
                                             {"id": "https://openalex.org/W3", "title": "Old B", "cited_by_count": 90}]})
        return FakeResponse({"meta": {"count": 321}, "results": [{"id": "https://openalex.org/W9", "title": "Newer",
                                                                   "cited_by_count": 40}]})


@pytest.fixture()
def graph_client(monkeypatch):
    GraphClient.calls = []
    monkeypatch.setattr(main.httpx, "AsyncClient", GraphClient)
    main.IN_MEMORY_CACHE.clear()
    return GraphClient


def test_citation_graph(graph_client):
    data = TestClient(main.app).get("/graph", params={"doi": "https://doi.org/10.1234/SEED"}).json()
    assert data["seed"]["id"] == "W1" and data["reference_total"] == 2 and data["citing_total"] == 321
    kinds = {n["id"]: n["kind"] for n in data["nodes"]}
    assert kinds == {"W1": "seed", "W3": "reference", "W2": "reference", "W9": "citing"}
    assert [n["id"] for n in data["nodes"] if n["kind"] == "reference"] == ["W3", "W2"]  # most cited first
    assert {"from": "W9", "to": "W1"} in data["edges"] and {"from": "W1", "to": "W2"} in data["edges"]
    assert any("cites:W1" in str(p.get("filter")) for _, p in graph_client.calls)
    calls = len(graph_client.calls)
    TestClient(main.app).get("/graph", params={"doi": "10.1234/seed"})
    assert len(graph_client.calls) == calls  # cached


def test_citation_graph_errors(graph_client):
    assert TestClient(main.app).get("/graph", params={"doi": "10.1234/missing"}).status_code == 404
    assert TestClient(main.app).get("/graph", params={"doi": "not a doi"}).status_code == 400
