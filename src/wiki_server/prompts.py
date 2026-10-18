"""Editable prompt guidance for the three dream stages (triage, decide, write).

Each prompt has two parts: the *guidance* (how to think), stored as an editable
file in the wiki and seeded with a default here; and the *schema* (the exact JSON
the code parses), kept in code and always appended by the daemon so editing the
guidance can never break the machine contract. A missing/edited file falls back
to nothing special: the code seeds the default if absent.
"""

from __future__ import annotations

from wiki_server.paths import resolve_under_root
from wiki_server.store import write_file

PROMPT_FILES = {
    "triage": "prompts/triage.md",
    "decide": "prompts/decide.md",
    "write": "prompts/write.md",
}

TRIAGE_DEFAULT = """# Prompt: triage (étape 1)

Tu es le trieur du Personal Memory Wiki. On te donne la politique (DREAM.md),
toute la mémoire court terme, et l'index de la mémoire long terme (le catalogue,
pas le contenu des pages).

Regroupe les entrées court terme par cohérence de sens (même personne, projet,
lieu, sujet ou idée), pas par tags ni par date d'écriture. Pour chaque groupe,
indique :
- les fichiers court terme concernés,
- une intention courte et neutre,
- les pages long terme qu'il touche, comme une liste de chemins canoniques : pour
  un sujet déjà au catalogue, reprends son chemin tel quel ; pour un sujet
  nouveau, propose un chemin déterministe en minuscules-à-tirets
  (catégorie/slug). Un même sujet doit recevoir EXACTEMENT le même chemin dans
  toutes les unités qui le touchent, pour qu'une seule page existe au final,
- un indice d'action dominant : integrate (une page existante à enrichir),
  promote (un sujet durable sans page encore), temporal (une chose datée ou
  actionnable), keep (pas assez mûr, à laisser en court terme).

Désambiguïse les homonymes. Avant de réutiliser une page existante pour une
personne (ou un lieu, une organisation), vérifie dans la description de l'index
que c'est bien le MÊME individu. Si c'est quelqu'un de différent qui porte le même
prénom, ne réutilise pas sa page : propose un chemin distinct avec un qualificatif
(relation, rôle, nom de famille, contexte), par ex. people/jules-frere-de-margaux.md
vs people/jules-collegue.md. Le prénom nu reste pour le premier ; les suivants
sont qualifiés. Ne fonds jamais deux personnes distinctes dans une seule page. Si
deux personnes différentes du même prénom apparaissent dans le lot, donne-leur des
chemins distincts.

Le self a un ensemble fixe de pages : les préférences de communication/édition de
l'utilisateur vont sur self/style.md ; ses voix ou personnages de pensée sur
self/voices.md ; tout le reste sur lui (bio, métier, relations, goûts, valeurs)
va sur self/identity.md. Pas d'autre page self.

Une personne désignée par sa relation (« ma copine », « mon père », « mon
manager ») est la personne déjà au catalogue pour cette relation : reprends sa
page, n'en crée pas une nouvelle au nom de la relation. Idem pour une
organisation désignée par un sigle ou un nom court (ONU = Organisation des Nations unies).

On te donne aussi les items temporels actifs. Une entrée qui dit qu'une tâche est
faite, annulée ou obsolète, ou qui répète un item déjà filé, forme une unité à
part entière (indice temporal) : l'étape suivante clôturera ou complétera
l'item existant au lieu d'en créer un doublon.

L'indice est une tendance, pas une contrainte : un même groupe peut avoir une
part durable et une part datée, et l'étape suivante pourra produire les deux.
Sois sélectif et fidèle ; n'invente rien et ne déduis pas au-delà du texte.
"""

DECIDE_DEFAULT = """# Prompt: decide (étape 2)

Tu décides quoi faire d'UNE unité. On te donne la politique (DREAM.md), l'unité
(les textes court terme), et le contenu actuel des pages long terme qu'elle
touche.

Une entrée peut concerner plusieurs sujets : liste une opération par page long
terme touchée (integrate pour une page existante, promote pour une nouvelle), et
un item par chose datée. Une entrée qui mentionne plusieurs personnes, projets ou
sujets touche donc une page par sujet, pas une seule. Reprends les chemins exacts
fournis dans touched_pages comme chemins canoniques, sans inventer de variante :
integrate si la page a déjà du contenu, promote si elle est vide (nouvelle).
N'intègre que si la page existante décrit bien le MÊME individu : si le contenu
montre que c'est une autre personne qui porte le même prénom, ne l'écrase pas,
promote plutôt une nouvelle page avec un slug qualifié (relation, rôle, nom de
famille). Deux personnes distinctes ne partagent jamais une page. Si rien n'est
assez mûr,
laisse l'entrée en court terme (listes vides). Pour les items temporels : type
(todo/reminder/event) et "due" OBLIGATOIRE = la date (YYYY-MM-DD) jusqu'à laquelle
l'item reste actif (échéance d'une tâche, date de fin d'un séjour). Un item
temporel a toujours une fin : s'il n'a pas de date d'expiration, ce n'est pas un
item temporel mais un fait durable, qui va alors sur une page long terme (avec sa
date dans le texte), jamais dans temporal/. Ne sur-fusionne pas : plusieurs choses
datées distinctes dans une même unité donnent un item temporel chacune, avec sa
propre échéance.

Sépare le fait durable de son emballage daté. Une chose datée mentionne souvent
un fait qui, lui, est durable : une personne, une relation, un lieu, une
préférence. Produis une page (promote ou integrate) pour la partie durable, et un
item temporel pour la partie datée seulement si elle a une date de fin. Un
événement marquant mais sans échéance (une naissance, une rencontre) est un fait
durable : il va sur la page concernée, pas dans temporal/. Une personne ou une
relation qui revient mérite sa page même si elle n'apparaît que dans des choses
datées. Tu ne supprimes jamais.

Avant tout promote, regarde related_pages : ce sont des pages existantes que le
code a trouvées parce que leur nom ou leur description apparaît dans l'unité. Si
l'une d'elles parle du même sujet (même personne, même organisation, même
projet, y compris désigné par une relation ou un sigle), integrate dans cette
page au lieu de créer une variante.

Regarde aussi temporal_actifs. Si l'unité dit qu'un item est fait, annulé ou
dépassé, ou si elle le remplace par une version corrigée, mets son chemin dans
temporal_done (et crée le nouvel item corrigé s'il y en a un). Ne crée jamais un
item temporel qui existe déjà : au besoin, clôture l'ancien et crée la version
juste.

Quand tu n'es pas sûr, ne devine pas : demande. Deux pages qui parlent peut-être
de la même personne, une date qui en contredit une autre, un statut flou (fait ou
seulement prévu ?), une relation ambiguë. Écris ce qui est sûr, et mets dans
questions une question courte, une seule chose à la fois, formulée pour l'utilisateur
et compréhensible seule (rappelle le contexte : qui, quoi, quelles pages). Si
rien n'est sûr dans l'unité, laisse-la en court terme et pose seulement la
question. Ne repose jamais une question déjà présente dans questions_ouvertes.
"""

WRITE_DEFAULT = """# Prompt: write (étape 3)

Tu écris UNE page de la mémoire long terme. On te donne la politique (DREAM.md),
l'opération à appliquer, et le contenu actuel de la page (si elle existe déjà).

Écris des notes factuelles, claires et concises. Pas de style d'auteur, pas de
voix narrative, ton neutre. Pour une intégration, renvoie le contenu COMPLET
fusionné (pas un diff), sans rien dupliquer ni perdre de l'existant. Pour une
nouvelle page, structure l'essentiel sans la gonfler. Inclus les liens markdown
utiles vers les pages connexes. Donne aussi une description d'une ligne pour
l'index. N'invente rien qui ne soit pas dans la source ou la page actuelle.

Les entrées court terme d'origine sont fournies dans sources : sers-t'en pour
garder les détails exacts (dates, chiffres, noms) que le résumé de l'opération
aurait perdus. Quand un fait nouveau remplace un ancien (statut, poste, adresse,
date corrigée), écris l'état actuel et garde l'ancien en historique daté plutôt
que de laisser deux affirmations contradictoires. N'écris jamais de secret
(mot de passe, clé de récupération, clé d'API, code) dans une page.
"""

DEFAULTS = {"triage": TRIAGE_DEFAULT, "decide": DECIDE_DEFAULT, "write": WRITE_DEFAULT}

# Output contracts, controlled by code (always appended to the guidance).
SCHEMAS = {
    "triage": (
        'Renvoie UNIQUEMENT cet objet JSON, sans texte autour ni bloc de code :\n'
        '{"units": [{"stm": ["<nom de fichier court terme>"], "intent": "<courte description>", '
        '"touches": ["long_term/<chemin>.md"], "hint": "integrate|promote|temporal|keep"}]}\n'
        '"touches" liste les chemins canoniques des pages touchées, existants ou '
        'proposés pour un sujet nouveau ; un même sujet reçoit le même chemin dans '
        'toutes les unités. Pour une page déjà au catalogue, reprends son chemin '
        'exact (préfixe long_term/ compris).'
    ),
    "decide": (
        'Renvoie UNIQUEMENT cet objet JSON, sans texte autour ni bloc de code :\n'
        '{"pages": [{"action": "integrate|promote", "page": "long_term/<chemin>.md", '
        '"change": "<ce qu\'on ajoute/fusionne>"}], '
        '"temporal": [{"type": "todo|reminder|event", "due": "YYYY-MM-DD", "content": "<texte>"}], '
        '"temporal_done": ["temporal/<fichier>.md"], '
        '"questions": ["<question courte pour l\'utilisateur>"], '
        '"rationale": "<courte justification>"}\n'
        '"pages" et "temporal" sont des LISTES. Une entrée peut toucher plusieurs pages '
        '(personnes, projets, sujets) et/ou plusieurs choses datées : mets une opération '
        'par page (integrate pour une page existante, promote pour une nouvelle) et un '
        'item par chose datée. Chaque item temporel a une "due" (YYYY-MM-DD) obligatoire ; '
        'sans échéance, ce n\'est pas un item temporel mais un fait durable (une page). '
        '"temporal_done" liste les items de temporal_actifs à clôturer (faits, annulés, '
        'dépassés ou remplacés par une version corrigée). Si une page de related_pages '
        'traite du même sujet, integrate dans celle-ci plutôt que de promote une variante. '
        '"questions" : ce que tu n\'es pas sûr de comprendre (même personne ? quelle date ? '
        'fait ou prévu ?), au lieu de deviner ; jamais une question déjà dans questions_ouvertes. '
        'Tout vide = garder l\'entrée en court terme.'
    ),
    "write": (
        'Renvoie UNIQUEMENT cet objet JSON, sans texte autour ni bloc de code :\n'
        '{"content": "<markdown COMPLET final de la page, liens inclus>", '
        '"description": "<une ligne pour l\'index>"}\n'
        "Un fait qui peut changer (statut, poste, projet, santé, matériel, record) "
        "porte la date à laquelle il était vrai, « (au AAAA-MM-JJ) », prise de la date "
        "de l'entrée source ; une estimation est marquée « (estimation) », un fait "
        "rapporté par quelqu'un d'autre « (selon <nom>) »."
        " Sur une page de personne (people/), garde en haut une section « ## Fiche » "
        "quand les sources le permettent : « - Anniversaire : <jour mois [année]> », "
        "« - Idées cadeaux : … », « - Aime : … », « - Dernière rencontre : <date> »."
    ),
}


def _obj(props: dict) -> dict:
    # Structured outputs require every property listed and no extras.
    return {"type": "object", "properties": props, "required": list(props),
            "additionalProperties": False}


_STR = {"type": "string"}
_STR_LIST = {"type": "array", "items": _STR}

# The same contracts as SCHEMAS, as JSON schemas for structured output, so the
# model's reply is valid JSON by construction (the text above still explains
# the meaning of each field).
JSON_SCHEMAS = {
    "triage": _obj({"units": {"type": "array", "items": _obj({
        "stm": _STR_LIST, "intent": _STR, "touches": _STR_LIST,
        "hint": {"type": "string", "enum": ["integrate", "promote", "temporal", "keep"]},
    })}}),
    "decide": _obj({
        "pages": {"type": "array", "items": _obj({
            "action": {"type": "string", "enum": ["integrate", "promote"]},
            "page": _STR, "change": _STR})},
        "temporal": {"type": "array", "items": _obj({
            "type": {"type": "string", "enum": ["todo", "reminder", "event"]},
            "due": _STR, "content": _STR})},
        "temporal_done": _STR_LIST,
        "questions": _STR_LIST,
        "rationale": _STR,
    }),
    "write": _obj({"content": _STR, "description": _STR}),
}


def personalize(text: str) -> str:
    """The default guidance speaks of "l'utilisateur"; with ``WIKI_OWNER_NAME``
    set, it uses that first name instead ("ce que Camille affirme")."""
    import os

    name = os.environ.get("WIKI_OWNER_NAME", "").strip()
    if not name:
        return text
    return text.replace("l'utilisateur", name).replace("L'utilisateur", name)


def ensure_prompt(stage: str) -> str:
    """Return the editable guidance for a stage, seeding the default if absent."""
    rel = PROMPT_FILES[stage]
    path = resolve_under_root(rel)
    if not path.is_file():
        write_file(rel, personalize(DEFAULTS[stage]), f"dream: add default {rel}")
    return path.read_text(encoding="utf-8")


def reset_prompts() -> None:
    """Overwrite the policy (DREAM.md) and the three stage prompts with their
    shipped defaults, in a single revertible commit. Lets the owner restore the
    canonical guidance from the UI after editing or after deploying a new default
    (deploys never touch these editable files)."""
    from wiki_server.dream.config import DEFAULT_DREAM_MD, DREAM_POLICY
    from wiki_server.store import write_files

    files = {DREAM_POLICY: personalize(DEFAULT_DREAM_MD)}
    for stage, rel in PROMPT_FILES.items():
        files[rel] = personalize(DEFAULTS[stage])
    write_files(files, "manual: reset prompts to defaults")


def build(stage: str, context: str) -> str:
    """Full prompt = editable guidance + injected context + fixed JSON schema."""
    guidance = ensure_prompt(stage)
    return f"{guidance}\n\n{context}\n\n{personalize(SCHEMAS[stage])}"
