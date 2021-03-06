import logging
from typing import List, Tuple

from lxml.etree import _Element, _ElementTree

from sutta_processor.application.value_objects import (
    MsId,
    MsVerse,
    PaliCrumb,
    PaliMsDivId,
)

log = logging.getLogger(__name__)


class PaliHtmlExtractor:
    @classmethod
    def get_crumb(cls, page: _ElementTree) -> PaliCrumb:
        last_href: _Element = page.xpath("//CRUMBS/a")[-1]
        pali_type = PaliCrumb(last_href.get("href"))
        return pali_type

    @classmethod
    def get_paragraphs(cls, page: _ElementTree) -> List[_Element]:
        return page.xpath("//body//p")

    @classmethod
    def get_ms_msdiv(cls, paragraph: _Element) -> Tuple[MsId, PaliMsDivId]:
        a_ms = paragraph.xpath("./a[@class='ms']")[0]
        ms_id = MsId.from_xml_id(a_ms.get("id", ""))
        msdiv_id = PaliMsDivId("")
        try:
            a_msdiv = paragraph.xpath("./a[@class='msdiv']")[0]
            msdiv_id = PaliMsDivId(a_msdiv.get("id", "").strip())
        except IndexError:
            log.trace("No msdiv if for ms: '%s'", ms_id)
        return ms_id, msdiv_id

    @classmethod
    def get_verse(cls, paragraph: _Element) -> MsVerse:
        text = paragraph.xpath("./text()")
        text = text[0] if text else ""
        versus = MsVerse(text.strip())
        return versus
