from vsview.api import hookimpl

from .plugin import AudioConvert


@hookimpl(trylast=True)
def vsview_get_audio_processor() -> type[AudioConvert] | None:
    return AudioConvert
