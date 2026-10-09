# services/report

`build_report(ReportInput, path)` : PDF A4 (reportlab) avec traitement en cours, proposition et alertes, statistiques, 3 graphiques, historique des doses (avec les mesures écartées à chaque validation), détail des mesures (colorées hors objectif, colonne Matin « retenue / écartée / comptée », colonne Note, résumé des notes de la période : `notes_summary`, et une ligne « Repas » par jour du journal alimentaire, insérée au-dessus des mesures du jour : `ReportInput.meals`, `meals_line`) et avertissement médical.
