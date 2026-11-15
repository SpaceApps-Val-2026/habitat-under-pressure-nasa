"""Deterministic checks on unrendered finding text."""

import re

from hup_api.writer import SLOT, fill

CAUSAL = re.compile(
    r"\b(?:caus(?:e|es|ed|ing|a|as|ó|aron|ado|ada)|provoc\w*|due to|owing to|because of|led to|leads? to|results? in|"
    r"responsible for|trigger\w*|driv(?:en|es?|ing) by|debido a|por culpa de|"
    r"da lugar a|dio lugar a|atribu\w*|a consecuencia de|se debe a|porque|a causa de)\b",
    re.IGNORECASE,
)
_REFUSAL = re.compile(
    r"^(?:the (?:data|evidence|observations) (?:cannot|do not) (?:establish|show|tell) (?:whether|that)|"
    r"the (?:data|evidence|observations) cannot attribute|"
    r"(?:los datos|las observaciones|la evidencia) no permiten (?:afirmar que|determinar si|atribuir)|"
    r"no se puede afirmar que)\s+[^,;:!?]+$", re.I,
)
_ADDED_CLAIM = re.compile(r"\b(?:pero|sin embargo|aunque|porque|but|yet|however|although|because)\b", re.I)
_EFFECT_CLAIM = re.compile(r"\b(?:affect(?:s|ed|ing)?|afect\w*|impacted|impactó|alteró|produjo)\b", re.I)


def denies_attribution(text):
    """Only a complete statement of what the evidence cannot establish."""
    sentence = text.strip().rstrip(".")
    return bool(_REFUSAL.fullmatch(sentence) and not _ADDED_CLAIM.search(sentence))


LEGAL_VERDICT = re.compile(
    r"\b(?:incumpl\w*|ilegal\w*|illegal\w*|breach\w*|violat\w*|non[- ]compliant|"
    r"(?:complies with|exceeds?|meets?) (?:the |a )?(?:law|legal limit|permit)|"
    r"(?:cumple|supera|rebasa) (?:la |el |un )?(?:ley|límite legal|permiso))\b",
    re.IGNORECASE,
)
LEVELS = (
    ("E1", r"\b(?:trend|tendencia|increas\w*|decreas\w*|fell|rose|subi[oó]|baj[oó]|aument\w*|descend\w*)\b"),
    ("E2", r"\b(?:coincid\w*)\b"),
    ("E3", r"\b(?:associat\w*|asocia\w*|correlat\w*|correla\w*|accompanied by|va acompañado de)\b"),
    ("E4", r"\b(?:exceptional|excepcional|lugares comparables|comparable sites)\b"),
    ("E5", r"\b(?:consistent with|coherente con)\b"),
)


def validate_text(finding, results, *, strict_attribution=False):
    level = finding.get("evidence_level", "E0")
    for language, text in finding["text"].items():
        plain = SLOT.sub("", text)
        if LEGAL_VERDICT.search(plain):
            return "legal_verdict"
        if any((CAUSAL.search(sentence) or (strict_attribution and _EFFECT_CLAIM.search(sentence)))
               and not denies_attribution(sentence)
               for sentence in re.split(r"[.!?](?=\s|$)", plain)):
            return "causal_language"
        if re.search(r"\d", re.sub(r"\b(?:19|20)\d\d\b", "", plain)):
            return "literal_figure"
        try:
            fill(text, results, language)
        except ValueError:
            return "invalid_placeholder"
        if any(level < minimum and re.search(pattern, plain, re.IGNORECASE) for minimum, pattern in LEVELS):
            return "unsupported_verb"
    return None
