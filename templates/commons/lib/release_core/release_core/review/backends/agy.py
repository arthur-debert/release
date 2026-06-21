"""agy — the Antigravity (agy) CLI review backend.

Invokes ``agy --model=<m> --print "Please read the file <tempfile> and follow
its instructions exactly. Output only the requested JSON."`` The full review
prompt is written to a temp file and agy is pointed at it; agy has no native
schema enforcement, so the prompt is built upstream with ``schema_inline=True``
(the expected JSON shape is described in-prose inside it).

The phos script shelled out with ``shell=True`` and a single command string;
here the invocation is a plain argv list (the shared ``proc`` helper never uses
a shell), so no quoting is needed.
"""

from __future__ import annotations

import os
import tempfile

from ... import proc
from ..schema import extract_json
from .base import Backend


def _print_instruction(prompt_path: str) -> str:
    return (
        f"Please read the file {prompt_path} and follow its instructions exactly. "
        f"Output only the requested JSON."
    )


class AgyBackend(Backend):
    name = "agy"
    binary = "agy"

    def __init__(self, model: str = "pro") -> None:
        self.model = model

    def _argv(self, prompt_path: str) -> list[str]:
        return [
            "agy",
            f"--model={self.model}",
            "--print",
            _print_instruction(prompt_path),
        ]

    def build_command(self, prompt: str, schema: dict) -> dict:
        placeholder = "<prompt-tempfile>.md"
        return {
            "argv": self._argv(placeholder),
            "stdin": None,
            "files": {placeholder: prompt},
        }

    def run(self, prompt: str, schema: dict, *, cwd: str | None = None) -> dict:
        prompt_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".md",
                prefix=".review_prompt_",
                delete=False,
            ) as prompt_file:
                prompt_file.write(prompt)
                prompt_path = prompt_file.name

            result = proc.run(self._argv(prompt_path), cwd=cwd)
            return extract_json(result.stdout)
        finally:
            if prompt_path and os.path.exists(prompt_path):
                os.remove(prompt_path)
