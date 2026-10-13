"""Conservative source-backed legal identities over immutable staged consolidation.

This deliberately small recognizer is not a legal ontology. Unsupported text/links remain
unresolved. Only original accepted citations and separately reviewed mappings authorize joins.
"""

import copy
import re
import unicodedata
from collections import defaultdict

from evaluation.legal_reference_schemas import (
    LEGAL_LAYER_VERSION,
    NORMALIZATION_VERSION,
    AliasMap,
    MeaningReviews,
)
from evaluation.staged_evidence_plan import digest

INSTRUMENT = re.compile(
    r"\b(?P<kind>Regulation|Directive|Verordnung|Richtlinie)\s*"
    r"(?:\(\s*(?P<prefix>EU|EC|EEC|EURATOM)\s*\)\s*)?(?:No\.?\s*)?"
    r"(?P<number>\d{2,4}\s*/\s*\d{1,4})(?:\s*/\s*(?P<suffix>EU|EC|EEC|EURATOM))?\b",
    re.IGNORECASE,
)
PROVISION = re.compile(
    r"\b(?P<kind>Articles?|Art\.|Artikel|Sections?)\s*"
    r"(?P<number>\d+[a-zA-Z]?(?:\s*\(\s*[A-Za-z0-9]+\s*\))*)"
    r"|(?P<section>§\s*\d+[a-zA-Z]?(?:\s*\(\s*[A-Za-z0-9]+\s*\))*)",
    re.IGNORECASE,
)
UNSPECIFIED = re.compile(
    r"\b(?:unspecified|applicable|national|Union|EU|Member[- ]State)"
    r"(?:\s+(?:or|and|Member[- ]State|Union|national))*\s+(?:laws?|legislation)\b"
    r"|^(?:laws?|legislation|Gesetze|Recht)$"
    r"|\b(?:anwendbare[nrs]?|geltende[nrs]?|nationale[nrs]?)\s+(?:Gesetze|Recht|Rechtsvorschriften)\b"
    r"|(?:law of (?:the )?(?:Union|member[- ]state(?: to which the processor is subject)?))"
    r"(?: (?:or|and) (?:the )?law of (?:the )?"
    r"(?:Union|member[- ]state(?: to which the processor is subject)?))*"
    r"|concerned law",
    re.I,
)
OBLIGATION = re.compile(r"\b(shall|must|required|muss|müssen|verpflichtet)\b", re.I)
EXCEPTION = re.compile(r"^(unless|except(?: where| when| if)?|es sei denn|außer)\b", re.I)
EXCEPTION_MARKER = re.compile(r"\b(?:unless|except (?:where|when|if)|es sei denn|außer)\b", re.I)
CONDITION = re.compile(r"^(if|provided that|wenn|sofern)\b", re.I)
DEFINITION = re.compile(
    r"\b(?:means|bedeutet|bezeichnet)\b|\bdefined\s+(?:as|in|by|under)\b"
    r"|\bdefinition\s+of\b"
    r"|\b(?:have|has)\s+the\s+meaning\s+(?:ascribed|assigned|given)\s+to\s+(?:them|it)\s+in\b",
    re.I,
)


def normalized(value):
    value = unicodedata.normalize("NFKC", value).translate(
        str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-"})
    )
    return " ".join(value.split())


def instrument_matches(value):
    text = normalized(value)
    result = []
    for match in INSTRUMENT.finditer(text):
        kind = match["kind"].casefold()
        kind = {"verordnung": "regulation", "richtlinie": "directive"}.get(kind, kind)
        prefix, suffix = (match[k].upper() if match[k] else None for k in ("prefix", "suffix"))
        if prefix and suffix and prefix != suffix:
            continue  # Conflicting jurisdiction markers are never repaired.
        key = ":".join(
            (kind, prefix or suffix or "unspecified", re.sub(r"\s", "", match["number"]))
        )
        result.append({"key": key, "written": match[0], "start": match.start(), "end": match.end()})
    return result


def provisions(value):
    text, result = normalized(value), []
    for match in PROVISION.finditer(text):
        kind = (
            "section"
            if match["section"] or (match["kind"] or "").casefold().startswith("section")
            else "article"
        )
        number = re.sub(r"\s", "", match["number"] or match["section"].lstrip("§"))
        result.append({"key": f"{kind}:{number}", "written": match[0]})
        # Preserve explicit coordinated article numbers, including unresolved "Articles ... FR".
        tail = text[match.end() :]
        while extra := re.match(r"^\s*(?:,|and|und)\s*(\d+[a-zA-Z]?(?:\([A-Za-z0-9]+\))*)", tail):
            result.append({"key": f"{kind}:{extra[1]}", "written": extra[1]})
            tail = tail[extra.end() :]
    return list({r["key"]: r for r in result}.values())


def citation_status(validation):
    """Display correction only; original validators/serialized step identities stay intact."""
    if validation.get("empty_batch") and not validation.get("citations"):
        return "NO_CITATIONS_APPLICABLE"
    return validation.get("citation_status", "NOT_ASSESSED")


def _evidence_valid(finding, corpus):
    if not finding["evidence"]:
        return False
    for item in finding["evidence"]:
        c = item["citation"]
        names = [n for n, doc in corpus.mapping.items() if doc == c["document_id"]]
        page = corpus.pages.get(c["document_id"], {}).get(c["page_number"])
        if (
            len(names) != 1
            or corpus.documents[names[0]]["sha256"] != c["pdf_sha256"]
            or page is None
            or not c["quote"].strip()
            or c["quote"] not in page
        ):
            return False
    return True


def _proof(finding, citation):
    return {
        "finding_id": finding["consolidated_id"],
        "origins": finding["origins"],
        "citation": citation,
    }


def _explicit_alias(quote, instrument, alias):
    """Only exact parentheses or explicit 'hereinafter/called' constructions, in either order."""
    text = normalized(quote)
    if _historical(text, instrument):
        return False  # A repealed instrument inside another title does not own its title alias.
    a, i = re.escape(normalized(alias)), re.escape(instrument["written"])
    patterns = (
        rf"{i}\s*\((?:the\s+)?['\"]?{a}['\"]?\)",
        rf"{a}\s*\(['\"]?{i}['\"]?\)",
        rf"{i}\s*[,;]?\s*(?:hereinafter(?: referred to as)?|also known as|called|im Folgenden)"
        rf"\s*['\"]?{a}(?:['\"]|[.;,]|$)",
    )
    if any(re.search(p, text, re.I) for p in patterns):
        return True
    # Scope explicit naming to one bounded title clause, not the whole paragraph or
    # simply the nearest identifier (which may be a directive repealed by that title).
    naming = re.compile(
        rf"\(\s*(?:hereinafter(?: referred to as)?|also known as|called|im Folgenden)"
        rf"\s+['\"]?(?:the\s+)?{a}['\"]?\s*\)",
        re.I,
    )
    for clause in naming.finditer(text):
        boundaries = list(re.finditer(r"[.!?;]\s+", text[: clause.start()]))
        start = boundaries[-1].end() if boundaries else 0
        scoped = [
            m
            for m in instrument_matches(text)
            if start <= m["start"] and m["end"] <= clause.start()
        ]
        active = [m for m in scoped if not _historical(text, m)]
        title = text[instrument["end"] : clause.start()]
        if (
            len(active) == 1
            and active[0]["start"] == instrument["start"]
            and 0 <= clause.start() - instrument["end"] <= 1600
            and re.match(r"\s+(?:of|on)\b", title, re.I)
            and re.search(r"\bon\b", title, re.I)
            and not OBLIGATION.search(title)
        ):
            return True
    # A parenthetical at the end of a single-instrument title can follow a repealed
    # directive in that title. Never attach it to the nearest (historical) identifier.
    active = [m for m in instrument_matches(text) if not _historical(text, m)]
    return bool(
        instrument["start"] == 0
        and len(active) == 1
        and re.search(r"\bon\b.+\brepealing\b", text, re.I)
        and re.search(rf"\((?:the\s+)?['\"]?{a}['\"]?\)[.]?$", text, re.I)
        and not OBLIGATION.search(text)
    )


def _historical(text, instrument):
    return bool(
        re.search(
            r"(?:repealing|repealed|Aufhebung(?: der)?)\s*$", text[: instrument["start"]], re.I
        )
        or re.match(
            r"\s*(?:(?:is|was|has been)\s+)?(?:repealed|aufgehoben)\b",
            text[instrument["end"] :],
            re.I,
        )
    )


def _bound_annotations(raw, schema, consolidation):
    if raw is None:
        return None
    data = schema.model_validate(raw)
    if (
        data.consolidation_sha256 != digest(consolidation)
        or data.provenance != consolidation["provenance"]
    ):
        raise ValueError("Legal annotations differ from original consolidation hash/provenance")
    return data


def _aliases(consolidation, corpus, raw):
    aliases, issues = defaultdict(list), []
    findings = consolidation["findings"]
    candidates = {
        normalized(value)
        for f in findings
        if f["kind"] == "LEGAL_REFERENCE"
        for value in (f["subject"], f["exact_identifier"])
        if not instrument_matches(value) and not provisions(value) and not _unspecified_label(value)
    }
    for finding in findings:
        if not _evidence_valid(finding, corpus):
            continue
        for item in finding["evidence"]:
            quote = item["citation"]["quote"]
            for instrument in instrument_matches(quote):
                for alias in sorted(candidates):
                    if _explicit_alias(quote, instrument, alias):
                        aliases[alias.casefold()].append(
                            {
                                "alias": alias,
                                "instrument_key": instrument["key"],
                                "basis": "EXPLICIT_SOURCE_EQUIVALENCE",
                                "proof": _proof(finding, item["citation"]),
                            }
                        )
    mapping = _bound_annotations(raw, AliasMap, consolidation)
    hashes = {o["response_file_sha256"] for f in findings for o in f["origins"]}
    if mapping:
        seen = set()
        for entry in mapping.aliases:
            instruments = instrument_matches(entry.instrument_identifier)
            if len(instruments) != 1 or provisions(entry.instrument_identifier):
                raise ValueError(
                    "Reviewed alias requires one explicit supported instrument identifier"
                )
            if not set(entry.original_response_sha256s) <= hashes:
                raise ValueError("Reviewed alias cites unknown original response artifact hashes")
            key = (normalized(entry.alias).casefold(), instruments[0]["key"])
            if key in seen:
                raise ValueError("Duplicate reviewed alias association")
            seen.add(key)
            aliases[key[0]].append(
                {
                    "alias": entry.alias,
                    "instrument_key": key[1],
                    "basis": "OPERATOR_REVIEWED_MAPPING",
                    "proof": entry.model_dump(mode="json"),
                }
            )
    for alias, rows in aliases.items():
        if len({r["instrument_key"] for r in rows}) > 1:
            issues.append(
                {"kind": "CONFLICTING_ALIAS_IDENTITIES", "alias": alias, "associations": rows}
            )
    return aliases, issues


def _identity(finding, aliases, valid):
    written = finding["exact_identifier"]
    matches = instrument_matches(written) + instrument_matches(finding["subject"])
    keys = {m["key"] for m in matches}
    cited = {
        m["key"] for e in finding["evidence"] for m in instrument_matches(e["citation"]["quote"])
    }
    associations = []
    for name in (finding["subject"], written):
        associations.extend(aliases.get(normalized(name).casefold(), []))
    if not keys:
        for alias, proofs in aliases.items():
            if re.search(r"\b" + re.escape(alias) + r"\b", normalized(written).casefold()):
                associations.extend(proofs)
    associations = list({digest(a): a for a in associations}.values())
    alias_keys = {a["instrument_key"] for a in associations}
    if not valid:
        return None, "UNRESOLVED_REFERENCE", [], "citation_invalid"
    if len(keys) > 1 or len(alias_keys) > 1 or (keys and alias_keys and keys != alias_keys):
        return None, "UNRESOLVED_REFERENCE", associations, "conflicting_instrument_identities"
    if keys and keys <= cited:
        return next(iter(keys)), "IDENTIFIED_INSTRUMENT", associations, None
    if not keys and len(alias_keys) == 1:
        # The original name must itself occur in a supplied quote; the reviewed map is not evidence.
        if any(
            re.search(
                r"\b" + re.escape(normalized(a["alias"]).casefold()) + r"\b",
                normalized(e["citation"]["quote"]).casefold(),
            )
            for a in associations
            for e in finding["evidence"]
        ):
            return next(iter(alias_keys)), "IDENTIFIED_INSTRUMENT", associations, None
    labels = [_unspecified_label(v) for v in (finding["subject"], written)]
    if (
        not keys
        and not associations
        and all(labels)
        and all(
            any(
                re.search(r"(?<!\w)" + re.escape(label) + r"(?!\w)", quote, re.I)
                for quote in (normalized(e["citation"]["quote"]) for e in finding["evidence"])
            )
            for label in labels
        )
    ):
        return None, "UNSPECIFIED_LEGAL_REFERENCE", [], "no_identifiable_instrument"
    return (
        None,
        "UNRESOLVED_REFERENCE",
        associations,
        "instrument_identity_not_explicitly_established",
    )


def _unspecified_label(value):
    # Match the entire claimed label, not generic words inside an unsupported named law.
    label = re.sub(r"^the\s+", "", normalized(value), flags=re.I)
    return label if UNSPECIFIED.fullmatch(label) else None


def _role(finding, instrument_key):
    roles = set()
    for e in finding["evidence"]:
        quote = normalized(e["citation"]["quote"])
        historical = False
        for m in instrument_matches(quote):
            if m["key"] == instrument_key and _historical(quote, m):
                roles.add("HISTORICAL_REPEALED_MENTION")
                historical = True
        if historical:
            continue
        exception_parts = _exception_parts(quote)
        if EXCEPTION.search(quote) or (exception_parts and _exception_focus(finding)):
            roles.add("EXCEPTION")
        elif CONDITION.search(quote) and finding["relationship"] in ("CONDITION", "PROCEDURE"):
            roles.add("PROCEDURAL_CONDITION")
        elif (
            finding["relationship"] == "DEFINITION"
            and DEFINITION.search(quote)
            and not re.search(
                r"\b(?:not|never)\s+(?:defined|have the meaning|has the meaning)"
                r"|\bno\s+definition",
                quote,
                re.I,
            )
        ):
            roles.add("DEFINITION")
        elif finding["relationship"] == "OBLIGATION" and (
            OBLIGATION.search(quote) or _parent_action(quote)
        ):
            roles.add("OPERATIVE_REQUIREMENT")
    # Historical/operative conflicts stay unresolved rather than choosing the convenient role.
    if "HISTORICAL_REPEALED_MENTION" in roles and "OPERATIVE_REQUIREMENT" in roles:
        return "UNRESOLVED_MENTION", "historical_and_operative_signals_conflict"
    if "HISTORICAL_REPEALED_MENTION" in roles:
        return "HISTORICAL_REPEALED_MENTION", None
    if len(roles) > 1:
        return "UNRESOLVED_MENTION", "conflicting_mention_role_signals"
    return next(iter(roles), "CONTEXTUAL_REFERENCE"), None


def _exception_parts(quote):
    """Bound explicit exceptions to a single sentence/clause, retaining normalized text.

    This is a deliberately limited recognizer, not a grammatical/semantic parser.
    Multiple connectors in one sentence and quoted/metalinguistic keywords stay unresolved.
    """
    text = normalized(quote)
    parts = []
    for sentence in re.split(r"(?<=[.!?;])\s+", text):
        markers = list(EXCEPTION_MARKER.finditer(sentence))
        if len(markers) != 1:
            continue
        marker = markers[0]
        main = sentence[: marker.start()].strip().rstrip(",").strip()
        subordinate = sentence[marker.start() :].rstrip(".!?;").strip()
        if (
            (marker.start() and sentence[marker.start() - 1] in "'\"")
            or sentence[marker.end() :].lstrip().startswith(("'", '"'))
            or not re.search(
                r"\b(?:prohibits?|forbids?|prevents?|requires?|permits?|allows?|"
                r"cannot|may not|is|are|untersagt|verbietet)\b",
                subordinate,
                re.I,
            )
        ):
            continue
        parts.append({"parent_clause": main, "exception_clause": subordinate})
    return parts


def _exception_focus(finding):
    # The model's relationship is only a focus hint; explicit cited clauses are required.
    return finding["relationship"] == "EXCLUSION" or bool(
        re.search(
            r"\b(?:unless|exception|restriction|prohibit\w*|forbid\w*|public interest|"
            r"Ausnahme|untersagt|verbietet)\b",
            normalized(finding["purpose"]),
            re.I,
        )
    )


ACTION_FAMILIES = (
    r"inform\w*|notif\w*",
    r"submit\w*",
    r"retain\w*|keep\w*",
    r"record\w*",
    r"provid\w*",
    r"transfer\w*",
    r"report\w*",
)


def _parent_action(text):
    """Recognize one explicit directive action; leave unfamiliar/compound parents unresolved."""
    if re.match(r"^(?:Inform|Notify)\b", text, re.I):
        return ACTION_FAMILIES[0]
    directives = list(re.finditer(r"\b(?:shall|must|(?:is|are) required to)\s+(\w+)\b", text, re.I))
    if len(directives) == 1:
        action = directives[0][1]
        return next((a for a in ACTION_FAMILIES if re.fullmatch(a, action, re.I)), None)
    return None


def _exception_link(child, parent, parent_finding):
    child_text, parent_text = normalized(child["quote"]), normalized(parent["quote"])
    if child_text not in parent_text:
        return None
    # Full paragraph equality is allowed only with an explicit, bounded subordinate
    # clause and an independently distinguishable parent focus in the original finding.
    matches = []
    for part in _exception_parts(parent_text):
        action = _parent_action(part["parent_clause"])
        if (
            action
            and part["exception_clause"] in child_text
            and re.search(r"\b(?:" + action + r")\b", parent_finding["purpose"], re.I)
        ):
            matches.append(part)
    return matches[0] if len(matches) == 1 else None


def _instrument_link(row, reference, kind, provision=None):
    finding = row["original_consolidated_finding"]
    for item in finding["evidence"]:
        quote = normalized(item["citation"]["quote"])
        matches = [m for m in instrument_matches(quote) if m["key"] == reference["instrument_key"]]
        for alias in reference["aliases"]:
            for match in re.finditer(
                r"(?<!\w)" + re.escape(normalized(alias["alias"])) + r"(?!\w)", quote, re.I
            ):
                matches.append({"start": match.start(), "end": match.end()})
        for m in matches:
            if kind == "obligation_under":
                supported = re.search(
                    r"(?:under|pursuant to|in accordance with|gemäß|nach)\s*$",
                    quote[: m["start"]],
                    re.I,
                ) and OBLIGATION.search(quote[m["end"] :])
            else:
                claimed = [provision] if provision is not None else row["provisions"]
                supported = any(
                    re.search(
                        re.escape(source_part["written"]) + r"\s+(?:of|of the|der|des)\s*$",
                        quote[: m["start"]],
                        re.I,
                    )
                    for source_part in provisions(quote)
                    for p in claimed
                    if source_part["key"] == p["key"]
                )
            if supported:
                return _proof(finding, item["citation"])
    return None


def _links(rows, references):
    links, unresolved = [], []
    for row in rows:
        reference = references.get(row["canonical_reference_id"])
        if row["citation_status"] != "VERIFIED_QUOTE" or not reference:
            continue
        if reference["specificity"] == "IDENTIFIED_INSTRUMENT":
            requested = [("provision_of", p) for p in row["provisions"]]
            if row["mention_role"] == "OPERATIVE_REQUIREMENT":
                requested.append(("obligation_under", None))
            for kind, provision in requested:
                proof = _instrument_link(row, reference, kind, provision)
                record = {
                    "kind": kind,
                    "from_finding_id": row["finding_id"],
                    "to_reference_id": reference["canonical_reference_id"],
                }
                if provision is not None:
                    record["from_provision_id"] = provision["canonical_provision_id"]
                if proof:
                    links.append({**record, "provenance": proof, "semantic_review": "PENDING"})
                else:
                    unresolved.append(
                        {**record, "reason": "no_explicit_instrument_relationship_in_cited_quote"}
                    )
            if row["alias_provenance"]:
                links.append(
                    {
                        "kind": "same_instrument_as",
                        "from_finding_id": row["finding_id"],
                        "to_reference_id": reference["canonical_reference_id"],
                        "provenance": row["alias_provenance"],
                        "semantic_review": "PENDING",
                    }
                )
        else:
            unresolved.extend(
                {
                    "kind": "provision_of",
                    "from_finding_id": row["finding_id"],
                    "from_provision_id": p["canonical_provision_id"],
                    "reason": "instrument_identity_unresolved",
                }
                for p in row["provisions"]
            )
        if row["mention_role"] not in ("EXCEPTION", "PROCEDURAL_CONDITION"):
            continue
        kind = "exception_to" if row["mention_role"] == "EXCEPTION" else "condition_of"
        supported = []
        for parent in rows:
            if (
                parent["finding_id"] == row["finding_id"]
                or parent["citation_status"] != "VERIFIED_QUOTE"
                or parent["mention_role"] not in ("OPERATIVE_REQUIREMENT", "PROCEDURAL_CONDITION")
            ):
                continue
            for child_e in row["source_citations"]:
                for parent_e in parent["source_citations"]:
                    child, whole = child_e["citation"], parent_e["citation"]
                    if (child["document_id"], child["page_number"], child["pdf_sha256"]) != (
                        whole["document_id"],
                        whole["page_number"],
                        whole["pdf_sha256"],
                    ):
                        continue
                    clause = None
                    if kind == "exception_to":
                        clause = _exception_link(
                            child, whole, parent["original_consolidated_finding"]
                        )
                        supported_clause = clause is not None
                    else:
                        supported_clause = (
                            normalized(child["quote"]) != normalized(whole["quote"])
                            and normalized(child["quote"]) in normalized(whole["quote"])
                            and CONDITION.search(normalized(child["quote"]))
                        )
                    if supported_clause:
                        provenance = {"child": child_e, "parent": parent_e}
                        if clause:
                            provenance["clause_support"] = {
                                **clause,
                                "text_basis": "WHITESPACE_NORMALIZED_QUOTATION",
                            }
                        supported.append(
                            {
                                "kind": kind,
                                "from_finding_id": row["finding_id"],
                                "to_finding_id": parent["finding_id"],
                                "provenance": provenance,
                                "semantic_review": "PENDING",
                            }
                        )
        if len({s["to_finding_id"] for s in supported}) == 1:
            links.append(supported[0])
        else:
            unresolved.append(
                {
                    "kind": kind,
                    "from_finding_id": row["finding_id"],
                    "reason": "ambiguous_parent"
                    if supported
                    else "no_explicit_parent_clause_in_cited_evidence",
                    "candidate_parent_ids": sorted({s["to_finding_id"] for s in supported}),
                }
            )
    return links, unresolved


def derive_legal_references(consolidation, corpus, *, alias_map=None, meaning_reviews=None):
    """Pure derived export: caller first replays immutable responses through recorded_step."""
    if (
        consolidation["schema_version"] != "staged-evidence-consolidation-1"
        or consolidation["provenance"] != corpus.provenance
    ):
        raise ValueError("Legal layer requires original staged consolidation/source provenance")
    aliases, issues = _aliases(consolidation, corpus, alias_map)
    references, rows = {}, []
    for original in consolidation["findings"]:
        finding = copy.deepcopy(original)
        valid = _evidence_valid(finding, corpus)
        key, specificity, associations, reason = _identity(finding, aliases, valid)
        if finding["kind"] != "LEGAL_REFERENCE":
            key, specificity, associations, reason = None, "NOT_LEGAL_REFERENCE", [], None
        ref_id = (
            "legal-"
            + digest(
                key
                or [
                    specificity,
                    normalized(finding["subject"]),
                    normalized(finding["exact_identifier"]),
                ]
            )[:24]
        )
        parts = provisions(finding["exact_identifier"])
        role, role_issue = (
            _role(finding, key) if valid else ("UNRESOLVED_MENTION", "citation_invalid")
        )
        unresolved = [r for r in (reason, role_issue, finding["uncertainty"]) if r]
        acronyms = (
            sorted(
                {
                    w
                    for w in re.findall(
                        r"\b[A-Z]{2,8}\b", finding["subject"] + " " + finding["exact_identifier"]
                    )
                    if w not in ("EU", "EC", "EEC", "EURATOM")
                }
            )
            if key is None
            else []
        )
        row = {
            "finding_id": finding["consolidated_id"],
            "canonical_reference_id": ref_id if finding["kind"] == "LEGAL_REFERENCE" else None,
            "reference_specificity": "SPECIFIC_PROVISION" if parts else specificity,
            "original_consolidated_finding": finding,
            "original_atomic_records": [
                copy.deepcopy(r)
                for r in consolidation["raw_records"]
                if any(
                    (r["batch_id"], r["finding"]["finding_id"], r["attempt_id"])
                    == (o["batch_id"], o["finding_id"], o["attempt_id"])
                    for o in finding["origins"]
                )
            ],
            "purpose": finding["purpose"],
            "relationship": finding["relationship"],
            "mention_role": role,
            "provisions": [
                {
                    **p,
                    "canonical_provision_id": "provision-" + digest([ref_id, p["key"]])[:24],
                    "specificity": "SPECIFIC_PROVISION",
                    "claimed_parent_reference_id": ref_id,
                    "parent_reference_id": None,
                }
                for p in parts
            ],
            "source_citations": finding["evidence"],
            "origins": finding["origins"],
            "alias_provenance": associations,
            "uncertainty": unresolved,
            "unresolved_abbreviations": acronyms,
            "citation_status": "VERIFIED_QUOTE" if valid else "CITATION_INVALID",
            "semantic_status": "SEMANTIC_REVIEW_PENDING",
            "human_review": None,
            "counted_as_operative_legal_requirement": valid
            and specificity == "IDENTIFIED_INSTRUMENT"
            and role == "OPERATIVE_REQUIREMENT",
        }
        rows.append(row)
        if finding["kind"] != "LEGAL_REFERENCE":
            continue
        ref = references.setdefault(
            ref_id,
            {
                "canonical_reference_id": ref_id,
                "instrument_key": key,
                "specificity": specificity,
                "instrument_name_as_written": finding["subject"],
                "explicit_instrument_identifier": key,
                "original_names_and_identifiers": [],
                "aliases": [],
                "provisions": [],
                "finding_ids": [],
                "unresolved_abbreviations": [],
            },
        )
        ref["original_names_and_identifiers"].append(
            {
                "name": finding["subject"],
                "identifier": finding["exact_identifier"],
                "finding_id": row["finding_id"],
                "origins": row["origins"],
            }
        )
        ref["finding_ids"].append(row["finding_id"])
        ref["aliases"] = list({digest(a): a for a in ref["aliases"] + associations}.values())
        ref["provisions"] = list(
            {p["canonical_provision_id"]: p for p in ref["provisions"] + row["provisions"]}.values()
        )
        ref["unresolved_abbreviations"] = sorted(set(ref["unresolved_abbreviations"] + acronyms))
    links, unresolved_links = _links(rows, references)
    reference_provisions = defaultdict(dict)
    for row in rows:
        row["supported_relationships"] = [
            link for link in links if link["from_finding_id"] == row["finding_id"]
        ]
        row["unresolved_relationships"] = [
            link for link in unresolved_links if link["from_finding_id"] == row["finding_id"]
        ]
        for provision in row["provisions"]:
            proof = [
                link
                for link in row["supported_relationships"]
                if link.get("from_provision_id") == provision["canonical_provision_id"]
            ]
            provision["association_status"] = "SOURCE_SUPPORTED" if proof else "UNRESOLVED"
            provision["relationship_provenance"] = proof
            provision["parent_reference_id"] = row["canonical_reference_id"] if proof else None
            if row["canonical_reference_id"] is not None:
                bucket = reference_provisions[row["canonical_reference_id"]]
                previous = bucket.get(provision["canonical_provision_id"])
                if previous is None or (proof and previous["association_status"] == "UNRESOLVED"):
                    bucket[provision["canonical_provision_id"]] = provision
    for ref_id, reference in references.items():
        reference["provisions"] = list(reference_provisions[ref_id].values())
    reviews = _bound_annotations(meaning_reviews, MeaningReviews, consolidation)
    by_id, seen = {r["finding_id"]: r for r in rows}, set()
    if reviews:
        for review in reviews.reviews:
            target = by_id.get(review.finding_id)
            if target is None or review.finding_id in seen:
                raise ValueError("Unknown or duplicate legal meaning review target")
            if set(review.original_response_sha256s) != {
                o["response_file_sha256"] for o in target["origins"]
            }:
                raise ValueError("Meaning review differs from original response artifact hashes")
            if review.status == "VERIFIED" and target["citation_status"] != "VERIFIED_QUOTE":
                raise ValueError("Invalid citations cannot receive verified legal meaning")
            seen.add(review.finding_id)
            target["human_review"] = review.model_dump(mode="json")
            target["semantic_status"] = {
                "VERIFIED": "HUMAN_VERIFIED_MEANING",
                "REJECTED": "HUMAN_REJECTED_MEANING",
                "UNCERTAIN": "HUMAN_UNCERTAIN_MEANING",
                "DRAFT": "SEMANTIC_REVIEW_PENDING",
            }[review.status]
            if review.status in ("REJECTED", "UNCERTAIN"):
                target["counted_as_operative_legal_requirement"] = False
    issues += copy.deepcopy(consolidation["issues"])
    named_identities = defaultdict(set)
    for row in rows:
        if row["canonical_reference_id"] is not None:
            name = normalized(row["original_consolidated_finding"]["subject"]).casefold()
            named_identities[name].add(row["canonical_reference_id"])
    issues += [
        {
            "kind": "SAME_WRITTEN_NAME_DIFFERENT_IDENTITIES",
            "name": name,
            "reference_ids": sorted(ids),
            "merged": False,
        }
        for name, ids in named_identities.items()
        if len(ids) > 1
    ]
    issues += [
        {"kind": "UNRESOLVED_FINDING", "finding_id": r["finding_id"], "reasons": r["uncertainty"]}
        for r in rows
        if r["uncertainty"]
    ]
    instruments = [r for r in references.values() if r["specificity"] == "IDENTIFIED_INSTRUMENT"]
    return {
        "schema_version": LEGAL_LAYER_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "plan_id": consolidation["plan_id"],
        "question_id": consolidation["question_id"],
        "original_consolidation_sha256": digest(consolidation),
        "provenance": copy.deepcopy(consolidation["provenance"]),
        "references": list(references.values()),
        "associated_findings": rows,
        "links": links,
        "unresolved_links": unresolved_links,
        "issues": issues,
        "quarantined_raw_records": [
            copy.deepcopy(r) for r in consolidation["raw_records"] if not r["accepted"]
        ],
        "source_coverage": {
            k: copy.deepcopy(consolidation[k])
            for k in (
                "status",
                "planned_batches",
                "completed_batches",
                "missing_batches",
                "failed_batches",
                "source_pages_planned",
                "source_pages_completed",
                "source_coverage_inventory",
            )
        },
        "citation_validation": {
            "original_valid_citations": consolidation["valid_citation_count"],
            "original_invalid_citations": consolidation["invalid_citation_count"],
            "missing_evidence_findings": consolidation["missing_evidence_findings"],
            "no_citations_applicable_batches": consolidation["no_relevant_findings_batches"],
            "interpretation": "Verbatim occurrence only; legal meaning requires independent review",
        },
        "summary": {
            "raw_atomic_findings": consolidation["raw_finding_count"],
            "accepted_raw_findings": consolidation["accepted_raw_finding_count"],
            "distinct_normalized_instruments": len(instruments),
            "provisions_per_instrument": {
                r["canonical_reference_id"]: sum(
                    p["association_status"] == "SOURCE_SUPPORTED" for p in r["provisions"]
                )
                for r in instruments
            },
            "claimed_provisions_per_instrument": {
                r["canonical_reference_id"]: len(r["provisions"]) for r in instruments
            },
            "unresolved_provision_associations": sum(
                p["association_status"] == "UNRESOLVED" for row in rows for p in row["provisions"]
            ),
            "requirements_per_instrument": {
                r["canonical_reference_id"]: sum(
                    f["counted_as_operative_legal_requirement"]
                    and f["canonical_reference_id"] == r["canonical_reference_id"]
                    for f in rows
                )
                for r in instruments
            },
            "unspecified_legal_references": sum(
                r["specificity"] == "UNSPECIFIED_LEGAL_REFERENCE" for r in references.values()
            ),
            "historical_repealed_mentions": sum(
                r["mention_role"] == "HISTORICAL_REPEALED_MENTION" for r in rows
            ),
            "evidence_backed_alias_merges": len(
                {
                    (ref["canonical_reference_id"], normalized(alias["alias"]).casefold())
                    for ref in instruments
                    for alias in ref["aliases"]
                }
            ),
            "ambiguous_unresolved_references": sum(
                r["specificity"] == "UNRESOLVED_REFERENCE" for r in references.values()
            ),
            "exceptions_linked_to_parent_findings": sum(
                link["kind"] == "exception_to" for link in links
            ),
            "findings_needing_human_review": sum(
                r["semantic_status"] != "HUMAN_VERIFIED_MEANING" for r in rows
            ),
            "citation_verified_findings": sum(
                r["citation_status"] == "VERIFIED_QUOTE" for r in rows
            ),
            "citation_invalid_findings": sum(
                r["citation_status"] == "CITATION_INVALID" for r in rows
            ),
            "no_citations_applicable_batches": len(consolidation["no_relevant_findings_batches"]),
        },
        "api_calls": 0,
        "ledger_mutations": 0,
        "semantic_accuracy": "NOT_SCORED",
        "reviewed_source_span_coverage": "SEPARATE_BENCHMARK_MEASURE",
    }


def review_template(layer):
    return {
        "schema_version": "legal-reference-meaning-reviews-1",
        "consolidation_sha256": layer["original_consolidation_sha256"],
        "provenance": layer["provenance"],
        "reviews": [
            {
                "finding_id": r["finding_id"],
                "original_response_sha256s": sorted(
                    {o["response_file_sha256"] for o in r["origins"]}
                ),
                "status": "DRAFT",
                "author_alias": None,
                "reviewer_alias": None,
                "reviewed_at": None,
                "review_notes": "",
            }
            for r in layer["associated_findings"]
        ],
    }
