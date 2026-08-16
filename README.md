# BL Tracker

Application Windows simple et locale pour suivre ses séries BL asiatiques,
épisode par épisode.

## L'histoire du projet

BL Tracker est né d'une collaboration entre **DrJeckyllMrHyde** et
**ChatGPT / Codex d'OpenAI**. Le logiciel a d'abord été imaginé et créé pour
la compagne de DrJeckyllMrHyde, afin de lui offrir un outil personnel, clair
et pratique pour organiser les séries qu'elle regarde.

Après l'avoir utilisé et amélioré ensemble, DrJeckyllMrHyde a choisi de rendre
le projet public afin qu'il puisse aussi aider la communauté des fans de BL.
Chacun peut désormais l'utiliser, l'adapter et contribuer à son évolution.

## Fonctionnalités

- ajout et modification de séries ;
- suivi des épisodes vus et de leur date de visionnage ;
- nombre total d'épisodes et progression ;
- pays d'origine et filtres dynamiques ;
- acteurs, personnages et rôles ;
- affiche locale avec miniature ;
- résumé, notes personnelles et lien rapide ;
- import d'informations depuis une URL compatible ;
- sauvegarde locale SQLite et export de la base ;
- création facultative d'un exécutable Windows autonome.

## Confidentialité

Toutes les données sont enregistrées **localement** dans `data/bl_tracker.db`.
Le projet n'envoie pas votre bibliothèque vers un serveur. La base personnelle
et les images téléchargées sont ignorées par Git afin d'éviter leur publication
accidentelle.

## Installation sous Windows

1. Téléchargez le projet puis décompressez-le.
2. Double-cliquez sur `INSTALLER_WINDOWS.bat` lors de la première utilisation.
3. Lancez ensuite `LANCER_BL_TRACKER.bat`.

Python 3 doit être installé et accessible dans le `PATH`. L'installateur ajoute
la bibliothèque Pillow, utilisée pour les miniatures.

## Créer un exécutable autonome

Double-cliquez sur `CREER_EXE_WINDOWS.bat`. Le résultat sera placé dans
`release/BL Tracker.exe`. La base et les images seront conservées dans le
dossier `data` situé à côté de l'exécutable.

## Développement et vérification

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python bl_tracker.py
```

## Contribuer

Les signalements de bugs, idées d'amélioration et contributions sont les
bienvenus. Merci de ne jamais joindre votre fichier `data/bl_tracker.db` ni vos
images personnelles à une issue ou une pull request.

## Licence

Ce projet est distribué sous licence [MIT](LICENSE).
