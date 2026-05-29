---
name: lex-primer
description: |
  Primer for writing correct Lex documents. Use when:
  (1) Writing or generating .lex content
  (2) Understanding Lex syntax (it is NOT Markdown)
  (3) Reviewing whether a .lex file is syntactically correct
  (4) Converting content to Lex format
---

# Lex Document Primer

Lex is NOT Markdown. It uses indentation and conventions from publishing — no `#` headers, no `---` separators, no `*bold*`. If you write Markdown syntax in a .lex file, it will be treated as plain text.

## Core Principle

Structure = indentation (4 spaces per level). The only explicit syntax marker is `::` (for annotations, verbatim closing, and table closing). Everything else is determined by position, indentation, and punctuation patterns.

## Document Title

The first non-annotation line(s) at the top of a document, followed by a blank line, form the document title. Not every document has one.

```text
My Document Title

    Content starts here (indented = inside a session).
```

Title with subtitle — the title line ends with `:` and a second non-blank, non-indented line follows:

```text
Sapiens:
A Brief History of Humankind

    Content here.
```

Rules:

- Must be the very first element (before any non-annotation content)
- Must be followed by a blank line
- Must not be indented
- A trailing `:` + second line = title with subtitle (colon is structural, stripped)
- A trailing `:` + blank line directly = plain title (colon is content)
- No trailing `:` = plain title, no subtitle

## The Seven Elements

### 1. Paragraph (fallback)

Consecutive non-blank lines at the same indentation level. If nothing else matches, it's a paragraph.

```text
This is a paragraph.
It can span multiple lines.
```

Paragraphs yield to other elements: they stop before 2+ consecutive list-item lines or a subject line followed by an indent.

### 2. Session (heading + content)

A title line, then a **blank line**, then **indented** content.

```text
1. Introduction

    This paragraph is inside the "Introduction" session.

    1.1. Background

        Nested session with its own content.
```

- Title can have an ordered marker (`1.`, `a.`, `I.`), extended marker (`1.1.`, `IV.2.1`), parenthetical marker (`(1)`, `(a)`), or be plain text
- The blank line after the title is MANDATORY (distinguishes from definition)
- Content MUST be indented relative to the title
- Sessions can contain all element types including nested sessions

### 3. Definition (term + immediate content)

A subject line ending with `:`, then **immediately** indented content (NO blank line).

```text
HTTP Methods:
    GET retrieves resources.
    POST creates new resources.
```

- NO blank line between subject and content (that would make it a session)
- Subject line MUST end with `:`
- Content can include paragraphs, lists, nested definitions, verbatim blocks, tables, and annotations
- Cannot contain sessions

### 4. List (2+ items)

Two or more list-item lines.

```text
Some intro text.

- First item
- Second item
- Third item
```

- Markers: `-` (dash), `1.` or `1)` (numbered), `a.` or `a)` (alpha), `I.` (roman), `(1)` or `(a)` (parenthetical), `1.1.` (extended/multi-segment)
- MUST have at least 2 items (single item = paragraph)
- Blank lines before lists are optional (paragraph look-ahead detects list boundaries), but common
- NO blank lines between items (blank line terminates the list)
- Marker style of first item defines the list style

### 5. Verbatim Block (raw content)

A subject line ending with `:`, optional blank line, indented raw content, and a closing `:: label ::` data marker.

```text
Example code:

    fn main() {
        println!("Hello");
    }

:: rust ::
```

- Content is NOT parsed (preserves raw text exactly)
- Closing `:: label ::` data marker is mandatory, at the same indentation as the subject
- The closing marker can have parameters: `:: javascript caption="Hello World" ::`
- Marker form (no indented content): just subject + closing annotation
- The Indentation Wall: content must be indented past the subject's indentation level

Verbatim groups — multiple subject/content pairs sharing one closing marker:

```text
Install:
    npm install lex-fmt

Run:
    lex format file.lex

:: shell ::
```

### 6. Table (structured data)

Same outer structure as verbatim blocks, but with `table` as the closing label and pipe-delimited content that gets inline-parsed.

```text
Comparison:
    | Method     | Speed  | Accuracy |
    | Approach A | 120ms  | 94.2%    |
    | Approach B | 45ms   | 91.7%    |
:: table align=lcr ::
```

- Subject line (caption) ending with `:`, indented pipe rows, closing `:: table ::`
- Leading and trailing `|` required on each row
- Cell content supports all lex inlines (`*bold*`, `_italic_`, `` `code` ``, etc.)
- First row is header by default (`header=0` for none, `header=2` for two header rows)
- Alignment via `align=` parameter: `l` left, `c` center, `r` right (one char per column)
- Cell merging: `>>` for colspan (absorbed by left neighbor), `^^` for rowspan (absorbed by upper neighbor)
- Separator lines (`|---|---|`) are cosmetic and ignored (eases markdown migration)
- Multi-line mode: blank lines between pipe groups make consecutive pipe lines form one row
- Footnotes: numbered list after last pipe row, separated by a blank line, inside the table block

### 7. Annotation (metadata)

Structured metadata using `::` markers. Annotations attach to the previous element, the parent container, or are document-level if they appear first.

```text
:: note :: Important information
:: warning severity=high :: Check this carefully
:: aside ::
    Block content here.
    - Can include lists
```

Three forms:

- **Marker**: `:: label ::` (no content)
- **Single-line**: `:: label :: inline text`
- **Block**: `:: label ::` + newline + indented content (dedent closes the block — no bare `::` needed)

Labels are mandatory. Parameters are optional: `:: label key=value ::`

Content cannot include sessions.

## Inline Formatting

```text
*bold text*           — strong (NOT **double asterisk**)
_italic text_         — emphasis (NOT *single asterisk*)
`code`                — inline code (literal, no nested inlines)
#math expression#     — math notation (literal, no nested inlines)
[reference]           — reference/link (literal, type determined by content)
```

Start markers require a non-alphanumeric predecessor and non-whitespace successor. End markers require a non-whitespace predecessor and non-alphanumeric successor.

Reference types (determined by content pattern):

- `[https://example.com]` — URL
- `[@doe2024]` — citation (supports multiple keys: `[@a; @b]`, locators: `[@a, pp. 42-45]`)
- `[^note1]` — footnote (labeled)
- `[42]` — footnote (numbered)
- `[#2.1]` — session reference
- `[./path/to/file]` — file reference
- `[TK]` or `[TK-feature]` — placeholder (to come)
- `[!!!]` — not sure (no alphanumeric content)
- `[Section Title]` — general reference (fallback)

Escape with backslash: `\*not bold\*`, `\[not a link\]`

- Before non-alphanumeric: escapes the character (backslash removed)
- Before alphanumeric: backslash preserved (paths like `C:\Users` work naturally)
- Inside literal inlines (`` ` ``, `#`, `[]`): no escape processing, backslashes preserved

## Common Mistakes (Lex != Markdown)

| Wrong (Markdown) | Right (Lex) |
|---|---|
| `# Heading` | `1. Heading` + blank line + indented content |
| `**bold**` | `*bold*` |
| `*italic*` | `_italic_` |
| `[text](url)` | `[url]` (or footnote pattern) |
| ` ```code``` ` | Subject line + indented code + `:: lang ::` |
| `---` separator | blank line |
| `> blockquote` | indented paragraph in a definition/session |
| markdown table | subject + pipe rows + `:: table ::` |
| `## Sub-heading` | Use numbered markers: `1.1.` or plain text + blank + indent |

## Indentation Rules

- 1 level = 4 spaces (tabs converted to 4 spaces)
- Content must be indented deeper than its parent element
- Dedent returns to parent scope
- Partial indentation is tolerated (6 spaces = 1 indent + 2 literal spaces)

## Parse Precedence

The parser tries elements in this order:

1. Document title (only at document start, before any content)
2. Verbatim block / Table (both detected together — closing label determines type)
3. Annotation (block form, then single-line form)
4. List without preceding blank (paragraph yields via look-ahead)
5. List with preceding blank
6. Definition (subject + immediate indent, no blank line)
7. Session (title + blank line + indent)
8. Paragraph (fallback, yields before list/definition boundaries)
9. Blank line group
