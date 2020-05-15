import logging

from sutta_processor.application.check_service import CheckService
from sutta_processor.application.domain_models import (
    BilaraHtmlAggregate,
    BilaraRootAggregate,
)
from sutta_processor.infrastructure.repository.repo import FileRepository
from sutta_processor.shared.config import Config

log = logging.getLogger(__name__)


# noinspection PyDataclass
def bilara_check_html(cfg: Config):
    cfg.repo: FileRepository
    cfg.check: CheckService
    bilara_root: BilaraRootAggregate = cfg.repo.bilara.get_root()
    bilara_html: BilaraHtmlAggregate = cfg.repo.bilara.get_html()
    diff = cfg.check.html.get_missing_segments(
        html_aggregate=bilara_html, base_aggregate=bilara_root
    )
    cfg.repo.generate_diff_feedback_file(diff=diff, name="missing")
    diff = cfg.check.get_surplus_segments(
        check_aggregate=bilara_html, base_aggregate=bilara_root
    )
    cfg.repo.generate_diff_feedback_file(diff=diff, name="surplus")
