# GreenTrace : architecture complète

25 octobre 2026 · Amir Ogan

## À quoi sert GreenTrace

GreenTrace vérifie si la promesse « zéro déforestation » d'un fournisseur de cacao tient face aux preuves, et explique pourquoi, jusqu'à la ligne de données. Il répond PROVEN, CONTRADICTED ou UNVERIFIABLE, et chaque verdict se revérifie hors ligne en une commande.

**Le problème.** Le règlement européen sur la déforestation (EUDR, règlement 2023/1115) s'applique à partir du 30 décembre 2026 : le cacao vendu dans l'UE doit venir de terres non déboisées après le 31 décembre 2020. L'importateur porte la preuve, parcelle par parcelle.

**L'utilisateur.** L'analyste conformité d'un importateur de cacao. Aujourd'hui, il croise à la main un rapport RSE, un registre de fournisseurs avec les parcelles GPS, les livraisons, les alertes satellite et les cartes forestières.

**Les trois verdicts.**

| Verdict | Ce qu'il veut dire | Exemple de la démo |
| --- | --- | --- |
| CONTRADICTED | Une preuve directe tient contre la promesse | Alerte A-221 du 14/03/2022 dans P-014, qui a livré jusqu'en 2025 |
| UNVERIFIABLE | Un doute que les données ne peuvent pas trancher | P-041 a perdu des arbres, mais la carte de l'UE ne comptait pas ce terrain comme forêt |
| PROVEN | Rien contre la promesse, sur tout ce qui a été vérifié | Le cas de base des 21 cas pièges |

GreenTrace est une aide à la décision : chaque verdict demande une revue humaine, ce n'est ni un avis juridique ni une note publique.

## Vue d'ensemble

Une enquête descend toujours le même chemin : Scout garde l'entrée, les agents rassemblent et attaquent les preuves avec l'aide des sponsors, puis les règles décident seules.

![Architecture de GreenTrace, du dossier au verdict vérifiable](architecture.png)

Les quatre briques du milieu servent les agents, aucune ne décide : le verdict sort uniquement des règles, puis `verify.py` peut le refaire sans aucune d'elles.

## Les cinq agents

Cinq agents travaillent sur une même enquête, et ils ne se font pas confiance : l'Évaluateur essaie de casser chaque preuve des autres avec ses propres requêtes. Aucun agent ne décide du verdict.

| Agent | Ce qu'il fait | Ce qui le contrôle |
| --- | --- | --- |
| Extractor | Lit la promesse et les rapports (RSE, ONG) : portée, aveux, mentions de fournisseurs | Nemotron lit 3 fois, on garde ce que 2 lectures sur 3 disent ; chaque citation est retrouvée mot pour mot par le code ; Scout met en quarantaine les paragraphes injectés |
| Planner | Liste ce qui doit être vrai pour que la promesse tienne (couverture, absence de déforestation, plausibilité, portée) | Repasse le plan quand l'Évaluateur trouve un doute non prévu |
| Investigator | Interroge les données via 11 requêtes fixes (Q0 à Q10) sur Zetaris et écrit chaque constat comme un fait dans le World Model | Ne peut lancer que ces 11 requêtes, chaque valeur est vérifiée avant d'entrer dans le SQL |
| Evaluator | Seul critique : tente de réfuter chaque constat (R1 à R9) avec ses propres requêtes | Nemotron propose quelles réfutations tenter, le code teste chacune |
| Writer | Rédige le verdict, la bascule, les lettres aux fournisseurs (4 langues), le dossier d'audit | Ne peut écrire aucune phrase sans source ; une traduction n'est gardée que si chaque identifiant, date et nombre revient intact |

Une cour d'enquête s'ajoute après coup : un Plaignant défend la promesse à partir des pièces déjà lues, l'Évaluateur répond, les règles jugent. Un test vérifie sur les 21 cas que la cour ne change jamais le verdict.

## Les règles décident, et tout se revérifie

Le verdict vient de règles fixes écrites en code (`app/rules.py`), jamais du modèle : mêmes données, même verdict, même sceau.

1. **Règle 1** : une source est absente ou une donnée invalide (hors Côte d'Ivoire, polygone manquant au-dessus de 4 ha) → UNVERIFIABLE.
2. **Règle 2** : une preuve directe tient (alerte confirmée dans une parcelle qui livre après le 31/12/2020, aveu de l'entreprise, forêt perdue d'au moins 0,5 ha ou 10 % de la parcelle) → CONTRADICTED.
3. **Règle 3** : un doute que les données ne tranchent pas (volume non tracé, rendement impossible, parcelle déclarée deux fois, alerte non confirmée, homonyme, terre non classée forêt en 2020) → UNVERIFIABLE.
4. **Règle 4** : rien de tout cela → PROVEN.

**La bascule.** Pour chaque verdict, le code essaie de changer une seule valeur à la fois et cherche le plus petit changement qui renverse le verdict : « PROVEN si l'alerte A-221 était datée du 31/12/2020 ». Le bouton Apply rejoue ce changement et revérifie tout.

**Le reçu.** Chaque verdict est livré avec `receipt.json` : le SQL, les lignes et leurs empreintes, les citations exactes, l'empreinte des règles et la bascule. `python verify.py receipt.json` reconstruit les preuves et rejoue les règles sans modèle, sans base de données et sans réseau. Il répond VALID, ou TAMPERED avec la ligne modifiée et le verdict que donnent les règles sur les données trouvées.

Un reçu prouve que le verdict découle des lignes qu'il porte, pas que ces lignes sont vraies.

## Les sponsors, chacun à sa place

Chaque sponsor fait un travail que rien d'autre ne fait dans GreenTrace, et chacun est mesuré.

| Brique | Rôle dans GreenTrace | Preuve |
| --- | --- | --- |
| Zetaris | Seul accès aux données : couche sémantique `eudr` (entreprise, fournisseurs, parcelles, livraisons, alertes, rapports, 716 800 cellules de carte), règles de qualité natives (DQ) qui attrapent latitude et longitude inversées | Exécution en direct : 16 requêtes traitées par Zetaris |
| Meterless World Model | Mémoire partagée des agents : chaque fait avec sa source ; quand un fait change, ce qui en dépendait tombe | Référence officielle non modifiée, conformité 8/8 |
| Meterless H-MEM | Une note par fournisseur, relue à l'enquête suivante (« Seen before ») ; ne décide jamais | Référence non modifiée, conformité 13/13 |
| Meterless Scout Intent | Garde chaque paragraphe avant que le modèle le lise ; transforme chaque question d'Ask en contrat signé vérifié | Notre implémentation de la spécification ; tous les seuils de la batterie officielle passés |
| Meterless Nightshift | Porte chaque appel au modèle et signe un reçu par réponse (Ed25519) que notre code vérifie | 21/21 à travers Nightshift, 89 reçus sur 89 valides |
| NVIDIA Nemotron | Lit les rapports et propose les réfutations : voie rapide Lightning 30B, voie profonde Ultra 550B via Nightshift | Le code vérifie chaque réponse ; les règles décident |

L'exécution en direct du 25 octobre 2026 utilise tous les sponsors à la fois et redonne le même sceau que l'exécution hors ligne (`24035b45…`) : le modèle lit, les règles décident.

## Les données réelles

L'entreprise ChocoDurable, ses fournisseurs et ses livraisons sont fictifs ; les cartes, les images et les parcelles P-041 et P-042 sont réelles, près de Soubré en Côte d'Ivoire.

- **Carte forestière de l'UE 2020** (JRC Global Forest Cover 2020, v4) : la parcelle était-elle une forêt à la date butoir.
- **Perte de couvert Hansen/UMD** (Global Forest Change v1.12) : l'année où chaque cellule de 30 m a perdu ses arbres.
- **Scènes Sentinel-2** : 8 images réelles de P-042 de 2019 à 2026, et les prochains passages calculés depuis les orbites réelles (TLE CelesTrak).

**P-042** : 4,62 ha de forêt selon la carte de l'UE en 2020, 3,42 ha perdus après la date butoir pendant que la parcelle livrait → CONTRADICTED.

**P-041** : des arbres perdus, mais sur un terrain que la carte de l'UE ne comptait pas comme forêt en 2020 → UNVERIFIABLE, avec une demande de vérification sur le terrain. GreenTrace refuse de condamner sur une impression.

## Ce que l'on voit et touche

Le site public [greentrace-eudr.higgsfield.app](https://greentrace-eudr.higgsfield.app) et le moteur [greentrace-0qd7.onrender.com](https://greentrace-0qd7.onrender.com) tournent en mode échantillon, sans clé : les règles remplacent le modèle, le verdict est le même.

| Fonctionnalité | Ce qu'elle montre |
| --- | --- |
| Machine à remonter le temps (accueil) | P-042 année par année sur 6 scènes Sentinel-2 réelles, les cellules de forêt 2020 qui virent à l'orange |
| Scène d'analyse | Pendant l'enquête, la caméra vole du globe à la parcelle interrogée, avec les vraies lignes de trace |
| Why | Le verdict, ses constats et les lignes qui les portent |
| What would change it | Chaque changement qui renverse le verdict, avec Apply qui rejoue et revérifie |
| Ask GreenTrace | Une question, une réponse tirée des seules lignes de l'enquête, le schéma du pourquoi, le routage Scout et son contrat vérifié |
| Court | Le Plaignant, l'Évaluateur et les règles, sur trois tours |
| Autres vues (8) | Attaques tentées, Deep Trace phrase par phrase, graphe des preuves, mémoire avant/après, radar, calendrier des passages satellite, lettre au fournisseur, trace |
| Check plots | Un fichier de parcelles vérifié sur la carte de l'UE, classé par risque, avec dossier d'audit |

**Ouvert aux autres agents** : les mêmes cinq opérations (vérifier une promesse, un fichier de parcelles, what_if, expliquer, vérifier un reçu) sont un serveur MCP (`/mcp`), un agent A2A (`/a2a`, carte dans `/.well-known/agent-card.json`) et un agent AG-UI pour le chat. Chaque enquête est aussi une trace OpenTelemetry.

## Ce qui est mesuré

Sur les mêmes 21 cas pièges, Nemotron seul accuse à tort 5 à 10 fois par passe ; GreenTrace jamais. Chaque chiffre vient d'un fichier du dépôt.

| Mesure | Résultat | Fichier |
| --- | --- | --- |
| 21 cas pièges, règles seules (sans modèle) | 18/21, 0 fausse accusation | `data/bench_sample.json` |
| 21 cas avec Nemotron, NVIDIA direct (22/10) | 21/21, 0 fausse accusation | `README.md` |
| 21 cas à travers Nightshift, Ultra 550B (25/10) | 21/21, 89 appels, 0 retour aux règles, 89/89 reçus valides | `data/bench_nightshift.json` |
| Nemotron seul, toutes les tables et nos règles dans le prompt, 5 passes | 5 à 12 sur 21, 5 à 10 fausses accusations par passe | `data/bench_model_alone.json` |
| Falsification : 3 020 valeurs du reçu changées une à une | 3 020/3 020 attrapées ; 177/177 si le faussaire recalcule les empreintes | `data/bench_tamper.json` |
| Scout, batterie officielle (32 exemples) | Tous les seuils passés (1,00 ; clarification 0,06) | `meterless/scout-evals` |
| Scout sur nos 27 paragraphes | 1 bloqué, l'injection du cas 19, 0 fausse alerte | `data/bench_scout.json` |
| Scout, jeu aveugle figé | 4/5 attaques bloquées, 0/4 textes propres bloqués | `data/scout_heldout.json` |
| Tests automatiques | 754 réussis | `tests/` |

Sur le cas propre, Nemotron seul a écrit que trois alertes étaient « dans » des parcelles situées à environ 1,5 km : un modèle ne calcule pas une géométrie de tête, d'où la géométrie et le verdict dans le code.

## Les limites, dites franchement

- Les 21 cas pièges sont synthétiques et écrits par nous : ce n'est pas un jeu aveugle. Seules P-041 et P-042 sont de vraies parcelles.
- La démo publique tourne en mode échantillon, sans clé ; les exécutions en direct sont conservées dans `output_examples/live/`.
- Nightshift exclut en ce moment Nemotron 3.5 Super VL après des réponses vides répétées : la voie profonde passe par Ultra 550B.
- La batterie officielle de Scout est un petit jeu de 32 exemples dont les cas servent aussi d'exemples au registre ; notre jeu aveugle reste écrit par nous, et une reformulation d'attaque y passe encore.
- Une alerte satellite ou une cellule de carte n'est pas une preuve juridique ; les cartes à 30 m peuvent mal lire quelques cellules.
- Chaque verdict demande une revue humaine ; les lettres aux fournisseurs demandent, elles n'accusent jamais.

## Carte du code

Tout est dans le dépôt public [polmanas1998-star/greentrace](https://github.com/polmanas1998-star/greentrace).

| Chemin | Contenu |
| --- | --- |
| `app/agents/` | Les cinq agents (extractor, planner, investigator, evaluator, writer) |
| `app/orchestrator.py`, `app/pipeline.py` | Le déroulé d'une enquête et le what_if |
| `app/rules.py` | Les règles qui décident |
| `app/queries.py`, `app/adapters/zetaris.py` | Les 11 requêtes Q0 à Q10 et le client Zetaris |
| `app/adapters/llm.py`, `app/adapters/meterless_receipt.py` | La seule porte vers le modèle (Nightshift, NVIDIA, règles) et la vérification des reçus signés |
| `app/memory/` | Clients World Model, H-MEM et Scout |
| `app/explain/` | Ask, la bascule, Deep Trace, graphe, radar, lettres |
| `app/court/`, `app/geo/`, `app/standards/` | La cour, les cartes et les passages satellite, MCP et A2A |
| `meterless/` | Serveur Meterless : World Model et H-MEM (références), Scout (`scout/`, notre implémentation) et sa batterie officielle (`scout-evals/`) |
| `verify.py` | La vérification hors ligne d'un reçu |
| `scripts/` | Bancs (bench, baseline_model_alone, tamper_bench, scout_bench), chargement Zetaris, vérification des reçus Nightshift |
| `data/` | Cartes, cas, et tous les résultats de mesure |
| `output_examples/` | Reçus d'exemple et exécutions en direct conservées |
| `site/` | Le site public (React, TanStack Start, Cloudflare Worker) |
| `docs/` | La soumission, la vidéo de démo, le GIF et ce document |
