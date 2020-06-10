import logging
import pprint
import re
from itertools import zip_longest
from typing import Dict, Optional, Set

from sutta_processor.application.domain_models import (
    BilaraHtmlAggregate,
    BilaraReferenceAggregate,
    BilaraRootAggregate,
    BilaraTranslationAggregate,
    BilaraVariantAggregate,
    YuttaAggregate,
)
from sutta_processor.application.domain_models.base import BaseRootAggregate, BaseVersus
from sutta_processor.application.value_objects.uid import UID, UidKey
from sutta_processor.shared.config import Config
from sutta_processor.shared.false_positives import (
    DUPLICATE_OK_IDS,
    HTML_CHECK_OK_IDS,
    HTML_START_HEADER_OK,
    VARIANT_ARROW_OK_IDS,
    VARIANT_UNKNOWN_OK_IDS,
)

from .base import ServiceBase
from .bd_reference import SCReferenceService
from .concordance import ConcordanceService
from .text_check import CheckText
from .uid_renumber import UidRenumber

log = logging.getLogger(__name__)


class CheckHtml(ServiceBase):
    _MISSING_UIDS = "[%s] There are '%s' UIDs that are in '%s' but missing in the html"
    _MISSING_UIDS_LIST = "[%s] Missing UIDs from html: %s"
    _SURPLUS_UIDS = "[%s] There are '%s' uids in '%s' that are not in the '%s' data"
    _SURPLUS_UIDS_LIST = "[%s] Surplus '%s' UIDs: %s"

    _ignored = HTML_CHECK_OK_IDS

    def get_missing_segments(
        self, html_aggregate: BilaraHtmlAggregate, base_aggregate: BaseRootAggregate
    ) -> set:
        base_uids = set(base_aggregate.index.keys())
        html_uids = set(html_aggregate.index.keys())
        html_missing = base_uids - html_uids
        if html_missing:
            log.error(
                self._MISSING_UIDS, self.name, len(html_missing), base_aggregate.name()
            )
            log.error(self._MISSING_UIDS_LIST, self.name, sorted(html_missing))
        return html_missing

    def get_surplus_segments(
        self,
        check_aggregate: BilaraHtmlAggregate,
        base_aggregate: BaseRootAggregate,
        false_positive: Set[str] = None,
    ) -> set:
        false_positive = false_positive or {}
        base_uids = set(base_aggregate.index.keys())
        html_uids = set(check_aggregate.index.keys())
        html_surplus = html_uids - base_uids

        def is_ignored(uid: UID) -> bool:
            """
            Headings that were added to the translation.
            Filter out headings by removing all entries ending with 0.
            """
            is_added_heading = 0 not in uid.key.seq
            return not is_added_heading or uid in self._ignored.union(false_positive)

        html_wrong = sorted(uid for uid in html_surplus if not is_ignored(uid=uid))
        if html_wrong:
            log.error(
                self._SURPLUS_UIDS,
                self.name,
                len(html_wrong),
                check_aggregate.name(),
                base_aggregate.name(),
            )
            log.error(
                self._SURPLUS_UIDS_LIST,
                self.name,
                check_aggregate.name(),
                sorted(html_wrong),
            )
        return set(html_wrong)

    def is_0_in_header_uid(self, aggregate: BilaraHtmlAggregate) -> Set[UID]:
        error_uids = {}
        prog = re.compile(r"<h\d")
        # We fix only headers which are at the end of segment switch
        candidate_uid: Optional[UID] = None
        for uid, versus in aggregate.index.items():

            if candidate_uid:
                is_section_jump = (
                    uid.strip_last_parts() != candidate_uid.strip_last_parts()
                )
                is_next_seq_one = uid.key.seq[-1] == 1
                if is_section_jump and is_next_seq_one:
                    omg = "[%s] Possible header not starting the section: '%s'"
                    # log.error(omg, self.name, candidate_uid)
                    new_uid = f"{uid.strip_last_parts()}.0"
                    log.error(
                        "old: '%s', curr: '%s', new: '%s'", candidate_uid, uid, new_uid
                    )
                    assert not aggregate.index.get(new_uid)
                    error_uids[candidate_uid] = new_uid
                candidate_uid = None

            if uid in HTML_START_HEADER_OK:
                continue
            elif prog.match(versus.verse) and 0 not in uid.key.seq:
                # omg = "[%s] Possible header not starting the section: '%s'"
                # log.error(omg, self.name, {uid: versus.verse})
                # error_uids.add(uid)
                candidate_uid = uid
        if error_uids:
            omg = "[%s] There are '%s' headers that don't start new section: %s"
            log.error(omg, self.name, len(error_uids), error_uids)
            f_pth_csv = self.cfg.debug_dir / "fixed_uid.csv"
            with open(f_pth_csv, "w") as f:
                for old_uid, new_uid in error_uids.items():
                    f.write(f"{old_uid},{new_uid}\n")
            log.error("File written: '%s'", f_pth_csv)
        return set(error_uids.keys())


class CheckTranslation(ServiceBase):
    _SURPLUS_UIDS = (
        "[%s] There are '%s' UIDs in '%s' lang that are not in the '%s' data"
    )
    _SURPLUS_UIDS_LIST = "[%s] Surplus UIDs in the '%s' lang: %s"

    def get_surplus_segments(
        self,
        translation_aggregate: BilaraTranslationAggregate,
        base_aggregate: BaseRootAggregate,
        false_positive: Set[str] = None,
    ) -> set:
        false_positive = false_positive or {}
        base_uids = set(base_aggregate.index.keys())
        for lang, lang_index in translation_aggregate.index.items():
            tran_uids = set(lang_index.keys())
            tran_surplus = tran_uids - base_uids
            if tran_surplus:
                log.error(
                    self._SURPLUS_UIDS,
                    self.name,
                    len(tran_surplus),
                    lang,
                    base_aggregate.name(),
                )
                log.error(
                    self._SURPLUS_UIDS_LIST, self.name, lang, sorted(tran_surplus)
                )
        return base_uids


class CheckVariant(ServiceBase):
    _MISSING_WORD = "[%s] Word '%s' not found in the base verse: '%s'"
    _MISSING_KEY = "[%s] Key '%s' was not found in '%s'"

    def get_wrong_uid_with_arrow(
        self, aggregate: BilaraVariantAggregate, base_aggregate: BaseRootAggregate,
    ) -> Set[UID]:
        missing_word_keys = set()

        for uid, versus in aggregate.index.items():
            word, *rest = versus.verse.split("→")
            if not rest:
                continue
            word = word.strip()
            try:
                base_verse: str = base_aggregate.index[uid].verse
            except KeyError:
                log.error(self._MISSING_KEY, self.name, uid, base_aggregate.name())
                missing_word_keys.add(uid)
                continue

            if (word not in base_verse) and (uid not in VARIANT_ARROW_OK_IDS):
                log.error(self._MISSING_WORD, self.name, word, {uid: base_verse})
                missing_word_keys.add(uid)

        if missing_word_keys:
            omg = "[%s] Wrong word count: '%s' uids: '%s'"
            log.error(omg, self.name, len(missing_word_keys), missing_word_keys)
        return missing_word_keys

    def get_unknown_variants(self, aggregate: BilaraVariantAggregate) -> Set[UID]:
        unknown_keys = set()
        for uid, versus in aggregate.index.items():
            word, *rest = versus.verse.split("→")
            if rest or uid in VARIANT_UNKNOWN_OK_IDS:
                continue
            unknown_keys.add(uid)

        if unknown_keys:
            msg = "[%s] There are '%s' uids are not validated"
            log.error(msg, self.name, len(unknown_keys))
            values = {k: aggregate.index[k].verse for k in unknown_keys}
            pretty_values = pprint.pformat(values, width=200)
            log.error("[%s] Not valid keys: \n%s", self.name, pretty_values)
        return unknown_keys


class CheckService(ServiceBase):
    _SURPLUS_UIDS = "[%s] There are '%s' uids in '%s' that are not in the '%s' data"
    _SURPLUS_UIDS_LIST = "[%s] Surplus '%s' UIDs: %s"

    def __init__(self, cfg: Config):
        super().__init__(cfg=cfg)
        self.reference = SCReferenceService(cfg=cfg)
        self.concordance = ConcordanceService(cfg=cfg)
        self.html = CheckHtml(cfg=cfg)
        self.translation = CheckTranslation(cfg=cfg)
        self.variant = CheckVariant(cfg=cfg)
        self.text = CheckText(cfg=cfg)
        self.sequence = SequenceCheck(cfg=cfg)
        self.renumber = UidRenumber(cfg=cfg)

    def get_surplus_segments(
        self,
        check_aggregate: BaseRootAggregate,
        base_aggregate: BaseRootAggregate,
        false_positive: Set[str] = None,
    ) -> set:
        false_positive = false_positive or {}
        cb_mapping = {
            BilaraTranslationAggregate: self.translation.get_surplus_segments,
            BilaraHtmlAggregate: self.html.get_surplus_segments,
        }
        callback = cb_mapping.get(type(check_aggregate), self._get_surplus_segments)
        result = callback(
            check_aggregate,
            base_aggregate=base_aggregate,
            false_positive=false_positive,
        )
        return result

    def _get_surplus_segments(
        self,
        check_aggregate: BaseRootAggregate,
        base_aggregate: BaseRootAggregate,
        false_positive: Set[str],
    ) -> set:
        base_uids = set(base_aggregate.index.keys())
        comm_uids = set(check_aggregate.index.keys())
        comm_surplus = comm_uids - base_uids.union(false_positive)
        if comm_surplus:
            log.error(
                self._SURPLUS_UIDS,
                self.name,
                len(comm_surplus),
                check_aggregate.name(),
                base_aggregate.name(),
            )
            log.error(
                self._SURPLUS_UIDS_LIST,
                self.name,
                check_aggregate.name(),
                sorted(comm_surplus),
            )
        return comm_surplus

    def check_uid_sequence_in_file(self, aggregate: BilaraRootAggregate):
        error_keys = set()
        previous_elem = UidKey(":0-0")
        for uid in aggregate.index:
            if not uid.key.is_next(previous=previous_elem):
                error_keys.add(uid)
                msg = "[%s] Sequence error. Previous: '%s' current: '%s'"
                log.error(msg, self.name, previous_elem.raw, uid)
            previous_elem = uid.key
        if error_keys:
            msg = "[%s] There are '%s' sequence key errors"
            log.error(msg, self.name, len(error_keys))

    def get_duplicated_versus_next_to_each_other(
        self, aggregate: BilaraRootAggregate
    ) -> set:
        error_keys = set()
        prev_versus = ""
        for uid, versus in aggregate.index.items():  # type: UID, BaseVersus
            verse = versus.verse.strip()
            if not verse:
                continue
            if verse == prev_versus and uid not in DUPLICATE_OK_IDS:
                error_keys.add(uid)
                msg = "[%s] Same versus next to each other. '%s': '%s'"
                log.error(msg, self.name, uid, verse)
            prev_versus = verse
        if error_keys:
            msg = "[%s] There are '%s' duplicated versus error"
            log.error(msg, self.name, len(error_keys))
            msg = "[%s] dupes UIDs: %s"
            log.error(msg, self.name, sorted(error_keys))
        return error_keys

    def get_empty_verses(self, aggregate: BilaraRootAggregate) -> set:
        error_keys = set()
        pattern = r"(\(\s\)|^\s$)"
        prog = re.compile(pattern)
        for uid, versus in aggregate.index.items():  # type: UID, BaseVersus
            result = prog.match(versus.verse)
            if result:
                error_keys.add(uid)
                msg = "[%s] Key has blank value: '%s': '%s'"
                log.error(msg, self.name, uid, versus.verse)

        if error_keys:
            msg = "[%s] There are '%s' blank versus error"
            log.error(msg, self.name, len(error_keys))
            msg = "[%s] blank UIDs: %s"
            log.error(msg, self.name, sorted(error_keys))
        return error_keys

    def get_unordered_segments(
        self, aggregate: BaseRootAggregate, false_positive: Set[str] = None
    ):
        if isinstance(aggregate, BilaraTranslationAggregate):
            wrong_uids = set()
            for lang, lang_index in aggregate.index.items():
                unordered_seg = self.sequence.get_unordered_segments(index=lang_index)

                if unordered_seg:
                    omg = "[%s] There are '%s' unordered segments for lang: '%s'"
                    log.error(omg, self.name, len(unordered_seg), lang)
                wrong_uids.update(unordered_seg)
            return wrong_uids

        unordered_seg = self.sequence.get_unordered_segments(index=aggregate.index)
        if unordered_seg:
            omg = "[%s] There are '%s' unordered segments: %s"
            log.error(omg, self.name, len(unordered_seg), sorted(unordered_seg))
        return unordered_seg


class SequenceCheck(ServiceBase):
    def get_unordered_segments(self, index: Dict[UID, BaseVersus]) -> Set[UID]:
        wrong_uid = set()
        previous = UidKey(":0-0")
        for uid in index:
            current = uid.key
            if not self.is_key_in_seq(previous, current):
                omg = "[%s] Sequence error. Previous: '%s' current: '%s"
                log.error(omg, self.name, previous.raw, current.raw)
                wrong_uid.add(uid)
            previous = uid.key
        return wrong_uid

    @classmethod
    def is_key_in_seq(cls, previous: UidKey, current: UidKey) -> bool:
        def is_new_file():
            """ Reset sequence when new file. """
            return current.key != previous.key

        def is_same_level():
            return len(current.seq) == len(previous.seq)

        def is_last_gt():
            return current.seq.last > previous.seq.last

        def is_last_lt():
            """When jumping to next, shorter, block."""
            return current.seq.last < previous.seq.last

        def is_second_last_1gt():
            try:
                return current.seq.second_last == previous.seq.second_last + 1
            except ValueError:
                return False

        def is_level_lt():
            return len(current.seq) < len(previous.seq)

        def is_seq_gt():
            for we, them in zip_longest(current.seq, previous.seq):
                try:
                    if we > them:
                        return True
                except TypeError:
                    # Some are baked: '7-8' and they stay in str
                    return False
            return False

        def is_str_head_in_sequence():
            """ Resolve:
            previous: 'mn13:23-28.6' current: 'mn13:29.1'
            Previous: 'mn13:32.5' current: 'mn13:33-35.1'
            Previous: 'mn28:33-34.1' current: 'mn28:35-36.1'
            """
            current_sequence_number = current.seq.head
            previous_sequence_number = previous.seq.head
            if isinstance(current_sequence_number, str):
                current_sequence_number = int(current_sequence_number.split("-")[0])
            if isinstance(previous_sequence_number, str):
                previous_sequence_number = int(previous_sequence_number.split("-")[-1])
            return current_sequence_number == previous_sequence_number + 1

        if is_new_file():
            return True
        elif is_same_level():
            if is_last_gt():
                return True
            if is_second_last_1gt():
                return True
            if is_seq_gt():
                return True
        else:
            if is_seq_gt():
                return True
            if is_level_lt() and is_last_lt():
                # sequence should be shorter and start from 0,1
                return True
        return is_str_head_in_sequence()
