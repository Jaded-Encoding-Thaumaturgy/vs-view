from typing import Literal

from pydantic import BaseModel


class GlobalSettings(BaseModel):
    factor: float = 100.0
    bin_res: Literal[0, 256, 512, 1024] = 0
    show_unsafe: bool = True
