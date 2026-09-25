/**
 * The one piece of Markdown a thread renders: emphasis.
 *
 * `*text*` / `_text_` is italic, `**text**` / `__text__` bold, `***text***` /
 * `___text___` both. Nothing else — no links, lists, headings or code — and nothing is
 * ever turned into HTML: the result is a tree of plain strings the caller renders as
 * elements, so a message cannot inject markup however it is written.
 *
 * The rules are a small subset of CommonMark's, chosen so ordinary text is left alone:
 * - An opening run must be followed by a non-space and a closing run preceded by one,
 *   so `2 * 3 * 4` and a `* item` line stay literal.
 * - A run closes only on a run of the same character and the same length, so
 *   `**bold *italic* bold**` nests and a mismatched `***a**` stays literal.
 * - `_` does not open or close inside a word, so `snake_case_name` stays literal; `*`
 *   does, as in CommonMark (`un*frigging*believable`).
 * - Emphasis never spans a line break, so a stray delimiter cannot restyle the rest of
 *   a message.
 * - A run longer than three, or one with no partner, is literal text.
 */

export type EmphasisStyle = "italic" | "bold" | "boldItalic";

export interface Emphasis {
  style: EmphasisStyle;
  children: EmphasisNode[];
}

export type EmphasisNode = string | Emphasis;

const STYLE_BY_LENGTH: Record<number, EmphasisStyle> = {
  1: "italic",
  2: "bold",
  3: "boldItalic",
};

const SPACE = /\s/u;
const WORD = /[\p{L}\p{N}]/u;

function isDelimiter(char: string | undefined): char is "*" | "_" {
  return char === "*" || char === "_";
}

/** The length of the run of `text[at]` starting at `at`. */
function runLength(text: string, at: number): number {
  let end = at;
  while (text[end] === text[at]) end++;
  return end - at;
}

function canOpen(text: string, at: number, length: number): boolean {
  const before = text[at - 1];
  const after = text[at + length];
  if (after === undefined || SPACE.test(after)) return false;
  return text[at] !== "_" || before === undefined || !WORD.test(before);
}

function canClose(text: string, at: number, length: number): boolean {
  const before = text[at - 1];
  const after = text[at + length];
  if (before === undefined || SPACE.test(before)) return false;
  return text[at] !== "_" || after === undefined || !WORD.test(after);
}

/** Where the run opened at `open` is closed, or null when nothing on its line closes it. */
function findClose(text: string, open: number, length: number): number | null {
  let at = open + length;
  while (at < text.length && text[at] !== "\n") {
    if (!isDelimiter(text[at])) {
      at++;
      continue;
    }
    const run = runLength(text, at);
    if (text[at] === text[open] && run === length && canClose(text, at, run)) {
      return at;
    }
    at += run;
  }
  return null;
}

/** Split `text` into plain strings and emphasised spans, adjacent strings merged. */
export function parseEmphasis(text: string): EmphasisNode[] {
  const nodes: EmphasisNode[] = [];
  const pushText = (value: string): void => {
    if (value === "") return;
    const last = nodes[nodes.length - 1];
    if (typeof last === "string") nodes[nodes.length - 1] = last + value;
    else nodes.push(value);
  };

  let at = 0;
  while (at < text.length) {
    if (!isDelimiter(text[at])) {
      let end = at;
      while (end < text.length && !isDelimiter(text[end])) end++;
      pushText(text.slice(at, end));
      at = end;
      continue;
    }
    const length = runLength(text, at);
    const style = STYLE_BY_LENGTH[length];
    const close =
      style !== undefined && canOpen(text, at, length)
        ? findClose(text, at, length)
        : null;
    if (style === undefined || close === null) {
      pushText(text.slice(at, at + length));
      at += length;
      continue;
    }
    nodes.push({ style, children: parseEmphasis(text.slice(at + length, close)) });
    at = close + length;
  }
  return nodes;
}
