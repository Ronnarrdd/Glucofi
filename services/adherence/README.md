# services/adherence

Journal des injections, pur et déterministe (aucune base, aucune horloge : `today` et `now` sont passés en paramètre).

- Une dose est *due* un jour où la dose en cours ce jour-là est supérieure à 0 UI, du premier jour du protocole à hier. Aujourd'hui n'est jamais compté.
- Le patient déclare une dose *prise* ou *non prise* (`contracts.Injection`) ; sans ligne, elle est *non renseignée*.
- `adherence(injections, changes, since, until, today)` : par dose, dues, prises, non prises, non renseignées, jours concernés et observance (prises sur dues ; une dose non renseignée compte comme non prise).
- `unlogged_days` / `unlogged_alerts` : doses pas cochées les trois derniers jours, plus aujourd'hui une fois la plage de la glycémie de référence de la dose passée (fin de la plage du matin pour la dose du matin, du soir pour celle du soir). Codes d'alerte `unlogged_evening`, `unlogged_morning`.
- `missed_doses(injections)` et `dose_before(jour, dose)` : ce que le moteur de dose (`services/dosing`) utilise pour écarter une glycémie qui suit une dose non prise.

Tests : `python3 -m unittest discover -t . -s services/adherence`. Eval : `python3 -m evals.replay_history` (journal tiré au hasard, oracle indépendant) et `python3 -m evals.store_merge` (fusion du journal entre PC et tablette).
