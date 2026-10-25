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
| P6 | Livraison GitHub (issue → PR → CI → merge) | Parcours réel démontré, autorité humaine séparée | **fait** — `scripts/deliver_github.py` a livré PD-028 : issue #33, PR #34, 8/8 checks (dont `product-tests` et `docker-tests`), merge squash `59ae38a`, arbre produit distant identique à la release scellée. |
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

- 2026-10-25 03:xx : **backup/restore du cockpit mesuré** — cockpit arrêté, WAL checkpointé, `PRAGMA integrity_check` `ok` sur `aeion.db` et `parceldesk.db`, archive `runtime/backups/cockpit-20261025T010347Z.tgz` (228 094 octets, 0600). Restauration en place après extraction temporaire vérifiée : 6 missions, 8 tentatives, 3 tickets (0 ouvert), packet `PD-006` valide, `product_head` `fc6ca0d`, `/warehouse` 200 ; 82 événements après redémarrage (un `engine.started` de plus). Limites : même hôte et même image, pas de corruption disque simulée, pas de restauration hors site ; le token executor et le credential gateway sont hors archive. L'archive contient le ledger et les sorties modèle liées, donc elle reste hors Git et hors stockage partagé.

- 2026-10-25 03:xx : **drill agent tué** — un test temporaire dormant ajouté au sandbox cuit dans l'image, cockpit reconstruit, `PD03` lancé (`qual-agent-kill-20261025T012706Z`) sous `PD-007`. À `01:27:11Z`, agent hôte tué par `SIGKILL` (pid 87613) pendant que le worker tournait depuis 3 s : mission bloquée dans la même seconde, `EXECUTION_FAILED`, `ExecutorUnavailable: Executor agent unavailable: Remote end closed connection without response`, 0 run, 0 token, 0 coût ; un seul `mission.started` et un seul `mission.blocked`. Le worker `aeion-test-2cf3…` (id `fa525fab2d85`) restait vivant (`--rm` ne nettoie qu'à la sortie, le `finally` de l'agent n'a pas tourné). Agent relancé (pid 94383) : avant de servir, il liste uniquement son label propriétaire et écrit `removed 1 container(s) left by a previous run` ; conteneur supprimé avant la fin de son sleep de 30 s. Après attente : toujours `blocked` / `EXECUTION_FAILED`, 1 `mission.started`, 0 run — aucun rejeu. Test temporaire retiré, image reconstruite, `template_digest` revenu à `c3ce129a…` (valeur PD-006), `/warehouse` 200, `open_tickets: 0`. Événement audité `operator.agent_kill_drill` avec l'identité du conteneur, les deux pids, zéro appel modèle et le compte de reap. Limites : conteneur de test, pas de rôle payant tué en vol (même label et même chemin, non exercé avec un modèle) ; le reap n'a lieu qu'au démarrage de l'agent ; les répertoires de travail `roles/`/`runs/` peuvent rester ; `tests/test_agent_kill.py` (opt-in `AEION_DOCKER_TESTS=1`) rejoue la moitié conteneur sans cockpit ni modèle.

- 2026-10-25 03:xx : **permissions du worker sur Linux** — le job Docker hébergé qui rejoue le drill a révélé un défaut réel : l'`umask 077` de l'agent laissait les répertoires du snapshot en 0700, donc l'uid 65532 du conteneur ne pouvait pas traverser `/work` (`Unusable worker report: [Errno 13] Permission denied: '/work'`). Docker Desktop le masquait sur macOS ; sur Linux, toute mission passant par l'agent aurait échoué de la même façon. Corrigé en normalisant tous les répertoires du snapshot en 0755 (les fichiers restent en 0444), avec un test unitaire qui construit le snapshot sous le même umask. PR #29, CI Docker verte ; suite locale 435 passed, 14 skipped.

- 2026-10-25 03:xx : **ParcelDesk product** — nouveau produit `sandbox/parceldesk/product` : API Quarkus 3.40.1 / Hibernate 7.4.9 / H2 2.4.240 sur **Temurin 25.0.4.1** (port 8081), web Deno 2.5 / Fresh 1.6.8 / Three.js 0.170 (port 8125), deux services Compose read-only, sans socket ni credential. Le worker devient polyglotte (Python + Maven/JDK 25 hors-ligne + Deno) et accepte `test_profile=product` : 13/13 (9 Maven + 4 Deno) ; le profil legacy reste 19/19 sur la release scellée. Les limites JVM passent à 1 GiB / 1,5 CPU / 256 pids / 512 MiB de scratch / 4096 fichiers. Ticket produit `TCK-B028A56C` → `PD-010` : auteur payant GLM 5 122/3 814 tokens (0,002675 $), reviewer DeepSeek 6 179/1 098 tokens (0,001586 $) `OK`, worker 13/13, acceptance Gate 12/12, Gate `CLEAN` autonome, packet `sha256:0c83baa4…dbb84f44`, release `3df45d50…`, tête produit séparée `product_head:product` (la tête legacy reste `fc6ca0d`), PR-001 auto-mergée par la web app, API promue depuis la release : PD-1050 expose `refundReason: "Damaged in transit"` et 4 000 cents. Deux échecs réels avant : `PD-008` bloqué `PATCH_CONTEXT` avant tout appel modèle (0 $) car le lecteur de contexte n'acceptait que Python ; `PD-009` a produit un patch correct mais le Gate l'a refusé `OUT_OF_SCOPE` (0,00475 $) à cause du périmètre codé en dur `app/tests`. Corrigés avec tests : contexte produit, périmètre `product/**`, acceptance qui restaure les tests produit de confiance, livraison avec le profil et une tête de release séparée. Dépense cumulée 0,018630 $, aucune exposition non réglée. Limites : la revue est un ledger H2 local (pas de PR GitHub), la promotion est un rebuild hôte de la release scellée, les services produit ne sont pas dans le job `stack-tests` hébergé, les deux familles de modèles partagent un compte. Doc : `docs/parceldesk-product.md`.

- 2026-10-25 04:4x : **Factory produit, deuxième et troisième releases autonomes** — `PD-020` (`TCK-B82E1033`, *Add a tested refund note formatter*) : auteur GLM 7 835/3 765 tokens (0,003058 $), reviewer DeepSeek 9 150/3 362 (0,003390 $) `OK`, worker 16/16, acceptance Gate **13/13 depuis la release acceptée PD-010** (preuve du correctif de tests de confiance), Gate `CLEAN`, packet `sha256:1041f53d…61eb`, release `b592d1f` (parent `3df45d5`), PR-002 auto-mergée. `PD-021` (`TCK-AB19B7EB`, *Use the refund note in the refund desk*) : GLM 8 112/2 852 (0,002643 $), DeepSeek 9 014/1 112 (0,002019 $) `OK`, worker 16/16, acceptance 16/16, Gate `CLEAN`, packet `sha256:f0087ba4…2ed09`, release `b01d4f7` (parent `b592d1f`), PR-003 auto-mergée. La page live reconstruite depuis PD-021 affiche « €40.00 refunded · Damaged in transit » ; les deux anciens champs séparés ont disparu.
- 2026-10-25 04:4x : **Corrections nées des échecs réels** — (1) plafond de sortie porté de 8 192 à **16 384** tokens (PD-011/PD-015 consumaient tout le budget en raisonnement) ; (2) `apply_patch` accepte les hunks minimaux (`--unidiff-zero`) et recompte les en-têtes (`--recount`), après PD-012/PD-013/PD-018 ; (3) règlement opérateur `Store.reconcile_unknown_attempt` qui facture **la réservation entière** (jamais zéro), stocke une note et débloque l'inférence — $2,000 réconciliés pour le flux inconnu PD-014 (dépassement de la borne 2 Mio) ; (4) routeur : un arrêt `length` avec complétion médiée et une erreur locale de credential gateway ne refroidissent plus le modèle — cooldowns DeepSeek erronés effacés avec note de correction. `PD-019` avait 16/16 et 13/13 mais a bloqué sur le reviewer refroidi ; `PD-020` a repassé le même ticket. Dépense rapportée cumulée 0,055208 $, exposition 2,055208 $, aucun essai non réglé.

- 2026-10-25 04:2x : **checkpoint humain et ledger de remboursement** — `PD-024` (`TCK-6F38ADDE`, *Expose the refund ledger endpoint*, risque **high**) : auteur GLM 4 486/15 196 tokens (0,008271 $), reviewer DeepSeek 5 896/10 439 (0,007148 $) `OK`, worker 17/17, acceptance Gate 16/16, Gate `HUMAN_CHECKPOINT` (risk=high), approbation opérateur via le proxy web, packet `sha256:d725c08b…a8d05a9`, release `aeeeb7a1`, **PR-004 mergée par `operator`** (pas auto). L'API promue depuis PD-024 expose `GET /api/refunds` (PD-1050 : 4 000 cents, « Damaged in transit », 2026-10-17). Le ticket suivant, `PD-028` (`TCK-AAE35494`, *Read the refund desk from the refund ledger*), auteur GLM 8 118/15 012, reviewer DeepSeek 9 352/6 299 `OK`, worker 17/17, acceptance 17/17, Gate `CLEAN`, packet `sha256:7518c6d6…`, release `b5fe5f7`, PR-005 auto-mergée ; le web reconstruit affiche « €40.00 refunded · Damaged in transit · 2026-10-17 ». Dépense rapportée 0,132792 $, réconciliée 4,000 $ (deux coupures médiateur), exposition 4,132792 $, aucun essai non réglé, 28 missions, 374 événements.
- 2026-10-25 04:1x : **trois défauts réels corrigés par les tickets** — (1) `PD-022` a été coupée à 91,7 s par la deadline upstream du gateway (90 s) alors que le rôle avait 180 s : deadline portée à **170 s**, avec test d'invariant ; l'essai inconnu a été réconcilié à 2,000 $ (jamais zéro). (2) `PD-023`, `PD-025`, `PD-026` ont épuisé la borne de **16 384** tokens de sortie ; les tâches ont été découpées en changements d'un fichier. (3) `PD-026`/`PD-027` ont été refusés `REVIEW_REJECTED` par DeepSeek sur un **faux décompte de hunk** alors que le diff canonique était correct ; le prompt de revue ne code plus `app/**`/pytest en dur, il utilise les chemins admis et déclare les en-têtes de diff machine-générés comme autoritatifs. La préférence d'auteur est restée GLM, la revue a été confiée à GLM/DeepSeek selon l'historique ; `PD-028` est passé `CLEAN`.
- 2026-10-25 04:3x : **P6, livraison GitHub côté opérateur** — ajout de `app/github_delivery.py`, `scripts/deliver_github.py` et `docs/github-delivery.md` : lecture du receipt `aeion.local-delivery/v1` et du packet scellé, vérification du commit/tree propre, refus d'un arbre produit divergé, remplacement strict de `sandbox/parceldesk/product/**`, branche `factory/pd-0xx`, issue/PR GitHub idempotentes, attente de **tous** les checks et merge squash seulement si `product-tests` et les autres passent. Le job CI `product-tests` (Temurin 25 + Maven, Deno 2.5) exécute réellement les suites du produit. Aucun credential GitHub dans le cockpit ou les rôles. Mesuré : 7 tests dédiés, dry-run PD-028 a préparé 7 fichiers produit sans push. Livraison distante live encore à mesurer.

- 2026-10-25 05:0x : **P6 mesuré en live** — `scripts/deliver_github.py --mission PD-028 --merge` a préparé la branche `factory/pd-028` (commit `2db7bc1`), créé l'issue **#33** et la PR **#34**, attendu **8/8** checks (`native-tests`, `stack-tests`, `product-tests`, `docker-tests`, `secrets`, `inference-tests`, `telemetry-tests`, `dependencies`) puis mergé en squash **`59ae38a`** ; l'issue est fermée. L'arbre `origin/main:sandbox/parceldesk/product` vaut `640bdbcf…`, exactement l'arbre `HEAD:product` de PD-028. Deux défauts réels ont été trouvés et corrigés : `gh pr checks` renvoie « no checks reported » avant l'enregistrement des jobs (PR #35) et le test du worker produit figeait 12 tests alors que PD-028 en a 17 (PR #36, plancher ≥ 12). Le cockpit n'a jamais reçu de credential GitHub ; la livraison reste une action opérateur.
