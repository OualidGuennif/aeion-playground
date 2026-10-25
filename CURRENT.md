# CURRENT — directives de l'opérateur et plan de travail

Écrit le 2026-10-24 à la demande de l'opérateur, avant une absence prolongée. Ce fichier
est la référence : toute décision doit pouvoir être rattachée à une ligne ci-dessous.

## Directives (verbatim, résumées sans les affaiblir)

1. **Continuer le travail du repo** : « il reste encore beaucoup de phases à compléter ».
   Finir ce que Codex n'avait pas terminé.
2. **Droit de merge** : l'opérateur donne le droit de merger les PR « après les avoir
   review pendant 72h ». Traduction opérationnelle : ne merger qu'après revue réelle du
   diff, CI hébergée verte sur la révision exacte, et aucune revendication non mesurée.
   L'opérateur n'a plus à être consulté.
3. **Intégrer d'autres frameworks de dev** et surtout **faire tourner toute la démo dans
   Docker**, y compris le cockpit principal — « même la démo main aeion il doit aussi
   tourner dans un docker et non pas sur mon pc local, pareil pour tout l'écosystème de la
   démo ». Tout doit respecter les **guidelines de sécurité**.
4. **Guidelines manquantes** : les repos `aeion-factory` et `aeion-spaceship` (et trois
   autres) n'ont pas de guidelines, « c'est la base ». Le playground doit être exemplaire
   sur ce point.
5. **Outils demandés** : `tt-a1i / archify` devait produire le diagramme du projet dans
   `knowledge/` — à faire. Évaluer `NVIDIA / OpenShell` et `vectorize-io / hindsight`
   (utiles ou non, et si déjà utilisés). `ECC` pour les skills si besoin.
   **Pas d'ORCA** : Paperclip suffit.
6. **ParcelDesk** : créer une vraie app user-facing spectaculaire — entrepôt en Three.js
   bien visible, travailleurs, desk de support — simulant une entreprise qui traite des
   commandes e-commerce et des remboursements. Utiliser le framework Aeion lui-même,
   créer des tickets, et **déléguer les tâches de modification de ParcelDesk à la démo**
   elle-même (c'est le test de bout en bout du système).
7. **Autorité d'approbation** : exceptionnellement, l'agent peut approuver (ou refuser
   selon son analyse) dans Paperclip et dans le cockpit Aeion, l'opérateur étant absent.
   Cette autorité reste **séparée de la sortie du modèle** : elle s'appuie sur les preuves
   du ledger, pas sur le texte produit par un agent.
8. **Périmètre** : tout ce qui n'est pas ParcelDesk (phases, infra, docs) = amélioration
   du playground, à faire **avant**. ParcelDesk en **dernier**, précisément pour valider
   que la boucle complète fonctionne.
9. **README GitHub** : expliquer comment comprendre la démo vite, comment la voir en
   action, et comment créer des tickets d'amélioration que les agents prendront.
10. **Plan explicite et organisé** avant d'exécuter.

## Contraintes non négociables (AGENTS.md)

- 250k de contexte maximum : à l'approche du plafond, écrire `HANDOVER.md` en entier puis
  compacter et continuer dans la même session.
- Preuve mesurée seulement. Un test non lancé est listé comme non lancé. Un fichier de
  configuration ne prouve pas une isolation.
- Pas de socket Docker, de home hôte ni de credentials dans un worker non fiable.
  Le contrôleur est de confiance ; les rôles ne le sont pas.
- Autorité Gate/approbation séparée de la sortie du modèle.
- Preuves scellées et conception cockpit/ParcelDesk existantes préservées.
- Secrets, état local, transcripts : hors Git.

## Plan, dans l'ordre

| # | Étape | Critère d'acceptation | État |
|---|---|---|---|
| P0 | `CURRENT.md` + plan | Ce fichier relu et cohérent | fait |
| P1 | Guidelines du repo : `CONTRIBUTING.md`, templates d'issue/PR, `SECURITY` complété | Un contributeur sait coder, tester, proposer ; un ticket ParcelDesk a un format que les agents peuvent lire | **fait** — PR #9, `6d15bde` |
| P2 | README : comprendre la démo, la lancer, voir le parcours, créer un ticket | Un lecteur froid lance la démo en < 10 min | **fait** — PR #9 |
| P3 | Écosystème en Docker : cockpit + services, sans socket dans les rôles | `docker compose up` sert le cockpit sur 127.0.0.1 ; tests de frontière négatifs | **fait** — PR #12 + #13 (`574d8d0`). Cockpit sain sur 127.0.0.1:8124, agent hôte (probe/run_tests/run_role), PD00 et PD03 mesurés via l'agent. Limites : test agent-kill non écrit, inférence live non qualifiée. |
| P4 | Diagramme `knowledge/` (archify/tt-a1i ou équivalent local) | Diagramme versionné, cohérent avec le code | **fait** — PR #14 (`3c015ac`), Archify 3.2.0, validate/deliver/check/browser-check pass. |
| P5 | Gate externe (external acceptance) | Un rapport de worker n'est jamais accepté seul | **fait** — PR #15 mergée (`c3b6bdf`). Le Gate rejoue les tests de confiance sur `app/` patché ; un worker seul est refusé `EXTERNAL_ACCEPTANCE`. |
| P6 | Livraison GitHub (issue → PR → CI → merge) | Parcours réel démontré, autorité humaine séparée | à faire — non implémenté ; le README dit explicitement que GitHub delivery est *Pending*. |
| P7 | Qualification finale : install fraîche, backup/restore, limites | Rapport daté, limites explicites | à faire |
| P8 | **ParcelDesk** démo 3D + support, tickets délégués à la Factory | La démo s'améliore via ses propres tickets | **fait** — PR #16 (`c37a479`). `/warehouse` Three.js, commandes/remboursements/support, onglet Factory → mission `agent` bornée (patch → tests worker → acceptance Gate → review → Gate → release locale). Limites : le patch accepté n'est pas hot-déployé ; le contexte libre reste pricing. |

## Journal

- 2026-10-24 : fichier créé. Repo sur `main` = `cf8fb66`, arbre propre, CI verte.
- 2026-10-24 22:5x : PR #9 mergée (`6d15bde`) — CONTRIBUTING, formulaire de ticket
  ParcelDesk, template de PR, README (parcours 10 min + comment créer un ticket), watchdog
  de session. Décision d'architecture pour P3 écrite dans `docs/demo-deployment.md` :
  **option C** (agent exécuteur sur l'hôte + API étroite), socket Docker jamais dans un
  conteneur. Aucune ligne de cette option n'est encore implémentée.

- 2026-10-24 23:xx : PR #12 mergée (`6bcd999`) — `ExecutorClient`, agent hôte
  (`probe`, `run_tests`, `run_role`), client distant, 405 tests. PR #13 (cockpit Docker)
  ouverte : image cockpit, service Compose, secret `executor_agent`, tests de frontière
  opt-in. Mesuré : cockpit sain, `/` et `/parceldesk` 200, PD00 (`12/19`, `NO_PATCH`),
  PD03 (`19/19`, `PROTECTED_PATH`), worker `--network none --read-only --user 65532`.
- 2026-10-24 23:xx : PR #14 mergée (`3c015ac`) — diagramme Archify dans `knowledge/` ;
  PR #15 (external acceptance) ouverte : le Gate rejoue les tests de confiance et refuse
  un worker seul ; test de mission avec tests affaiblis bloqué avant review.

- 2026-10-25 00:xx : PR #15 mergée (`c3b6bdf`) — acceptance Gate-owned ; mission avec tests remplacés bloquée `EXTERNAL_ACCEPTANCE` avant review. PR #13 (cockpit Docker) : health sans agent corrigé, stack CI relancée. PR #16 (ParcelDesk 3D + Factory) : `/warehouse` rendu (capture navigateur), remboursement PD-1035 mesuré 24,00 €, 418 tests mesurés. Diagramme Archify et agent hôte déjà sur `main`.

- 2026-10-25 00:xx : PR #13 mergée (`574d8d0`) — cockpit Docker, binds `runtime/cockpit` + token, stack CI verte avec `/`, `/parceldesk`, `/warehouse` et `/api/health` sans agent. PR #16 mergée (`c37a479`) — `/warehouse` 3D + Factory. Suite finale : 422 passed, 13 skipped. Étapes restantes : P6 (GitHub delivery) et P7 (qualification fraîche/agent-kill), plus l'inférence live non exercée.

- 2026-10-25 01:xx : preuve live **réussie** — rôle `forge` réel dans son conteneur OpenCode via le gateway, modèle `opencode-go/deepseek-v4.1-flash`, 259 tokens entrée / 1 699 sortie, lease `completed`, sortie patch JSON. Le credential Console de l'opérateur a été monté uniquement dans le gateway (`runtime/secrets/opencode-provider.json`, 0600, gitignoré) ; la copie d'export a été supprimée. Le free tier reste refusé (« free tier can only be used from within OpenCode »). Détail et limites : `docs/live-inference.md`. La routing policy locale reste expirée : une mission complète live exige une autorisation future + budget.

- 2026-10-25 02:xx : **boucle live complète** — PD01 dans le cockpit Docker → executor hôte → gateway. Forge payant `opencode-go/deepseek-v4.1-flash` (979/1 654 tokens, 0,001139 $) produit le patch de prix correct ; tests 19/19 côté worker et 19/19 côté Gate (image `aeion.pytest-executor/v1` identique à l'admission) ; Sentinel payant `opencode-go/glm-5.3-flash` (1 160/187 tokens, 0,000267 $) rend `OK` sur une autre famille ; Gate `HUMAN_CHECKPOINT` (risque high + `app/pricing.py`), l'opérateur lit la preuve et approuve ; mission `PD-003` terminée `assisted`, gate `HUMAN_APPROVED`, packet scellé `sha256:a314da8a78c84bfbeb4552585d1d9575057a4ef7903ca38a7477dc9e3388aadd`, total 2 139/1 841 tokens et 0,001406 $. Trois bugs de frontière révélés et corrigés par l'exécution réelle (identité Docker demandée à l'executor, protocole patch imposé au remote, `allowed_paths` de PD01/PD02). Limites : mêmes fournisseur/compte pour les deux familles, Paperclip HTTP 403, pas de release locale pour PD01, CI hébergée toujours sur fixtures contrôlées.

- 2026-10-25 02:xx : **validation Factory live** — ticket borné `TCK-E68512F8` → `PD-006` (aire pricing, risque low) : forge payant `opencode-go/deepseek-v4.1-flash` 1 171/2 020 tokens (0,001388 $), tests 19/19 côté worker et 19/19 côté Gate (même image `aeion.pytest-executor/v1`), revue `opencode-go/glm-5.3-flash` 1 324/352 tokens (0,000375 $) `OK`, Gate `HUMAN_CHECKPOINT` (`app/pricing.py`), approbation opérateur, packet scellé `sha256:40dbb227147920738c592648ef48589795f55066bad2100e6b1e4eec5eae4aa9`, release locale `fc6ca0df544e5132ae154ac9ba462789a3b1156e` (parent `86ff1351a6079d22cf1351e8fbe7a911b14c2b54`), `product_head` mis à jour ; total ticket 0,001763 $ / 2 495/2 372 tokens. Deux échecs réels avant : `PD-004` tronqué à 8 192 tokens de sortie (0,005099 $), que le routeur a traité à tort comme une indisponibilité fournisseur (cooldown d'une heure) ; `PD-005` est alors tombé sur GLM et a rendu un patch corrompu (0,000307 $). Corrigé par #23 : un `length` est un échec de qualité, pas une indisponibilité ; l'état obsolète a été levé par un événement audité. Les tickets liés restaient `running` après la mission : corrigé par #24, réconciliation auditée, `open_tickets: 0`. Dépense cumulée mesurée 0,009619 $, aucune exposition non réglée. Limites : release locale non déployée, commentaire « DEFECT » conservé (contrainte du ticket), même fournisseur/compte pour les deux familles, Paperclip HTTP 403, CI hébergée toujours sur fixtures contrôlées.
