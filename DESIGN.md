# Glucofi sur PC : design

Le design de référence est celui de la tablette : [GlucofiAndroid/DESIGN.md](../GlucofiAndroid/DESIGN.md) (couleurs, polices Fredoka et Nunito, échelle typographique, composants, renard). Le PC le reprend tel quel ; ce document ne liste que les écarts.

## Ce qui est repris à l'identique

- Couleurs claires et sombres : `app/theme.py`, comparées à `Theme.kt` par `app/tests/test_theme.py`.
- Échelle typographique : mêmes tailles, en points (px × 0,75) pour suivre le réglage « grands caractères » de Gnome.
- Composants : bannière du renard, tuiles Matin et Soir, bouton « Valider N UI », colonne « Pourquoi ? », « Dernière mesure », bandeaux à pastille ronde, filtres en pilules, cartes par jour, tuiles de synthèse pastel (`app/components.py`, `app/art.py`).
- Logo, soleil et lune : mêmes tracés que `Common.kt` et `ic_doodle_*.xml` (vérifié par test). Renard et tache de pinceau : illustrations de la tablette, le renard seulement dans la bannière d'Aujourd'hui et du premier lancement.
- Icônes : Material Symbols arrondies, comme la tablette.

## Écarts propres au PC

- **Onglets en haut** : pilules au centre de la barre de titre, à côté du logo. Sous 600 px de large, ils passent en bas comme sur la tablette.
- **Préférences en dialogue** : le protocole se modifie dans un dialogue (sections blanches sur fond sauge, champs à contour, unités en suffixe), ouvert depuis Doses ou le menu.
- **Graphiques** : onglet propre (courbe, glycémies du matin et dose, répartition), absent de la tablette, avec l'export PDF pour le médecin.
- **Souris et clavier** : survol des pilules et des lignes, infobulles, Ctrl+R pour récupérer les mesures, Ctrl+P pour le PDF, Ctrl+, pour les Préférences.
- **Voir le détail** : dialogue centré sur grand écran, feuille du bas sous 600 px.
- **Tablette et PDF** : boutons de la barre de titre, repris dans le menu.
