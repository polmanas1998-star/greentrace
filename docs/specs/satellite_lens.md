# Satellite Lens IA : « Nemotron looks. The code checks. »

Décidé par Amir le 21/10/2026 au soir (« ça doit être boosté à l'IA », « ça doit choquer les jurys »).
C'est LE petit ajout du 21/10 prévu par le brief. Il ne passe qu'APRÈS les portes 1 et 2 (voir § 6).

## 1. Ce que voit le juge

1. Il dessine une parcelle sur la carte (la carte du 25).
2. L'app montre l'image Sentinel-2 de la parcelle **avant (2020) et après (2026)**, découpée en **grille 3 × 3 (A1 à C3)**.
3. **Nemotron Vision regarde** et pose ses marques case par case : « coupe récente », « plantation », « sol nu », « nuage, je ne peux pas conclure », avec une confiance de 0 à 1.
4. **Nemotron choisit l'outil** pour vérifier ce qu'il croit voir : NDVI (végétation) pour une coupe, NBR pour un brûlis, fausses couleurs infrarouges pour la végétation. Liste FERMÉE de 3 indices, jamais 25.
5. **Le code vérifie chaque affirmation**, case par case, contre la perte Hansen/UMD et l'indice calculé :
   - **vert, CONFIRMED** : l'IA avait raison, le code le prouve ;
   - **rouge, REJECTED** : l'IA s'est trompée (ex. un nuage pris pour du sol nu) ;
   - **orange, MISSED BY AI** : le code trouve une perte que l'IA n'a pas vue.
6. Score affiché : « AI [a]/9 · code corrected [b] cells », puis le verdict, calculé par les règles comme avant.

## 2. Ce qui se passe derrière (et ce qui ne change pas)

- **Le verdict reste calculé par le code** (`app/rules.py`). L'IA ne fait jamais basculer un verdict seule : une affirmation de l'IA n'entre dans le World Model que si le code la confirme, avec `derived_from` (cases, indice, lignes Hansen).
- L'**Evaluator** note chaque affirmation de l'IA ; chaque note est une ligne de trace (`agent_name: Evaluator`, `action: check_vision_claim`, `status: confirmed | rejected | missed`, `provider`, `model`).
- Le modèle passe par la couche fournisseur : **Meterless d'abord** (fournisseur selon le contrat Power Prompt ; Nightshift n'est PAS installé, décision du 21/10), NVIDIA direct en repli, chaque repli écrit dans la trace.
- **SAMPLE_MODE** (pas de modèle) : le Lens affiche « AI lens needs a model » ; la grille du CODE (Hansen + indice) marche quand même. **Jamais de réponse d'IA enregistrée rejouée** : ce serait une sortie écrite d'avance (motif de disqualification).
- Le reçu de verdict (`verify.py`) ne dépend pas de l'IA : les réponses du modèle y sont en « info only », les vérifications du code sont recalculables.

## 3. Preuve faite le 21/10 (sonde hors dépôt, aucune ligne de code du projet)

Appel réel à `nemotron-3.5-super-vl-120b-a12b` (NVIDIA direct), image avant/après de Soubré **sans aucune annotation**, 1024 × 507 px :
- réponse en **13 s** ; avec une image 1552 × 768 et 3 000 jetons : **délai dépassé à 120 s** ;
- l'IA situe la perte au **centre** (juste) et en bas à droite (en partie), repère **seule les nuages** comme piège possible, confiance 0,9 ;
- elle **rate la grosse perte en haut à gauche** que la carte Hansen montre : exactement le moment « MISSED BY AI » de la démo ;
- avec `max_tokens` 700, la réponse est **vide** : le raisonnement mange tout. Prévoir au moins 2 500 jetons ou couper le raisonnement.
- le nom renvoyé par l'API est `private/nvidia/nemotron-3.5-super-120b-a12b` : noter dans la trace le modèle DEMANDÉ et le modèle RENVOYÉ.

### 3 bis. Deuxième sonde, en grille (21/10, 17 h 42) : c'est elle qui est DANS la vidéo de présentation

Même zone (2,4 × 1,4 km, celle de la vidéo), grille 3 × 3, réponse JSON par case, comparée à la perte Hansen 2021-2024 (seuil 0,5 ha par case) :
- **AI 6/9** d'accord avec le code ; **code corrected 3** : 1 fausse alerte (A3 : l'IA voit une coupe, Hansen 0,03 ha) et 2 pertes **ratées** (B3 2,35 ha, C2 2,69 ha : « forêt intacte » selon l'IA), soit **5,04 ha ratés** ;
- **54 s** pour la réponse en grille (image 1024 px, 4 000 jetons) : prévoir un délai max de **90 s** et l'appel en arrière-plan, pas 45 s ;
- fichiers : `8_Video_motion_design/lens_21_10/` (`ia_output.json`, `code_truth.json`, `ia_input.jpg` et les 3 scripts). C'est une SONDE hors dépôt : à déclarer comme travail préparatoire, le code du Lens s'écrit après le 22.

## 4. Pièges connus

- **Nightshift** : pas installé (décision du 21/10, pas de carte graphique). Le Lens passe par NVIDIA direct, repli écrit dans la trace. Le prix DGX est laissé de côté, sauf voie officielle sans GPU annoncée.
- **Lenteur** : 13 s par analyse. Appel lancé en parallèle de l'enquête, délai max 90 s (54 s mesurées en grille), cache par empreinte (parcelle + dates), image ≤ 1024 px.
- **Sortie structurée** : demander un JSON strict par case (`cell`, `label`, `confidence`, `lens`) ; si le JSON est invalide, une seule relance, puis « vision unavailable » dans la trace.
- **Cacaoyère sous ombrage** : elle reste verte au NDVI. Le code ne dit jamais « déforestation » sur le seul NDVI : il croise avec Hansen et le dit.
- **Nuages** : masque SCL de Sentinel-2 ; une case trop nuageuse est « cannot see », jamais « intact ».
- **Licences** : Sentinel-2 L2A (Copernicus) et Hansen (CC BY 4.0) uniquement dans l'app. Pas d'EOX (voir risques_disqualification_21_10.txt).
- Données : les bandes Sentinel-2 (B02, B03, B04, B08, B11, B12 + SCL, deux dates) de la zone de démo sont récupérées par le **script de données du 22** (STAC public d'Earth Search, sans clé), en cache dans le dépôt, petites. Aucun téléchargement au démarrage.

## 5. Temps (pour Amir qui code dans Cursor, débogage compris)

| Morceau | Faire | Config + débogage | Total |
|---|---|---|---|
| Bandes Sentinel-2 dans le script de données (2 dates, masque nuages, recadrage) | 1 h | 1 h | **2 h** |
| Découpe avant/après + grille 3 × 3 pour la parcelle dessinée | 0 h 45 | 0 h 30 | **1 h 15** |
| Appel Nemotron Vision (image, JSON strict, délai, cache, repli) | 1 h | 1 h | **2 h** |
| Vérificateur du code par case (Hansen + indice) + lignes de trace | 1 h | 0 h 30 | **1 h 30** |
| Interface : grille, marques de l'IA, couleurs, score | 1 h 15 | 0 h 45 | **2 h** |
| Tests (dont un test qui rougit si l'IA fait basculer le verdict seule) | 0 h 45 | 0 h 15 | **1 h** |
| **Total complet** | | | **≈ 10 h** |

**Version courte (recommandée), ≈ 5 h 30** : grille + Nemotron Vision sur les couleurs naturelles + vérification par Hansen seule (pas de nouvelles bandes, pas de choix d'indice). Couleurs vert, rouge, orange et le score : tout le choc de la vidéo est déjà là. Le choix d'indice par l'IA (NDVI, NBR) = bonus si la version courte est verte.

## 6. Où le mettre dans la semaine (proposition, à trancher avec Amir)

- **25 au soir**, seulement si les **portes 1 et 2 sont vertes** : version courte, 1re moitié (grille + appel Vision).
- **26 au matin** : 2e moitié (vérificateur, interface, tests), AVANT le tournage. Pour faire la place, F2 (« prochaine preuve la plus utile ») passe en bonus : le Lens est plus visible.
- Si une porte n'est pas verte : **le Lens est abandonné sans toucher au reste.** Rien d'autre ne dépend de lui.

## 7. Phrases (à garder VRAIES le jour du tournage)

- Remplace partout « Our model never reads a pixel » par : **« Nemotron looks. The code checks. Nothing the AI sees reaches the verdict unverified. »**
- Vidéo (10 s, vers 1:50) : « Nemotron looks at the satellite. The code checks every word. Here, the AI missed [x] hectares. GreenTrace didn't. » ([x] lu dans la trace ; si l'IA n'a rien raté ce jour-là, dire ce qui s'est vraiment passé.)
- README : « Vision is a second opinion, not a verdict. Every AI observation is checked cell by cell against Hansen/UMD tree-cover loss before it can enter the evidence. »
