"""Three fixed issuer filings for 2022 distribution evidence; no market requests."""

from __future__ import annotations

from html.parser import HTMLParser
from typing import TypedDict

from tools.bear_2022_sources import canonical, digest, need

class Document(TypedDict):
    id: str
    url: str
    issuer: str
    candidate_tax_years: list[int]


DOCUMENTS: tuple[Document, ...] = (
    {
        "id": "spy-fy2023",
        "url": "https://www.sec.gov/Archives/edgar/data/884394/000119312523285083/d505786dn30d.htm",
        "issuer": "SPDR S&P 500 ETF Trust",
        "candidate_tax_years": [2022, 2023],
    },
    {
        "id": "sectors-fy2022",
        "url": "https://www.sec.gov/Archives/edgar/data/1064641/000119312522300047/d403933dncsr.htm",
        "issuer": "THE SELECT SECTOR SPDR TRUST",
        "candidate_tax_years": [2022],
    },
    {
        "id": "sectors-fy2024",
        "url": "https://www.sec.gov/Archives/edgar/data/1064641/000119312524270452/d870399dncsr.htm",
        "issuer": "THE SELECT SECTOR SPDR TRUST",
        "candidate_tax_years": [2023],
    },
)
DOCUMENT_LIMIT = 8 * 1024 * 1024
POLICY = {
    "schema": "bear_2022_issuer_documents_v1",
    "maximum_official_requests": 3,
    "maximum_document_bytes": DOCUMENT_LIMIT,
    "archive_blob_bytes": 524288,
    "maximum_parts_per_document": 16,
    "automatic_retry_allowed": False,
    "redirects_allowed": False,
    "unknown_reservation_reset_allowed": False,
    "market_requests": 0,
    "classification_automatically_admitted": False,
    "engine_invoked": False,
    "PIT_verified": False,
}
PLAN_HASH = digest(canonical({"policy": POLICY, "documents": DOCUMENTS}))


def product(identifier):
    matches = [row for row in DOCUMENTS if row["id"] == identifier]
    need(len(matches) == 1, "DOCUMENT_SCOPE")
    return matches[0]


class FilingText(HTMLParser):
    """Extract readable text without executing HTML or requesting subresources."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks = []
        self.hidden = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden.append(tag)
        elif not self.hidden and tag in {"p", "br", "tr", "td", "th", "div"}:
            self.chunks.append(" ")

    def handle_endtag(self, tag):
        if self.hidden and tag == self.hidden[-1]:
            self.hidden.pop()
        elif not self.hidden and tag in {"p", "tr", "td", "th", "div"}:
            self.chunks.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.chunks.append(data)


def evidence(identifier, body):
    """Locate candidate tax sections, not a final distribution classifier."""
    item = product(identifier)
    need(type(body) is bytes and 0 < len(body) <= DOCUMENT_LIMIT, "DOCUMENT_SIZE")
    parser = FilingText()
    parser.feed(body.decode("utf-8", errors="strict"))
    text = " ".join("".join(parser.chunks).split())
    identity = " ".join(text.replace("®", "").split()).casefold()
    need(item["issuer"].casefold() in identity, "DOCUMENT_ISSUER_IDENTITY")
    needle, lower = "tax character", text.casefold()
    starts, cursor = [], 0
    while (position := lower.find(needle, cursor)) >= 0:
        starts.append(position)
        cursor = position + len(needle)
    need(0 < len(starts) <= 16, "DOCUMENT_TAX_SECTION_COUNT")
    sections = [text[max(0, pos - 160) : pos + 6000] for pos in starts]
    return {
        "status": "ISSUER_TAX_SECTIONS_LOCATED_NOT_CLASSIFICATION_ADMISSION",
        "document": item,
        "body_sha256": digest(body),
        "body_bytes": len(body),
        "text_sha256": digest(text.encode()),
        "tax_sections": sections,
        "classification_automatically_admitted": False,
        "engine_invoked": False,
        "PIT_verified": False,
    }
