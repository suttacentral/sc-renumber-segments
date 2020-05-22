import logging

from sutta_processor.application.check_service import CheckService
from sutta_processor.application.domain_models import (
    BilaraRootAggregate,
    PaliCanonAggregate,
)
from sutta_processor.infrastructure.repository.repo import FileRepository
from sutta_processor.shared.config import Config

log = logging.getLogger(__name__)


# noinspection PyDataclass
def ms_palicanon_load(cfg: Config):
    cfg.repo: FileRepository
    cfg.check: CheckService
    # root: BilaraRootAggregate = cfg.repo.bilara.get_root()
    # cfg.repo.dump_pickle(aggregate=root)
    # pali: PaliCanonAggregate = cfg.repo.get_all_pali_canon()
    # cfg.repo.dump_pickle(aggregate=pali)
    root: BilaraRootAggregate = cfg.repo.load_pickle(aggregate_cls=BilaraRootAggregate)
    pali: PaliCanonAggregate = cfg.repo.load_pickle(aggregate_cls=PaliCanonAggregate)

    cfg.check.text.get_missing_text(root=root, pali=pali)
