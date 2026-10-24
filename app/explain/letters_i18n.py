"""The supplier letter in English, French, Spanish or Portuguese, with every id, date, number and quote kept.

The letter is the one the run already wrote (app/explain/exports.supplier_letters). Two ways to put it
in another language, tried in this order:
  model      the run had a model: Nemotron translates the whole letter (Writer, fast lane, through
             app/adapters/llm.ModelGate). Its answer is kept only if every protected token comes back
             character for character, as many times as in the English letter.
  templates  otherwise, or if the model changed a token: sentences written by hand for each language,
             filled with the values read from the English letter itself. A sentence no template knows
             stays in English, and is listed under "untranslated".
Protected tokens: ids (A-221, P-014, F-007), dates, numbers, document ids (ngo_2025), quotes, the
supplier's name and the names of the sources (Hansen/UMD, GreenTrace).
"""
import re
from collections import Counter
from typing import Any, Optional

from app.checks.landcover import NO_DATA
from app.rules import NO_EU_FOREST_MAP, TREE_COVER_NOT_FOREST

LANGS = ("en", "fr", "es", "pt")
LANGUAGE_NAMES = {"en": "English", "fr": "French", "es": "Spanish", "pt": "Portuguese"}
LITERALS = ("Hansen/UMD", "GreenTrace")

_TOKEN = re.compile(r'"[^"\n]+"|“[^”\n]+”|\b\d{4}-\d{2}-\d{2}\b|\b[A-Z]{1,3}-\d{2,6}\b|\b[A-Za-z]+_\d{4}\b'
                    r'|\d+(?:\.\d+)?')

# The English letter, as app/explain/exports._letter writes it.
_LETTER = re.compile(
    r"^DRAFT prepared by GreenTrace, to be reviewed by our compliance team before sending\.\n\n"
    r"\[date\]\n\n"
    r"Dear (?P<name>.+) \((?P<sid>[^()\n]+)\),\n\n"
    r"As part of our review of the cocoa we bought in (?P<year>\d{4}), our checks found the following points "
    r"about the plots and volumes in our records for you:\n\n"
    r"(?P<points>(?:- .+\n)+)\n"
    r"These points come from public satellite data and from our own registry\. They may have a simple "
    r"explanation, and we would like to understand them with you\.\n"
    r"(?:\nTo complete our file, could you send us:\n\n(?P<requests>(?:- .+\n)+))?\n"
    r"Thank you for your help\.\n\nKind regards,\n\n\[name, compliance team\]\n$")

_YEARS = r"(?P<years>\d{4}(?:, \d{4})*)"
_NUM = r"[\d.]+"
# One point of the letter (first letter upper-cased, final full stop removed) -> its template key.
POINTS = {
    "confirmed_alert": rf"Alert (?P<alert>\S+) \((?P<date>\d{{4}}-\d{{2}}-\d{{2}}), (?P<conf>\w+)\) lies inside "
                       rf"(?P<plot>\S+), which delivered in (?P<year>\d{{4}}) or later",
    "unconfirmed_alert": r"Alert (?P<alert>\S+) inside (?P<plot>\S+) has (?P<conf>\w+) confidence only",
    "alert_near_point": r"Alert (?P<alert>\S+) is (?P<m>\d+) m from (?P<plot>\S+), declared by a point: "
                        r"a polygon is needed to decide",
    "invalid_coordinates": r"(?P<plot>\S+) lies outside Cote d'Ivoire as declared",
    "polygon_missing": rf"(?P<plot>\S+) covers (?P<ha>{_NUM}) ha but is declared by a point",
    "impossible_yield": rf"(?P<plot>\S+) declared (?P<tha>{_NUM}) t/ha in (?P<year>\d{{4}}) "
                        rf"\(above (?P<max>{_NUM}) t/ha\)",
    "declared_twice": r"(?P<plot>\S+) is declared by (?P<owners>\S+(?: and \S+)+)",
    "sources_in_conflict": r"(?P<doc>\S+) paragraph (?P<n>\d+) reports clearing by (?P<who>.+) in "
                           r"(?P<year>\d{4}); no confirmed alert backs it",
    "forest_loss": rf"(?P<ha>{_NUM}) ha that the EU map counted as forest in 2020 were lost inside (?P<plot>\S+) "
                   rf"in {_YEARS} \(Hansen/UMD\), while it kept delivering \(threshold (?P<thr>{_NUM}) ha: "
                   rf"0\.5 ha or 10 % of the plot\)",
    "forest_loss_small": rf"(?P<ha>{_NUM}) ha of 2020 forest lost inside (?P<plot>\S+) in {_YEARS}, below 0\.5 ha "
                         rf"and 10 % of the plot while it delivered: confirm in the field",
    "tree_cover_not_forest": rf"(?P<plot>\S+): (?P<ha>{_NUM}) ha of tree cover lost in {_YEARS}; "
                             + re.escape(TREE_COVER_NOT_FOREST),
    "tree_cover_no_map": rf"(?P<plot>\S+): (?P<ha>{_NUM}) ha of tree cover lost in {_YEARS} \(Hansen/UMD\); "
                         + re.escape(NO_EU_FOREST_MAP),
    "maps_silent": r"(?P<plot>\S+): the reference maps did not answer",
    "no_map": r"(?P<plot>\S+): " + re.escape(NO_DATA),
    "untraced": rf"The (?P<t>{_NUM}) t you delivered in (?P<year>\d{{4}}) are not linked to any plot in our registry",
}
# One request of the letter (leading "- " and a final full stop removed).
REQUESTS = {
    "field_report": r"A field report on (?P<plot>\S+)",
    "polygon": r"The polygon of (?P<plot>\S+)",
    "coordinates": r"Please correct the coordinates of (?P<plot>\S+)",
    "registry": r"Your plot registry",
    "other_plots": r"Which other plots the volume of (?P<plot>\S+) came from\?",
    "which_one": r"Which one farms (?P<plot>\S+)\?",
    "plot_records": r"Your plot records for the year the NGO cites",
}

TEMPLATES: dict[str, dict[str, Any]] = {
    "fr": {
        "and": " et ",
        "confidence": {"low": "faible", "high": "élevée", "highest": "très élevée"},
        "head": ("PROJET préparé par GreenTrace, à relire par notre équipe conformité avant envoi.\n\n[date]\n\n"
                 "À l'attention de {name} ({sid}),\n\nMadame, Monsieur,\n\n"
                 "Dans le cadre de notre revue du cacao acheté en {year}, nos contrôles ont relevé les points "
                 "suivants concernant les parcelles et les volumes enregistrés à votre nom :"),
        "explain": ("Ces points proviennent de données satellitaires publiques et de notre propre registre. "
                    "Ils peuvent avoir une explication simple, et nous souhaitons les comprendre avec vous."),
        "ask": "Pour compléter notre dossier, pourriez-vous nous envoyer :",
        "tail": "Merci pour votre aide.\n\nCordialement,\n\n[nom, équipe conformité]",
        "points": {
            "confirmed_alert": "L'alerte {alert} ({date}, confiance {conf}) se trouve dans la parcelle {plot}, "
                               "qui a livré en {year} ou après.",
            "unconfirmed_alert": "L'alerte {alert} dans la parcelle {plot} n'a qu'une confiance {conf}.",
            "alert_near_point": "L'alerte {alert} est à {m} m de la parcelle {plot}, déclarée par un point : "
                                "un polygone est nécessaire pour trancher.",
            "invalid_coordinates": "La parcelle {plot} se trouve hors de Côte d'Ivoire telle que déclarée.",
            "polygon_missing": "La parcelle {plot} couvre {ha} ha mais est déclarée par un point.",
            "impossible_yield": "La parcelle {plot} a déclaré {tha} t/ha en {year} (au-delà de {max} t/ha).",
            "declared_twice": "La parcelle {plot} est déclarée par {owners}.",
            "sources_in_conflict": "Le paragraphe {n} de {doc} signale un défrichement par {who} en {year} ; "
                                   "aucune alerte confirmée ne le corrobore.",
            "forest_loss": "{ha} ha que la carte de l'UE comptait comme forêt en 2020 ont été perdus dans la "
                           "parcelle {plot} en {years} (Hansen/UMD), alors qu'elle continuait de livrer "
                           "(seuil {thr} ha : 0.5 ha ou 10 % de la parcelle).",
            "forest_loss_small": "{ha} ha de forêt de 2020 perdus dans la parcelle {plot} en {years}, sous 0.5 ha "
                                 "et 10 % de la parcelle pendant qu'elle livrait : à confirmer sur le terrain.",
            "tree_cover_not_forest": "{plot} : {ha} ha de couvert arboré perdus en {years} ; du couvert arboré a été "
                                     "perdu, mais la carte de l'UE de 2020 ne comptait pas ce terrain comme forêt : "
                                     "à confirmer sur le terrain.",
            "tree_cover_no_map": "{plot} : {ha} ha de couvert arboré perdus en {years} (Hansen/UMD) ; carte "
                                 "forestière de l'UE de 2020 non disponible ici : la perte indiquée concerne le "
                                 "couvert arboré, pas la forêt au sens de l'UE.",
            "maps_silent": "{plot} : les cartes de référence n'ont pas répondu.",
            "no_map": "{plot} : pas encore de carte de référence pour cette zone.",
            "untraced": "Les {t} t que vous avez livrées en {year} ne sont liées à aucune parcelle de notre registre.",
        },
        "requests": {
            "field_report": "Un rapport de terrain sur la parcelle {plot}.",
            "polygon": "Le polygone de la parcelle {plot}.",
            "coordinates": "Merci de corriger les coordonnées de la parcelle {plot}.",
            "registry": "Votre registre de parcelles.",
            "other_plots": "De quelles autres parcelles provient le volume de la parcelle {plot} ?",
            "which_one": "Lequel de vous exploite la parcelle {plot} ?",
            "plot_records": "Vos registres de parcelles pour l'année citée par l'ONG.",
        },
    },
    "es": {
        "and": " y ",
        "confidence": {"low": "baja", "high": "alta", "highest": "muy alta"},
        "head": ("BORRADOR preparado por GreenTrace, que nuestro equipo de cumplimiento debe revisar antes de "
                 "enviarlo.\n\n[fecha]\n\nA la atención de {name} ({sid}):\n\n"
                 "En el marco de nuestra revisión del cacao que compramos en {year}, nuestros controles detectaron "
                 "los siguientes puntos sobre las parcelas y los volúmenes registrados a su nombre:"),
        "explain": ("Estos puntos proceden de datos satelitales públicos y de nuestro propio registro. Pueden tener "
                    "una explicación sencilla y nos gustaría entenderlos con ustedes."),
        "ask": "Para completar nuestro expediente, les rogamos que nos envíen:",
        "tail": "Gracias por su ayuda.\n\nAtentamente,\n\n[nombre, equipo de cumplimiento]",
        "points": {
            "confirmed_alert": "La alerta {alert} ({date}, confianza {conf}) se encuentra dentro de la parcela "
                               "{plot}, que entregó en {year} o después.",
            "unconfirmed_alert": "La alerta {alert} dentro de la parcela {plot} solo tiene confianza {conf}.",
            "alert_near_point": "La alerta {alert} está a {m} m de la parcela {plot}, declarada por un punto: "
                                "se necesita un polígono para decidir.",
            "invalid_coordinates": "La parcela {plot} se encuentra fuera de Costa de Marfil según lo declarado.",
            "polygon_missing": "La parcela {plot} abarca {ha} ha pero está declarada por un punto.",
            "impossible_yield": "La parcela {plot} declaró {tha} t/ha en {year} (por encima de {max} t/ha).",
            "declared_twice": "La parcela {plot} está declarada por {owners}.",
            "sources_in_conflict": "El párrafo {n} de {doc} informa de un desmonte por parte de {who} en {year}; "
                                   "ninguna alerta confirmada lo respalda.",
            "forest_loss": "{ha} ha que el mapa de la UE contaba como bosque en 2020 se perdieron dentro de la "
                           "parcela {plot} en {years} (Hansen/UMD), mientras seguía entregando (umbral {thr} ha: "
                           "0.5 ha o 10 % de la parcela).",
            "forest_loss_small": "{ha} ha de bosque de 2020 perdidas dentro de la parcela {plot} en {years}, por "
                                 "debajo de 0.5 ha y del 10 % de la parcela mientras entregaba: confirmar sobre el "
                                 "terreno.",
            "tree_cover_not_forest": "{plot}: {ha} ha de cobertura arbórea perdidas en {years}; se perdió cobertura "
                                     "arbórea, pero el mapa de la UE de 2020 no contaba este terreno como bosque: "
                                     "confirmar sobre el terreno.",
            "tree_cover_no_map": "{plot}: {ha} ha de cobertura arbórea perdidas en {years} (Hansen/UMD); mapa "
                                 "forestal de la UE de 2020 no disponible aquí: la pérdida indicada es de cobertura "
                                 "arbórea, no de bosque según la UE.",
            "maps_silent": "{plot}: los mapas de referencia no respondieron.",
            "no_map": "{plot}: todavía no hay mapa de referencia para esta zona.",
            "untraced": "Las {t} t que ustedes entregaron en {year} no están vinculadas a ninguna parcela de nuestro "
                        "registro.",
        },
        "requests": {
            "field_report": "Un informe de campo sobre la parcela {plot}.",
            "polygon": "El polígono de la parcela {plot}.",
            "coordinates": "Por favor, corrijan las coordenadas de la parcela {plot}.",
            "registry": "Su registro de parcelas.",
            "other_plots": "¿De qué otras parcelas procede el volumen de la parcela {plot}?",
            "which_one": "¿Cuál de ustedes cultiva la parcela {plot}?",
            "plot_records": "Sus registros de parcelas del año que cita la ONG.",
        },
    },
    "pt": {
        "and": " e ",
        "confidence": {"low": "baixa", "high": "alta", "highest": "muito alta"},
        "head": ("RASCUNHO preparado pelo GreenTrace, a rever pela nossa equipa de conformidade antes do envio."
                 "\n\n[data]\n\nÀ atenção de {name} ({sid}):\n\n"
                 "No âmbito da nossa revisão do cacau que comprámos em {year}, os nossos controlos identificaram "
                 "os seguintes pontos sobre as parcelas e os volumes registados em vosso nome:"),
        "explain": ("Estes pontos provêm de dados de satélite públicos e do nosso próprio registo. Podem ter uma "
                    "explicação simples, e gostaríamos de os compreender convosco."),
        "ask": "Para completar o nosso processo, poderiam enviar-nos:",
        "tail": "Obrigado pela vossa ajuda.\n\nCom os melhores cumprimentos,\n\n[nome, equipa de conformidade]",
        "points": {
            "confirmed_alert": "O alerta {alert} ({date}, confiança {conf}) situa-se dentro da parcela {plot}, que "
                               "entregou em {year} ou depois.",
            "unconfirmed_alert": "O alerta {alert} dentro da parcela {plot} tem apenas confiança {conf}.",
            "alert_near_point": "O alerta {alert} está a {m} m da parcela {plot}, declarada por um ponto: é "
                                "necessário um polígono para decidir.",
            "invalid_coordinates": "A parcela {plot} situa-se fora da Costa do Marfim, tal como declarada.",
            "polygon_missing": "A parcela {plot} abrange {ha} ha mas está declarada por um ponto.",
            "impossible_yield": "A parcela {plot} declarou {tha} t/ha em {year} (acima de {max} t/ha).",
            "declared_twice": "A parcela {plot} está declarada por {owners}.",
            "sources_in_conflict": "O parágrafo {n} de {doc} relata uma desflorestação por {who} em {year}; nenhum "
                                   "alerta confirmado o corrobora.",
            "forest_loss": "{ha} ha que o mapa da UE contava como floresta em 2020 foram perdidos dentro da parcela "
                           "{plot} em {years} (Hansen/UMD), enquanto continuava a entregar (limiar {thr} ha: 0.5 ha "
                           "ou 10 % da parcela).",
            "forest_loss_small": "{ha} ha de floresta de 2020 perdidos dentro da parcela {plot} em {years}, abaixo "
                                 "de 0.5 ha e de 10 % da parcela enquanto entregava: confirmar no terreno.",
            "tree_cover_not_forest": "{plot}: {ha} ha de coberto arbóreo perdidos em {years}; perdeu-se coberto "
                                     "arbóreo, mas o mapa da UE de 2020 não contava este terreno como floresta: "
                                     "confirmar no terreno.",
            "tree_cover_no_map": "{plot}: {ha} ha de coberto arbóreo perdidos em {years} (Hansen/UMD); mapa "
                                 "florestal da UE de 2020 não disponível aqui: a perda indicada é de coberto "
                                 "arbóreo, não de floresta segundo a UE.",
            "maps_silent": "{plot}: os mapas de referência não responderam.",
            "no_map": "{plot}: ainda não há mapa de referência para esta zona.",
            "untraced": "As {t} t que entregaram em {year} não estão ligadas a nenhuma parcela do nosso registo.",
        },
        "requests": {
            "field_report": "Um relatório de campo sobre a parcela {plot}.",
            "polygon": "O polígono da parcela {plot}.",
            "coordinates": "Por favor, corrijam as coordenadas da parcela {plot}.",
            "registry": "O vosso registo de parcelas.",
            "other_plots": "De que outras parcelas provém o volume da parcela {plot}?",
            "which_one": "Qual de vós cultiva a parcela {plot}?",
            "plot_records": "Os vossos registos de parcelas do ano citado pela ONG.",
        },
    },
}

SYSTEM = ("You translate a draft letter to a cocoa supplier from English into {language}. Keep every identifier "
          "(such as A-221, P-014, F-007, ngo_2025), every date (YYYY-MM-DD), every number, every name and every "
          "quoted text exactly as written, character for character: never reformat a number (keep the decimal "
          "point). Keep the line breaks and the list dashes. Translate everything else, placeholders in square "
          "brackets included, in a polite and neutral tone. Answer with the translated letter only.")
SCHEMA = {"type": "object", "required": ["letter"], "additionalProperties": False,
          "properties": {"letter": {"type": "string", "minLength": 20, "maxLength": 12000}}}


# ---- protected tokens -------------------------------------------------------------------------------------

def protected(text: str, names: tuple[str, ...] = ()) -> Counter:
    """What must survive a translation unchanged: each token with how many times it appears."""
    found = Counter(_TOKEN.findall(text))
    for literal in (*LITERALS, *names):
        if literal and text.count(literal):
            found[literal] += text.count(literal)
    return found


# ---- the letters of a run ------------------------------------------------------------------------------------

def letters(run: Any, lang: str, model: Any = None, supplier: Optional[str] = None) -> dict[str, Any]:
    """GET /runs/{run_id}/letter?lang=: every supplier letter of the run (or one), in lang."""
    if lang not in LANGS:
        raise ValueError(f"lang must be one of {', '.join(LANGS)}, not {lang!r}")
    drafts = [d for d in run.output.get("supplier_letters", []) if supplier in (None, d["supplier_id"])]
    if supplier is not None and not drafts:
        raise KeyError(f"no letter for supplier {supplier!r} in this run")
    names = tuple(s["name"] for s in run.evidence.suppliers)
    out = [translate(d["letter"], lang, model, names) | {"supplier_id": d["supplier_id"], "name": d["name"]}
           for d in drafts]
    by = sorted({letter["translated_by"] for letter in out})
    return {"run_id": run.ctx.run_id, "lang": lang, "letters": out,
            "status": "success" if drafts else "no_letter",
            "reason": None if drafts else "no finding concerns a supplier: there is no letter to write",
            "translated_by": by[0] if len(by) == 1 else by}


def translate(english: str, lang: str, model: Any = None, names: tuple[str, ...] = ()) -> dict[str, Any]:
    """One letter in lang: the model's translation if it kept every protected token, else the templates."""
    base = {"letter_en": english, "protected_tokens": sum(protected(english, names).values())}
    if lang == "en":
        return {**base, "letter": english, "translated_by": "original", "untranslated": [], "model_rejected": None}
    rejected = None
    if model is not None:
        answer, info = model.complete_json("Writer", SYSTEM.format(language=LANGUAGE_NAMES[lang]),
                                           [("letter", english)], SCHEMA)
        if answer is None:
            rejected = f"no model answer ({info.get('provider')}): {', '.join(info.get('fallbacks') or []) or 'rules'}"
        else:
            rejected = check(english, answer["letter"], names)
            if rejected is None:
                return {**base, "letter": answer["letter"], "translated_by": "model", "untranslated": [],
                        "model": info.get("model"), "model_rejected": None}
    else:
        rejected = "no model for this run (sample mode makes no model call)"
    letter, untranslated = from_templates(english, lang)
    return {**base, "letter": letter, "translated_by": "templates", "untranslated": untranslated,
            "model_rejected": rejected}


def check(english: str, translated: str, names: tuple[str, ...] = ()) -> Optional[str]:
    """None when every protected token of the English letter is in the translation, the same number of times."""
    if translated.strip() == english.strip():
        return "the model gave the letter back untranslated"
    before, after = protected(english, names), protected(translated, names)
    if before == after:
        return None
    lost = sorted((before - after).elements())
    added = sorted((after - before).elements())
    return (f"the model changed protected tokens: missing {lost[:8]}" + (f", added {added[:8]}" if added else ""))


def from_templates(english: str, lang: str) -> tuple[str, list[str]]:
    """The letter rebuilt from the hand-written sentences of lang, and the sentences left in English."""
    match = _LETTER.match(english)
    if match is None:
        return english, ["the letter does not have the layout the templates know: kept in English"]
    t = TEMPLATES[lang]
    untranslated: list[str] = []
    points = [_sentence(line, POINTS, t["points"], t, untranslated, strip=".") for line in _items(match["points"])]
    lines = [t["head"].format(name=match["name"], sid=match["sid"], year=match["year"]), "",
             *[f"- {p}" for p in points], "", t["explain"]]
    if match["requests"]:
        requests = [_sentence(line, REQUESTS, t["requests"], t, untranslated, strip=".")
                    for line in _items(match["requests"])]
        lines += ["", t["ask"], "", *[f"- {r}" for r in requests]]
    lines += ["", t["tail"]]
    return "\n".join(lines) + "\n", untranslated


def _items(block: str) -> list[str]:
    return [line[2:] for line in block.splitlines() if line.startswith("- ")]


def _sentence(line: str, patterns: dict[str, str], templates: dict[str, str], t: dict, untranslated: list[str],
              strip: str) -> str:
    text = line[:-1] if line.endswith(strip) else line
    for key, pattern in patterns.items():
        found = re.fullmatch(pattern, text)
        if found:
            values = found.groupdict()
            if "conf" in values:
                values["conf"] = t["confidence"].get(values["conf"], values["conf"])
            if "owners" in values:
                values["owners"] = t["and"].join(values["owners"].split(" and "))
            return templates[key].format(**values)
    untranslated.append(line)
    return line
