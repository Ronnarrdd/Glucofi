# services/meals

Journal alimentaire : estimation des calories et des glucides d'un repas écrit en texte libre, par Gemini (plan gratuit).

- `estimate_meal(texte, clé)` : un appel REST `generateContent`, réponse JSON typée (`MealEstimate` : calories, glucides, fourchette basse/haute, détail par aliment). Seul le texte du repas part chez Google.
- `parse_estimate` valide la réponse : texte qui n'est pas un repas refusé, valeurs négatives ou invraisemblables (> 400 g de glucides, > 5 000 kcal) refusées, fourchette recadrée autour de l'estimation.
- Toute panne devient une `EstimateError` dont le message est écrit pour le patient : clé refusée, quota, hors ligne, réponse illisible. Une relance sur les erreurs 5xx.
- **La clé n'est jamais dans le dépôt.** `load_api_key` lit `GEMINI_API_KEY` dans l'environnement, sinon dans `.env` (racine du projet, ou dossier de données de Glucofi). `.env` est ignoré par git ; `.env.example` montre le format. Sur la tablette, la clé vient de `local.properties` (ignoré par git aussi).
- Le modèle est `gemini-flash-latest` : le dernier Flash, gratuit.
- Les chiffres sont des estimations. Le patient peut les corriger à la main (source `manual`) ; ils ne servent jamais au calcul de la dose d'insuline.

Tests (sans réseau, transport simulé) : `python3 -m unittest discover -t . -s services/meals`.
Eval (réseau, clé requise, hors commit) : `python3 -m evals.meal_estimates`.
