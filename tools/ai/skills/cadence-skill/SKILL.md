---
name: cadence-skill
description: "Look up and apply Cadence SKILL and Virtuoso APIs using the AI Assistant's bundled offline reference. Use when writing, reviewing, debugging, or explaining classic SKILL or SKILL++ involving core language functions, database access, layout, schematics, UI/forms, Library Manager, GDM, PCells, ROD, callbacks, or IPC; verify unfamiliar signatures and execution contexts before proposing code."
---

# Cadence SKILL Reference

Use the local, read-only `search_skill_api` and `get_skill_api` MCP tools before
relying on remembered Cadence API syntax. These tools query the reference
bundled with the AI Assistant and never execute SKILL.

## Lookup Workflow

1. Classify the task as core SKILL, database/design framework, layout,
   schematic, UI/forms, Library Manager (`lmgr*`), GDM (`gdm*`), PCell/ROD,
   callback, or IPC work.
2. Call `search_skill_api` with a function name, prefix, signature fragment, or
   short topic. Use a narrow query first and increase `limit` only when needed.
3. Call `get_skill_api` with the exact returned `name` or `reference_id`.
4. Check the returned usage, documentation, document set, and product version.
   If several matches are returned, preserve their context instead of merging
   them into one assumed signature.
5. Write the smallest code that follows the verified signature. Define
   functions only as `procedure(name(args) body ...)`, with no space before
   either opening parenthesis. Execute inline snippets as
   `lambda((args ...) body ...)` (same no-space rule),
   `prog((vars ...) body ...)` or `let((bindings ...) body ...)`. Do not use
   `defun`, `nlambda`, `nprocedure`, `mprocedure`, macros, generic/method
   definitions, local function-definition forms, or function shorthand
   `define((name args) ...)`. Ordinary SKILL++ variable bindings such as
   `define(value 1)` are allowed. State any behavior the offline reference does
   not establish.
6. Check generated source with `check_skill`, supplying exactly one of `code`
   or `path`. Each `(` must close with `)`; `]` is only for indexing and must
   never close pending parentheses. Use `;` for line comments or non-nested
   `/* ... */` for block comments. The check does not execute SKILL.
7. Fix reported source locations and recheck the complete source. Do not
   blindly append or remove parentheses. After two unsuccessful repair
   attempts, report the unresolved diagnostics. A passing preflight covers
   lexical and definition-policy checks, not full syntax or logic correctness.

For example, search for `substring`, then retrieve its exact entry before
using its optional length argument. Search by a topic such as `application
form` when the function name is unknown, then retrieve the relevant `hi*`
entries exactly.

## Object Attribute Access

Prefer the access operators when exploring or reading Cadence object
attributes instead of defaulting to `dbGet`/`get` or hard-coded field lists:

- `obj~>prop` reads one attribute or property; `obj~>prop=value` writes it.
- `obj~>?` lists the available attribute names for a database object.
- `obj~>??` returns the attribute names and values as a disembodied property
  list (DPL).
- `->` accesses DPLs, defstructs, association tables, and user types;
  `obj->?` and `obj->??` enumerate their slots or keys.
- `~>` generalizes `->` and can also map across a list of objects, for example
  `cv~>instances~>name`.

Use these operators when they fit the object type, and confirm writability and
the returned structure with the reference or a small probe. The `?` and `??`
forms can return large DPLs, so filter or print only the needed fields. See
`skdfref/topic/Retrieving_the_List_of_Attribute_Names__Attributes_or_Values_for_a_dbObject`
and `sklanguser/topic/Access_Operators_in_SKILL`.

## Context And Safety

- Treat documentation lookup as evidence about APIs, not proof of live runtime
  state, installed product support, or callback availability.
- Keep CIW, application, Library Manager, and GDM callback contexts separate.
  Do not assume an `lmgr*` or `gdm*` function is callable from ordinary CIW
  code merely because it exists in the reference.
- Use the `virtuoso-assistant` workflow for live context inspection and SKILL
  execution. The launcher auto-approves documented MCP calls, but `eval_skill`
  and `load_skill_file` still execute in the live process and must not be
  invoked merely to perform documentation lookup.
- If no exact entry exists, search alternate spellings or a narrower prefix.
  Do not invent a signature. Ask for a small, explicitly approved CIW probe
  when runtime behavior is still uncertain.
- Respect `truncated: true` in a returned document. Base claims only on visible
  text and identify omitted details rather than guessing them.
