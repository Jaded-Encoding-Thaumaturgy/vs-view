# Source - https://stackoverflow.com/a
# Posted by Phoenix87
# Retrieved 2026-01-07, License - CC BY-SA 4.0

# importtime.py

from argparse import ArgumentParser
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

microseconds = int

# uv run python -X importtime -c "from vsview.cli import vsview_cli; vsview_cli()" 2> imports.txt && uv run python -m importtime imports.txt > imports.html  # noqa: E501


@dataclass
class Import:
    module: str
    self: microseconds
    cumulative: microseconds
    level: int
    dependencies: list["Import"] = field(default_factory=list)

    @classmethod
    def parse(cls, line: str) -> "Import":
        assert line.startswith("import time:")

        data = line[len("import time:") :]
        self, cumulative, text = data.split(" | ", 2)
        n = len(text)
        name = text.lstrip()
        level = (n - len(name)) // 2

        return cls(name, int(self), int(cumulative), level)

    def html(self, total: int) -> str:
        data = f"""<progress value="{self.cumulative}" max="{total}" style="width: 2em;"></progress>
            <code>{self.module}</code>
            <span style="color: #888; font-size:small; font-family: arial;">{self.cumulative / 1000:.3f} ms</span>
            <span style="color: #888; font-size:small; font-family: arial;">({self.cumulative / total:.2%})</span>
        """

        if self.dependencies:
            output = f"""<details style="padding-left: 1em"><summary>{data}</summary>"""

            for dep in sorted(self.dependencies, key=lambda dep: dep.cumulative, reverse=True):
                output += dep.html(total)

            output += "</details>\n"

        else:
            output = f"""<div style="padding-left: 2em">{data}</div>"""

        return output


def parse_import_time_report(filename: Path) -> Import:
    root = Import("root", 0, 0, -1)

    stack = deque([root])
    for line in filename.read_text().splitlines()[::-1][:-1]:
        import_ = Import.parse(line)
        while stack[-1].level >= import_.level:
            stack.pop()

        stack[-1].dependencies.append(import_)
        stack.append(import_)

    root.cumulative = sum(dep.cumulative for dep in root.dependencies)

    return root


def main() -> None:
    argp = ArgumentParser()
    argp.add_argument("filename", type=Path)
    args = argp.parse_args()

    root = parse_import_time_report(args.filename)
    for i in sorted(root.dependencies, key=lambda dep: dep.cumulative, reverse=True):
        print(i.html(root.cumulative))


if __name__ == "__main__":
    main()
