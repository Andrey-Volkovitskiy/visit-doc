import { describe, expect, it } from "vitest";
import { parseEmphasis } from "../src/lib/emphasis";

describe("parseEmphasis: the three emphasis forms", () => {
  it.each([
    ["*text*", "italic"],
    ["_text_", "italic"],
    ["**text**", "bold"],
    ["__text__", "bold"],
    ["***text***", "boldItalic"],
    ["___text___", "boldItalic"],
  ] as const)("reads %s as %s", (source, style) => {
    expect(parseEmphasis(`a ${source} b`)).toEqual([
      "a ",
      { style, children: ["text"] },
      " b",
    ]);
  });

  it("nests one form inside another", () => {
    expect(parseEmphasis("**bold *both* bold**")).toEqual([
      {
        style: "bold",
        children: ["bold ", { style: "italic", children: ["both"] }, " bold"],
      },
    ]);
  });

  it("keeps several spans on one line apart", () => {
    expect(parseEmphasis("*a* and **b**")).toEqual([
      { style: "italic", children: ["a"] },
      " and ",
      { style: "bold", children: ["b"] },
    ]);
  });
});

describe("parseEmphasis: ordinary text stays literal", () => {
  it.each([
    ["plain text", "no delimiter at all"],
    ["2 * 3 * 4", "a delimiter with a space after it opens nothing"],
    ["* item one", "a list-like line"],
    ["snake_case_name", "an underscore inside a word"],
    ["**unclosed", "a run with no partner"],
    ["***a**", "a run closed by a different length"],
    ["*a_", "a run closed by a different character"],
    ["****a****", "a run longer than three"],
    ["** **", "a run followed by a space"],
    ["*one\ntwo*", "a span across a line break"],
  ])("leaves %j alone (%s)", (source) => {
    expect(parseEmphasis(source)).toEqual([source]);
  });

  it("styles a star inside a word, as CommonMark does", () => {
    expect(parseEmphasis("un*frigging*believable")).toEqual([
      "un",
      { style: "italic", children: ["frigging"] },
      "believable",
    ]);
  });

  it("returns nothing for an empty message", () => {
    expect(parseEmphasis("")).toEqual([]);
  });
});
