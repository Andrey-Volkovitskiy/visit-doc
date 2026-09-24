import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { OutcomeDisclosure } from "../src/components/OutcomeDisclosure";
import type { AttentionMark, RequestOutcome } from "../src/lib/chatStream";

const CITED = [
  { entry_id: 7, chunk_index: 0, chunk_text: "Bring your referral letter." },
];

function answered(
  position: number,
  question: string,
  verdict: RequestOutcome["verdict"] = "answered",
): RequestOutcome {
  return {
    position,
    question,
    answer: "Yes — bring your referral letter.",
    verdict,
    citations: CITED,
  };
}

function abstained(
  position: number,
  question: string,
  verdict: RequestOutcome["verdict"] = "abstained_empty_pool",
): RequestOutcome {
  return { position, question, answer: null, verdict, citations: [] };
}

function renderDisclosure(
  outcomes: RequestOutcome[] | null,
  mark: AttentionMark | null = null,
) {
  return render(<OutcomeDisclosure requestOutcomes={outcomes} mark={mark} />);
}

function marker(): HTMLElement {
  return screen.getByTestId("outcome-marker");
}

describe("OutcomeDisclosure: opening and closing (FR-027, FR-028)", () => {
  it("starts closed, showing the marker and nothing else", () => {
    renderDisclosure([answered(0, "do I need a referral?")]);

    expect(marker()).toBeInTheDocument();
    expect(marker()).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("request-outcome")).toBeNull();
  });

  it("expands on activation and reports that it is expanded", () => {
    renderDisclosure([answered(0, "do I need a referral?")]);

    fireEvent.click(marker());

    expect(marker()).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("request-outcome")).toBeInTheDocument();
  });

  it("collapses on a second activation", () => {
    renderDisclosure([answered(0, "do I need a referral?")]);

    fireEvent.click(marker());
    fireEvent.click(marker());

    expect(marker()).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("request-outcome")).toBeNull();
  });

  it("opens on activation rather than on hover", () => {
    // FR-027 says activation, not hover: an evidence block that appears when the
    // pointer passes over a message is one a staff member cannot read without keeping
    // the pointer still, and one a keyboard cannot open at all.
    renderDisclosure([answered(0, "do I need a referral?")]);

    fireEvent.mouseOver(marker());
    fireEvent.mouseEnter(marker());

    expect(marker()).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("request-outcome")).toBeNull();
  });

  it("is a real button, so a keyboard reaches it", () => {
    renderDisclosure([answered(0, "do I need a referral?")]);

    expect(marker().tagName).toBe("BUTTON");
    expect(marker()).toHaveAccessibleName();
  });

  it("lets two disclosures be open at once, independently", () => {
    // FR-028. Each instance owns its own boolean, so this falls out rather than being
    // arranged — a `Set` held by the thread would be state in a component with no other
    // interest in it.
    render(
      <>
        <div data-testid="first">
          <OutcomeDisclosure
            requestOutcomes={[answered(0, "first question")]}
            mark={null}
          />
        </div>
        <div data-testid="second">
          <OutcomeDisclosure
            requestOutcomes={[answered(0, "second question")]}
            mark={null}
          />
        </div>
      </>,
    );

    const first = within(screen.getByTestId("first"));
    const second = within(screen.getByTestId("second"));

    fireEvent.click(first.getByTestId("outcome-marker"));
    expect(first.getByTestId("outcome-marker")).toHaveAttribute("aria-expanded", "true");
    expect(second.getByTestId("outcome-marker")).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(second.getByTestId("outcome-marker"));
    expect(first.getByTestId("outcome-marker")).toHaveAttribute("aria-expanded", "true");
    expect(second.getByTestId("outcome-marker")).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(first.getByTestId("outcome-marker"));
    expect(first.getByTestId("outcome-marker")).toHaveAttribute("aria-expanded", "false");
    expect(second.getByTestId("outcome-marker")).toHaveAttribute("aria-expanded", "true");
  });
});

describe("OutcomeDisclosure: when a marker exists at all (FR-026, FR-026a)", () => {
  it("marks an assistant reply holding answered outcomes as served", () => {
    renderDisclosure([answered(0, "do I need a referral?")]);

    expect(marker()).toHaveAttribute("data-outcome-state", "served");
  });

  it("marks a patient message holding an attention mark as needing a person", () => {
    // The two conditions fall on different messages, and that is not an inconsistency
    // to smooth over: outcomes describe what the assistant did, a mark is the thing a
    // person has to act on.
    renderDisclosure(null, "patient_asked_for_person");

    expect(marker()).toHaveAttribute("data-outcome-state", "needs-person");
  });

  it("marks an abstention as needing a person", () => {
    renderDisclosure([abstained(0, "what does it cost?")]);

    expect(marker()).toHaveAttribute("data-outcome-state", "needs-person");
  });

  it("marks a mixture of answered and unanswered as needing a person", () => {
    // Something in it is still owed to a person, so the weaker reading would be wrong.
    renderDisclosure([
      answered(0, "do I need a referral?"),
      abstained(1, "what does it cost?"),
    ]);

    expect(marker()).toHaveAttribute("data-outcome-state", "needs-person");
  });

  it("renders no marker for a message holding neither", () => {
    const { container } = renderDisclosure(null, null);

    expect(screen.queryByTestId("outcome-marker")).toBeNull();
    expect(container).toBeEmptyDOMElement();
  });

  it("renders a marker for a message holding both", () => {
    renderDisclosure([answered(0, "do I need a referral?")], "not_authorized");

    expect(marker()).toHaveAttribute("data-outcome-state", "needs-person");
  });

  it("states its state in words as well as in an attribute", () => {
    // FR-006: the attribute is for a test, the accessible name is for a reader. A
    // marker whose state lived only in its colour would satisfy neither.
    renderDisclosure([abstained(0, "what does it cost?")]);
    const needsPerson = marker().getAttribute("aria-label") ?? "";

    expect(needsPerson).toMatch(/\S/);

    screen.getByTestId("outcome-marker").remove();
    renderDisclosure([answered(0, "do I need a referral?")]);
    expect(marker().getAttribute("aria-label")).not.toBe(needsPerson);
  });
});

describe("OutcomeDisclosure: what an expanded block says (FR-029, FR-030, FR-031)", () => {
  function expand(): void {
    fireEvent.click(marker());
  }

  it("lists each request in the order recorded", () => {
    renderDisclosure([
      answered(0, "do I need a referral?"),
      abstained(1, "what does it cost?"),
      answered(2, "what should I bring?"),
    ]);
    expand();

    const positions = screen
      .getAllByTestId("request-outcome")
      .map((el) => el.getAttribute("data-position"));
    expect(positions).toEqual(["0", "1", "2"]);
  });

  it("gives each request its question", () => {
    renderDisclosure([
      answered(0, "do I need a referral?"),
      abstained(1, "what does it cost?"),
    ]);
    expand();

    const questions = screen
      .getAllByTestId("outcome-question")
      .map((el) => el.textContent);
    expect(questions).toEqual(["do I need a referral?", "what does it cost?"]);
  });

  it("gives an answered request the documents it drew on", () => {
    renderDisclosure([answered(0, "do I need a referral?")]);
    expand();

    expect(screen.getByTestId("citations")).toHaveTextContent(
      "Bring your referral letter.",
    );
  });

  it("marks an unanswered request unanswered, and names its reason", () => {
    renderDisclosure([abstained(0, "what does it cost?")]);
    expand();

    const unanswered = screen.getByTestId("outcome-unanswered");
    expect(unanswered).toBeInTheDocument();
    expect(unanswered.textContent).toMatch(/\S/);
    expect(screen.queryByTestId("citations")).toBeNull();
  });

  it("distinguishes unanswered from answered by more than colour", () => {
    // FR-030, and SC-006 reached from the other direction. With every stylesheet thrown
    // away the two still have to read differently — so the difference is an element and
    // a word, not a class that happens to paint something.
    renderDisclosure([
      answered(0, "do I need a referral?"),
      abstained(1, "what does it cost?"),
    ]);
    expand();

    const [first, second] = screen.getAllByTestId("request-outcome");
    expect(first).toHaveAttribute("data-verdict", "answered");
    expect(second).toHaveAttribute("data-verdict", "abstained_empty_pool");
    expect(within(first!).queryByTestId("outcome-unanswered")).toBeNull();
    expect(within(second!).getByTestId("outcome-unanswered")).toBeInTheDocument();
  });

  it("names a differently-worded reason for each kind of abstention", () => {
    // Four verdicts mean four different things and call for four different fixes — an
    // empty corpus is somebody's cue to write an entry, a rerank floor is not. One
    // wording for all of them would put them back into the single uninformative value
    // spec 008 split them out of.
    const verdicts = [
      "abstained_empty_corpus",
      "abstained_empty_pool",
      "abstained_similarity_floor",
      "abstained_rerank_floor",
    ] as const;
    const wordings = new Set<string>();
    for (const verdict of verdicts) {
      const { unmount } = renderDisclosure([abstained(0, "what does it cost?", verdict)]);
      expand();
      wordings.add(screen.getByTestId("outcome-unanswered").textContent ?? "");
      unmount();
    }
    expect(wordings.size).toBe(verdicts.length);
  });

  it("still marks an answer produced without reranking", () => {
    renderDisclosure([answered(0, "do I need a referral?", "answered_unreranked")]);
    expand();

    expect(screen.getByTestId("verdict-mark")).toBeInTheDocument();
    expect(screen.getByTestId("citations")).toBeInTheDocument();
  });

  it("states the booking outcome is not yet recorded, in every expanded block", () => {
    renderDisclosure([answered(0, "do I need a referral?")]);
    expand();

    expect(screen.getByTestId("booking-outcome-stub")).toBeInTheDocument();
  });

  it("states it for an abstaining block too", () => {
    renderDisclosure([abstained(0, "what does it cost?")]);
    expand();

    expect(screen.getByTestId("booking-outcome-stub")).toBeInTheDocument();
  });

  it("states it for a block that holds only an attention mark", () => {
    renderDisclosure(null, "patient_asked_for_person");
    expand();

    expect(screen.getByTestId("booking-outcome-stub")).toBeInTheDocument();
  });

  it("presents the booking gap as deliberately absent, not as a failure", () => {
    // SC-008's first half. A gap the backend cannot serve yet must not read as an
    // error, an empty result or something that went wrong — those would send a staff
    // member looking for a fault that does not exist.
    renderDisclosure([answered(0, "do I need a referral?")]);
    expand();

    const stub = screen.getByTestId("booking-outcome-stub");
    expect(stub.textContent).toMatch(/not yet/i);
    expect(stub.textContent).not.toMatch(/error|failed|unavailable|problem|went wrong/i);
    // And it is not an empty list standing in for one.
    expect(stub.querySelector("ul")).toBeNull();
    expect(stub.querySelector("ol")).toBeNull();
  });

  it("leaves the mark's own words to the message, and renders none itself", () => {
    // The mark stays unexpanded on the message: it is the one thing here a staff member
    // has to see while scanning a thread, and a second copy behind the marker would be
    // two elements carrying one hook.
    renderDisclosure(null, "urgent_condition");
    expand();

    expect(screen.queryByTestId("attention-mark")).toBeNull();
  });

  it("renders no request list for a message carrying only a mark", () => {
    // Null outcomes mean no FAQ half ran, which is not the same as one that ran and
    // answered nothing. Rendering an empty request list would say the second.
    renderDisclosure(null, "urgent_condition");
    expand();

    expect(screen.queryByTestId("request-outcome")).toBeNull();
    expect(screen.queryByTestId("citations")).toBeNull();
    expect(screen.queryByTestId("outcome-unanswered")).toBeNull();
  });
});
