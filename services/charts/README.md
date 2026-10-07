# services/charts

- `stats.py` : statistiques sans dépendance (moyenne, % sous / dans / au-dessus de l'objectif, par moment de la journée).
- `figures.py` : figures matplotlib construites sans pyplot, partagées par l'écran (`FigureCanvasGTK4Agg`) et le PDF. Nécessite `python3-matplotlib`.
- `palette.py` : `ChartPalette`, couleurs et polices d'une figure. Chaque figure prend `palette=` en dernier argument :
  - par défaut `PDF_PALETTE`, les couleurs historiques du rapport (bleu, vert, rouge, orange d'Adwaita), fond et texte de matplotlib : le PDF du médecin ne change pas ;
  - à l'écran, `app.theme.chart_palette(dark)` : couleurs du thème de la tablette, clair ou sombre, police Nunito.

Les couleurs sont posées sur chaque élément (axes, textes, points, légende), jamais dans `matplotlib.rcParams` : ce réglage est global, et le PDF se construit dans un thread pendant que l'écran dessine ses graphiques. Les tests (`tests/test_charts.py`) vérifient que `rcParams` ne bouge pas, que les figures par défaut gardent les valeurs de matplotlib et que chaque élément d'une figure à thème prend sa couleur.
