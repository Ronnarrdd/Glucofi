# services/dosing

Moteur de titration de la dose du soir. Pur et déterministe : aucune I/O, `today` est passé en paramètre.

- `morning_readings(readings, settings)` : glycémie du matin de chaque jour, dans la plage horaire des réglages : la première mesure « à jeun », sinon la première « avant repas » ou sans marqueur. Les marqueurs « après repas », « coucher » et « autre moment » sont exclus (`contracts.MORNING_ALLOWED_MEALS`).
- `propose(readings, changes, settings, today) -> DoseProposal` : règle appliquée, dose proposée, justification, alertes.
- `apply_proposal(proposal, now) -> DoseChange` : à appeler uniquement après validation par l'utilisateur.

Tests : `python3 -m unittest discover -t . -s services/dosing`. Eval : `python3 -m evals.replay_history`.
