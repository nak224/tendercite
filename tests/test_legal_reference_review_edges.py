"""Self-generated legal paragraphs for PR review; no operator documents or API calls."""

# Imported pytest fixture is intentionally rebound as test parameters.
# ruff: noqa: F811

import pytest
from test_legal_reference_intelligence import (  # noqa: F401
    ALIAS,
    DIRECTIVE,
    REG,
    legal,
    requirement,
)

from evaluation.legal_reference_intelligence import derive_legal_references


def long_title(*, leading="The synthetic annex explains the terminology. ", middle="", tail=""):
    return (
        f"{leading}{REG} of the Synthetic Assembly on the handling of example records"
        f"{middle} (hereinafter referred to as 'the {ALIAS}'). {tail}"
    )


def alias_records(quote):
    return [
        [requirement(quote, relationship="REFERENCE")],
        [
            requirement(
                f"Under {ALIAS}, the supplier must retain a sample log.",
                subject=ALIAS,
                identifier=ALIAS,
            )
        ],
    ]


@pytest.mark.parametrize("leading", ["An explanatory paragraph cites ", "A preceding sentence. "])
def test_long_title_with_explicit_naming_inside_paragraph_resolves_alias(legal, leading):
    core, corpus = legal(
        alias_records(long_title(leading=leading, tail="Additional prose follows."))
    )
    result = derive_legal_references(core, corpus)
    assert result["summary"]["distinct_normalized_instruments"] == 1
    assert result["summary"]["ambiguous_unresolved_references"] == 0
    proof = result["references"][0]["aliases"][0]["proof"]
    assert proof["origins"] and "hereinafter" in proof["citation"]["quote"]


def test_long_title_repealed_directive_never_owns_main_instrument_alias(legal):
    quote = long_title(middle=f" and repealing {DIRECTIVE}")
    records = alias_records(quote)
    records[0].append(
        requirement(quote, subject=DIRECTIVE, identifier=DIRECTIVE, relationship="REFERENCE")
    )
    core, corpus = legal(records)
    result = derive_legal_references(core, corpus)
    refs = {r["instrument_key"]: r for r in result["references"]}
    assert refs["regulation:EU:2016/679"]["aliases"]
    assert refs["directive:EC:95/46"]["aliases"] == []


@pytest.mark.parametrize(
    "change",
    ["two_instruments", "new_sentence", "new_clause", "no_naming", "too_long", "operative_prose"],
)
def test_ambiguous_or_unbounded_title_naming_does_not_merge(legal, change):
    quote = long_title()
    if change == "two_instruments":
        quote = long_title(middle=" and Regulation (EU) 2099/111 on different sample records")
    if change == "new_sentence":
        quote = quote.replace(" (hereinafter", ". Unrelated terms follow (hereinafter")
    if change == "new_clause":
        quote = quote.replace(" (hereinafter", "; unrelated terms follow (hereinafter")
    if change == "no_naming":
        quote = quote.replace("hereinafter referred to as ", "similar in topic to ")
    if change == "too_long":
        quote = quote.replace(" (hereinafter", " sample" * 300 + " (hereinafter")
    if change == "operative_prose":
        quote = quote.replace(" (hereinafter", ", a bidder must write a glossary (hereinafter")
    core, corpus = legal(alias_records(quote))
    result = derive_legal_references(core, corpus)
    assert result["summary"]["evidence_backed_alias_merges"] == 0
    assert result["summary"]["ambiguous_unresolved_references"] == 1


def test_ascribed_meaning_definition_requires_quote_evidence(legal):
    records = alias_records(long_title())
    records[1] = [
        requirement(
            "In this synthetic annex, the labels have the meaning ascribed to them in "
            f"Article 4 of the {ALIAS}.",
            subject=ALIAS,
            identifier=f"Article 4 of {ALIAS}",
            relationship="DEFINITION",
        )
    ]
    core, corpus = legal(records)
    layer = derive_legal_references(core, corpus)
    finding = layer["associated_findings"][1]
    assert finding["mention_role"] == "DEFINITION"
    assert finding["provisions"][0]["association_status"] == "SOURCE_SUPPORTED"
    assert finding["semantic_status"] == "SEMANTIC_REVIEW_PENDING"


@pytest.mark.parametrize(
    "quote",
    [
        "The meaning of this requirement is disputed.",
        "No definition is provided in this synthetic annex.",
        "These labels do not have the meaning ascribed to them in Article 4.",
        "The labels are not defined in this synthetic annex.",
    ],
)
def test_definition_label_without_affirmative_source_construction_is_not_enough(legal, quote):
    core, corpus = legal([[requirement(quote, relationship="DEFINITION")]])
    assert (
        derive_legal_references(core, corpus)["associated_findings"][0]["mention_role"]
        == "CONTEXTUAL_REFERENCE"
    )
