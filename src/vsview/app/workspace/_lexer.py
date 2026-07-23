from __future__ import annotations

import re
import sys
from collections.abc import Iterable, Iterator
from typing import Any, Protocol, override

from pygments.lexer import Lexer
from pygments.lexers.python import PythonLexer
from pygments.token import Error, Whitespace, _TokenType


class PatternMatch(Protocol):
    def __call__(self, string: str, pos: int = 0, endpos: int = sys.maxsize) -> re.Match[str] | None: ...


class ByGroups(Protocol):
    def __call__(self, lexer: Lexer, match: re.Match[str], ctx: Any | None = None) -> Any: ...


type StateToken = tuple[PatternMatch, _TokenType | ByGroups, str | int | tuple[str, ...] | None]


class StatefulPythonLexer(PythonLexer):
    _tokens: dict[str, list[StateToken]]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.last_state_stack: tuple[str, ...] = ("root",)

    @override
    def get_tokens_unprocessed(
        self,
        text: str,
        stack: Iterable[str] = ("root",),
    ) -> Iterator[tuple[int, _TokenType, str]]:
        pos = 0
        tokendefs = self._tokens
        statestack = list(stack)
        statetokens = tokendefs[statestack[-1]]

        while True:
            for rexmatch, action, new_state in statetokens:
                if not (m := rexmatch(text, pos)):
                    continue

                if isinstance(action, _TokenType):
                    yield pos, action, m.group()
                else:
                    yield from action(self, m)

                pos = m.end()
                if new_state is None:
                    break

                match new_state:
                    case tuple():
                        for state in new_state:
                            match state:
                                case "#pop" if len(statestack) > 1:
                                    statestack.pop()
                                case "#push":
                                    statestack.append(statestack[-1])
                                case _:
                                    statestack.append(state)
                    case int():
                        if abs(new_state) >= len(statestack):
                            del statestack[1:]
                        else:
                            del statestack[new_state:]
                    case "#push":
                        statestack.append(statestack[-1])
                    case _:
                        raise ValueError(f"wrong state def: {new_state!r}")

                statetokens = tokendefs[statestack[-1]]
                break
            else:
                try:
                    if text[pos] == "\n":
                        statestack = ["root"]
                        statetokens = tokendefs["root"]
                        yield pos, Whitespace, "\n"
                        pos += 1
                        continue
                    yield pos, Error, text[pos]
                    pos += 1
                except IndexError:
                    break
        self.last_state_stack = tuple(statestack)
