# services/dosing

Moteur de titration de la dose du soir. Pur et déterministe : aucune I/O, `today` est passé en paramètre.

- `morning_readings(readings, settings)` : glycémie du matin de chaque jour, dans la plage horaire des réglages : la première mesure « à jeun », sinon la première « avant repas » ou sans marqueur. Les marqueurs « après repas », « coucher » et « autre moment » sont exclus (`contracts.MORNING_ALLOWED_MEALS`).
- Notes : une glycémie du matin possible (`is_morning_candidate`) dont la note a `exclude_from_dosing` est ignorée (`excluded_from_dosing`), sauf sous le seuil bas (`can_exclude`, `exclusion_refused`) : une note ne masque jamais une baisse. Les alertes hypo / hyper voient toutes les mesures.
- `propose(readings, changes, settings, today) -> DoseProposal` : règle appliquée, dose proposée, justification, alertes (dont `excluded`, niveau INFO, et `exclusion_refused`, niveau WARNING), mesures écartées depuis la dose en cours (`DoseProposal.excluded`).
- `apply_proposal(proposal, now) -> DoseChange` : à appeler uniquement après validation par l'utilisateur ; `DoseChange.excluded` garde les mesures écartées avec leur motif.

Tests : `python3 -m unittest discover -t . -s services/dosing`. Eval : `python3 -m evals.replay_history`.
