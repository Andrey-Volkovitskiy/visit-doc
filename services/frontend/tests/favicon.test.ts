import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * The mark exists twice: inline in `Wordmark` (src/App.tsx), where it is themed with
 * utility classes, and as a standalone file (public/favicon.svg), where it is fetched
 * before any stylesheet by a browser that will not run CSS against it.
 *
 * A favicon was declined once on exactly this ground — "a second copy of it to keep in
 * step" — so these tests are the reason it is no longer a fair objection. They read both
 * files and compare them, so the two cannot drift apart silently. Nothing here renders
 * anything: it is a check on two files, which is the only place this invariant lives.
 */

// Resolved from the project root rather than from `import.meta.url`: vitest transforms
// this module and hands it an `http:` url, which `readFileSync` refuses.
const read = (path: string): string =>
  readFileSync(resolve(process.cwd(), path), "utf8");

const favicon = read("public/favicon.svg");
const app = read("src/App.tsx");
const html = read("index.html");

/** Every `d="..."` in a blob of markup, in order. */
function paths(source: string): string[] {
  return [...source.matchAll(/\sd="([^"]+)"/g)].map((m) => m[1]!);
}

/** The `Wordmark` component's own markup, and nothing else in App.tsx. */
function wordmark(): string {
  const start = app.indexOf("function Wordmark()");
  expect(start).toBeGreaterThan(-1);
  const end = app.indexOf("\nfunction App()", start);
  expect(end).toBeGreaterThan(start);
  return app.slice(start, end);
}

describe("the favicon is a file a browser can actually use", () => {
  // These exist because the first version of this file passed every test below and was
  // still invisible in every browser. Its comment mentioned the theme tokens by their
  // CSS names, which put a double hyphen inside an XML comment; XML forbids that, so the
  // document was not well-formed and Chrome refused to decode it. Six tests that matched
  // text with regexes all passed against a file that drew nothing.
  //
  // The lesson is the one this feature has been applying throughout: asserting that a
  // file *contains* the right strings is not asserting that it *works*. Nothing below
  // reads the markup as text.

  it("is well-formed XML", () => {
    const doc = new DOMParser().parseFromString(favicon, "image/svg+xml");
    const error = doc.querySelector("parsererror");
    expect(error?.textContent ?? null).toBeNull();
  });

  it("parses into an <svg> root carrying the SVG namespace", () => {
    const doc = new DOMParser().parseFromString(favicon, "image/svg+xml");
    expect(doc.documentElement.tagName.toLowerCase()).toBe("svg");
    expect(doc.documentElement.namespaceURI).toBe("http://www.w3.org/2000/svg");
  });

  it("declares its own size and viewBox, so a browser can scale it to a tab", () => {
    const doc = new DOMParser().parseFromString(favicon, "image/svg+xml");
    const svg = doc.documentElement;
    expect(svg.getAttribute("viewBox")).toBeTruthy();
    expect(svg.getAttribute("width")).toBeTruthy();
    expect(svg.getAttribute("height")).toBeTruthy();
  });

  it("draws something — a filled shape, not an empty document", () => {
    const doc = new DOMParser().parseFromString(favicon, "image/svg+xml");
    const drawn = doc.querySelectorAll("rect, path, circle, polygon");
    expect(drawn.length).toBeGreaterThan(0);
  });
});

describe("the favicon is the same mark as the header's", () => {
  it("draws the same paths, in the same order", () => {
    const inline = paths(wordmark());
    expect(inline.length).toBeGreaterThan(0);
    expect(paths(favicon)).toEqual(inline);
  });

  it("draws them on the same canvas", () => {
    const viewBox = /viewBox="([^"]+)"/;
    expect(favicon.match(viewBox)?.[1]).toBe(wordmark().match(viewBox)?.[1]);
  });

  it("draws the same rounded square behind them", () => {
    const rect = /<rect ([^>]*?)\/>/;
    const attrs = (s: string) =>
      Object.fromEntries(
        [...(s.match(rect)?.[1] ?? "").matchAll(/([a-zA-Z-]+)="([^"]+)"/g)].map(
          (m) => [m[1]!, m[2]!],
        ),
      );
    const a = attrs(favicon);
    const b = attrs(wordmark());
    for (const key of ["x", "y", "width", "height", "rx"]) {
      expect(a[key]).toBe(b[key]);
    }
  });

  it("uses the theme's own colours, not colours of its own", () => {
    // The header reaches them through `fill-accent` / `fill-surface`; a favicon cannot,
    // so it writes the values out. These are the two tokens' values in `app.css`, and
    // this is what stops the icon quietly becoming a colour the product does not use.
    const css = read("src/styles/app.css");
    const token = (name: string) =>
      css.match(new RegExp(`--color-${name}:\\s*([^;]+);`))?.[1]?.trim();

    const accent = token("accent");
    const surface = token("surface");
    expect(accent).toBeTruthy();
    expect(surface).toBeTruthy();

    const used = [...favicon.matchAll(/(?:fill|stroke)="(#[0-9a-fA-F]{3,8})"/g)].map(
      (m) => m[1]!.toLowerCase(),
    );
    expect(used.length).toBeGreaterThan(0);
    for (const colour of used) {
      expect([accent, surface]).toContain(colour);
    }
  });

  it("is declared in the document, so no browser guesses /favicon.ico", () => {
    // The whole point. With no `rel="icon"` a browser asks for /favicon.ico by default,
    // which this app does not ship — that request was the 404 on every first load.
    expect(html).toMatch(/<link\s+rel="icon"[^>]*href="\/favicon\.svg"/);
    expect(html).toMatch(/type="image\/svg\+xml"/);
  });

  it("still brings in no third-party URL (FR-004)", () => {
    expect(html).not.toMatch(/https?:\/\//);
    expect(favicon).not.toMatch(/https?:\/\/(?!www\.w3\.org)/);
  });
});
