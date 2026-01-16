from logging import getLogger

logger = getLogger(__name__)


def gc_collect() -> None:
    logger.debug("Running garbage collection")
    import gc

    for i in range(3):
        gc.collect(generation=i)

    for _ in range(3):
        gc.collect()
