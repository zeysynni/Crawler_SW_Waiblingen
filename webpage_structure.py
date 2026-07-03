from pydantic import BaseModel, Field
from typing import List, Optional

class QA(BaseModel):
    question: str = Field(
        description=(
            "The visible heading or question text of a clickable expandable element (plus button, arrow, accordion). "
            "Click the element first, wait 1 second, then copy the text EXACTLY as shown on the webpage. "
            "Never invent or paraphrase."
        )
    )
    answer: str = Field(
        description=(
            "The hidden content revealed AFTER clicking the expandable element. "
            "Wait 1 second after clicking before reading. "
            "Copy EXACTLY as shown – never use the question text as the answer. "
        )
    )

class FAQ(BaseModel):
    title: Optional[str] = Field(
        default=None,
        description=(
            "The visible section title if present. "
            "This section can have many names: 'FAQ', 'Häufige Fragen', 'Allgemeine Informationen', "
            "'Hinweis', 'Wichtige Informationen', 'Sie haben Fragen?', or any section "
            "where content is hidden behind clickable plus buttons or arrows. "
            "Copy exactly as shown."
        )
    )
    QAs: List[QA] = Field(
        description=(
            "List of all expandable elements on this page – regardless of section name. "
            "ANY clickable plus button, arrow, or accordion element belongs here. "
            "This includes: FAQ questions, Hinweise, Allgemeine Informationen, "
            "technical details, pricing info, or ANY other expandable content. "
            "If you see a clickable plus or arrow anywhere on the page, it belongs here. "
            "Click EVERY single one – none may be skipped."
        )
    )

class ContentSegment(BaseModel):
    subheading: Optional[str] = Field(
        default=None,
        description=(
            "A visible subheading inside this Block. "
            "Smaller than the heading but larger than normal text. "
            "Use markdown. "
            "FAQ doesn't belong to this. "
        )
    )
    text: Optional[str] = Field(
        default=None,
        description=(
            "Descriptive text content. NOT for: FAQs, files, or contact info. "
            "Copy exactly as shown — do not summarize, shorten, or paraphrase. "
            "PRESERVE the page's original formatting as Markdown: keep "
            "**bold**, and especially bullet or numbered LISTS — render each "
            "list item as its own '- ' or '1.' line, never as one run-on line."
        )
    )
    files: Optional[str] = Field(
        default=None,
        description=(
            "Names of downloadable files listed in this area (mostly PDFs). "
            "List each file name on a new line."
        ),
        json_schema_extra={"example": "Preisblatt_2024.pdf\nAGB.pdf"}
    )

    contacts: Optional[str] = Field(
        default=None,
        description=(
            "Contact information found in this segment. "
            "Include phone numbers and what they are for. "
            "Ignore email addresses. "
            "Copy exactly as shown. Do not summarize or paraphrase."
        )
    )
    faqs: Optional[FAQ] = Field(
        default=None,
        description=(
            "FAQ or expandable Q&A section found in this segment. "
            "Use this whenever there are clickable plus buttons, arrows, or accordion elements."
        )
    )

class Block(BaseModel):
    heading: str = Field(
        description=(
            "The visible title of a colored container/box on the page. "
            "A new Block starts every time a new colored container or visually distinct section begins. "
            "Usually the largest or most prominent text in the container, often colored or bold. "
            "If the container has no visible title, use the first prominent text inside it. "
            "Do not invent headings. Do not merge multiple containers into one Block. "
            "Use Markdown (## or ###)."
        )
    )
    segments: List[ContentSegment] = Field(
        description=(
            "All content inside this container. "
            "A segment represents one type of content: FAQs, table, contacts, files, or text. "
            "One block can have multiple segments of different types. "
            "Always check for FAQs and expandable elements first before using text."
        )
    )

class Page(BaseModel):
    url: str = Field(
        description=(
            "The actual URL of this webpage. "
            "Update this every time you navigate to a new page. "
            "Copy the real URL from the browser – never invent it."
        )
    )
    blocks: List[Block] = Field(
        description=(
            "One Block per colored container/box on the page. "
            "A page with 4 containers = 4 Block objects. "
            "Start a new Block every time you see a new colored container or visually distinct section. "
            "Do not merge multiple containers into one Block. "
        )
    )

class Webpages(BaseModel):
    base_url: str = Field(
        description="The starting URL provided in the user prompt."
    )
    pages: List[Page] = Field(
        description=(
            "One Page object for every webpage visited. "
            "The base page is the first Page. "
            "If you click on a button on the base page to enter another page, create a new Page object. "
            "In each page, crawl from top to bottom, don't ignore anything."
            "Use markdown format everywhere to keep the original look. "
        )
    )
