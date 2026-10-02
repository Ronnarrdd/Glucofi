# services/charts

- `stats.py` : statistiques sans dépendance (moyenne, % sous / dans / au-dessus de l'objectif, par moment de la journée).
- `figures.py` : figures matplotlib construites sans pyplot, partagées par l'écran (`FigureCanvasGTK4Agg`) et le PDF. Nécessite `python3-matplotlib`.
