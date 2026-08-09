"""Δ17 (degraded adoption) — deterministic declaration bootstrapping.

Static, LLM-free extraction of processor declarations from our OWN code,
producing :class:`ComponentDecl` records whose ``citations`` carry exact
``file:line`` provenance — the input Δ9's citation gate requires.

Degraded scope (per the mechanism assessment): only the DECLARATIVE facts
are extracted — ``_hook`` / ``_order`` / ``_singleton_group`` / ``_after``
class attributes, ``on_*`` / ``@on()`` handler coverage, and the four
explicitly declared data-channel class attributes (``_writes_slot_keys``
etc.).  Slot/event reads hidden inside method BODIES are deliberately NOT
inferred (static attribute-access inference is dirty and low-confidence);
they stay manual + observation-reconciled.

Values come from ``get_graph_metadata`` — the same single source of truth
the builder serializes — so a bootstrap run can never disagree with the
runtime about WHAT is declared; the AST pass only adds WHERE.  Re-runnable:
when the class changes, re-bootstrapping follows it (the static
WELL_KNOWN_DECLARATIONS snapshot would rot; the consistency test pins them
together).
"""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

from ..core.processor import MultiHookProcessor, get_graph_metadata
from .declaration import ComponentDecl, DeclarationSource, WELL_KNOWN_DECLARATIONS

#: class attributes whose assignments are citable declarations
_DECL_ATTRS = (
    "_hook", "_order", "_singleton_group", "_after",
    "_writes_slot_keys", "_reads_slot_keys",
    "_reads_event_fields", "_writes_event_fields",
)

#: decl-field name → class attribute it is declared by
_FIELD_TO_ATTR = {
    "hook": "_hook",
    "order": "_order",
    "singleton_group": "_singleton_group",
    "after": "_after",
    "writes_to": "_writes_slot_keys",
    "reads_from": "_reads_slot_keys",
    "reads_event_fields": "_reads_event_fields",
    "writes_event_fields": "_writes_event_fields",
}


def _rel(path: str) -> str:
    """Repo-relative posix path when possible (portable citations)."""
    p = Path(path).resolve()
    try:
        return p.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def _class_source_citations(cls: type) -> "tuple[dict[str, str], list[str]]":
    """AST-walk one class body: attr → file:line, plus handler def lines."""
    try:
        src_file = inspect.getsourcefile(cls)
        src_lines, class_line = inspect.getsourcelines(cls)
    except (OSError, TypeError):
        return {}, []
    if not src_file:
        return {}, []
    try:
        tree = ast.parse("".join(src_lines))
    except SyntaxError:
        return {}, []
    cls_defs = [n for n in tree.body
                if isinstance(n, (ast.ClassDef,))]
    if not cls_defs:
        return {}, []
    body = cls_defs[0].body
    rel = _rel(src_file)

    attr_cites: dict[str, str] = {}
    handler_cites: list[str] = []
    for node in body:
        line = class_line + node.lineno - 1
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name) and t.id in _DECL_ATTRS:
                    attr_cites.setdefault(t.id, f"{rel}:{line}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            is_handler = node.name.startswith("on_")
            for deco in node.decorator_list:
                fn = deco.func if isinstance(deco, ast.Call) else deco
                if isinstance(fn, ast.Name) and fn.id == "on":
                    is_handler = True
            if is_handler:
                handler_cites.append(f"{rel}:{line}")
    return attr_cites, handler_cites


def _mro_citations(cls: type) -> "tuple[dict[str, str], list[str]]":
    """Walk the MRO below MultiHookProcessor — nearest declaration wins."""
    attr_cites: dict[str, str] = {}
    handler_cites: list[str] = []
    for base in cls.__mro__:
        if base is MultiHookProcessor or base is object:
            break
        cites, handlers = _class_source_citations(base)
        for name, cite in cites.items():
            attr_cites.setdefault(name, cite)  # nearest-in-MRO wins
        handler_cites.extend(handlers)
    return attr_cites, handler_cites


def bootstrap_declaration(target: str) -> ComponentDecl:
    """Statically bootstrap one declaration with file:line citations.

    Values come from ``get_graph_metadata`` (single source of truth); the
    AST pass contributes only the citations.  Raises ``ImportError`` /
    ``AttributeError`` for unimportable targets — the bootstrapper runs on
    OUR code only, never on candidates.
    """
    mod_path, cls_name = target.rsplit(".", 1)
    cls = getattr(importlib.import_module(mod_path), cls_name)

    meta = get_graph_metadata(cls)
    attr_cites, handler_cites = _mro_citations(cls)

    citations: dict = {}
    for field_name, attr in _FIELD_TO_ATTR.items():
        if attr in attr_cites:
            citations[field_name] = attr_cites[attr]
    if handler_cites:
        citations["hooks"] = ",".join(handler_cites)

    return ComponentDecl(
        target=target,
        hook=meta["_hook_"],
        hooks=tuple(meta["_hooks_"]),
        order=meta["_order_"],
        singleton_group=meta["_singleton_group_"],
        after=tuple(meta["_after_"]),
        writes_to=tuple(meta["_writes_slots_"]),
        reads_from=tuple(meta["_reads_slots_"]),
        reads_event_fields=tuple(meta["_reads_event_fields_"]),
        writes_event_fields=tuple(meta["_writes_event_fields_"]),
        source=DeclarationSource.CODE_INTROSPECTION,
        confidence=1.0,
        citations=citations,
    )


def bootstrap_well_known(
    targets: "list[str] | None" = None,
) -> "dict[str, ComponentDecl]":
    """Regenerate the well-known table from source — the anti-rot path.

    ``WELL_KNOWN_DECLARATIONS`` stays a static snapshot so ``to_graph()``
    remains pure-read (L4.5: no imports); this function is the maintenance
    tool that recomputes it WITH citations.  The consistency test pins the
    static snapshot to this output, so a class change that is not mirrored
    into the snapshot fails loudly instead of rotting silently.
    """
    out: dict[str, ComponentDecl] = {}
    for target in (targets if targets is not None
                   else sorted(WELL_KNOWN_DECLARATIONS)):
        out[target] = bootstrap_declaration(target)
    return out
