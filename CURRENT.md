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
| P1 | Guidelines du repo : `CONTRIBUTING.md`, templates d'issue/PR, `SECURITY` complété | Un contributeur sait coder, tester, proposer ; un ticket ParcelDesk a un format que les agents peuvent lire | à faire |
| P2 | README : comprendre la démo, la lancer, voir le parcours, créer un ticket | Un lecteur froid lance la démo en < 10 min | à faire |
| P3 | Écosystème en Docker : cockpit + services, sans socket dans les rôles | `docker compose up` sert le cockpit sur 127.0.0.1 ; tests de frontière négatifs | à faire |
| P4 | Diagramme `knowledge/` (archify/tt-a1i ou équivalent local) | Diagramme versionné, cohérent avec le code | à faire |
| P5 | Gate externe (external acceptance) | Un rapport de worker n'est jamais accepté seul | à faire |
| P6 | Livraison GitHub (issue → PR → CI → merge) | Parcours réel démontré, autorité humaine séparée | à faire |
| P7 | Qualification finale : install fraîche, backup/restore, limites | Rapport daté, limites explicites | à faire |
| P8 | **ParcelDesk** démo 3D + support, tickets délégués à la Factory | La démo s'améliore via ses propres tickets | à faire (en dernier) |

## Journal

- 2026-10-24 : fichier créé. Repo sur `main` = `cf8fb66`, arbre propre, CI verte.
