# Copyright 2026 Darwin-Agent
# SPDX-License-Identifier: MIT
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .....workspace.workspace import Workspace


class TemplateSystemPromptBuilder:
    """Jinja2-template-based system prompt.

    Args:
        template_path: Path to a .j2 or .md template file.
        extra_context: Static variables merged into the render context.
    """

    def __init__(self, template_path: str, extra_context: dict | None = None):
        # Resolve a file:// URI (any spelling the evolver emits) to a plain path
        # so open() works. Routes through the shared resolver so template and
        # processor targets agree byte-for-byte; the old
        # ``template_path[len("file://"):]`` left an RFC-style third slash in
        # front of a Windows drive and the resulting OSError was swallowed into
        # an EMPTY system prompt (890 rollouts in s1k8b103 ran promptless). A
        # bare path passes through untouched. Imported lazily to keep this deep
        # processor module free of an import-time dependency on core.
        if isinstance(template_path, str):
            from .....core.file_uri import normalize_file_uri

            template_path = normalize_file_uri(template_path)
        self.template_path = template_path
        self._extra_context: dict = extra_context or {}
        self._template = None

    def _load_template(self) -> object:
        if self._template is None:
            try:
                from jinja2 import Template

                with open(self.template_path, "r", encoding="utf-8") as f:
                    self._template = Template(f.read())
            except ImportError:
                raise ImportError("jinja2 is required: pip install jinja2")
        return self._template

    async def build(self, workspace: "Workspace | None" = None) -> str:
        template = self._load_template()
        return template.render(**self._extra_context, workspace=workspace)
