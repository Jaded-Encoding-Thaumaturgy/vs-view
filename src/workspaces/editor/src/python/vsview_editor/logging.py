from logging import Logger, LogRecord, getLogger

from jetpytools import SPath


def context_filter(record: LogRecord) -> bool:
    source: str | None = getattr(record, "js_source", None)
    line: int | None = getattr(record, "js_lineno", None)

    if source and line:
        record.pathname = (SPath(__file__).parent / "web_dist" / "assets" / source).to_str()
        record.filename = source
        record.module = source
        record.lineno = line

    return True


def setup_js_logger() -> Logger:
    logger = getLogger("vsview_editor_js")

    for h in logger.handlers[:]:
        logger.removeHandler(h)
    for f in logger.filters[:]:
        logger.removeFilter(f)

    logger.addFilter(context_filter)
    return logger


js_logger = setup_js_logger()
