"""SPDX-License-Identifier: MIT
Copyright (c) 2026 Rug Munch Media LLC

The three named editions — prompt pack, writer, editor pass, image contract.

Layout (import the submodule you need; this package imports nothing eagerly, so
a caller that only wants the prompt pack does not pull the chat client):

    editions.py     the one table: title, send time, brief, skeleton, caps
    prompt_pack.py  prompts/*.md → rendered string
    messages.py     edition spec + style config → writer and editor messages
    notes.py        evidence rows → numbered notes, sufficiency floor
    draft.py        JSON parse + the citation/number guard
    llm.py          the one chat call (aliases ``writer`` / ``editor`` on :8009)
    writer.py       write_edition(): notes → edition document
    editor.py       editor_pass(): second call, strips unsupported claims
    image.py        choose_image(): one candidate or no_image
    compose.py      compose_with_editor(): sibling of the engine's fact-check path
"""
