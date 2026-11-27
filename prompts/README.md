# Prompts de GuideBot

[`system_prompt.txt`](system_prompt.txt) définit le rôle du modèle, les règles de citation, la conservation des conditions et le signal d’abstention `INFORMATION_INSUFFISANTE`. Le code charge ce fichier à chaque rédaction.

1. GuideBot cherche dans les versions publiées, valides et accessibles au compte. Il récupère les passages et leurs repères avant d’appeler Ollama.
2. Le modèle reçoit la langue, la question et les passages numérotés. Les instructions éventuellement présentes dans la question ou dans un document restent du texte à analyser.
3. Ollama retourne un objet JSON avec `answer` et `references`. Python vérifie le format, le résultat incomplet et les numéros de référence. La température est fixée à zéro pour rendre les essais plus reproductibles.
4. La discussion affiche les extraits cités. La reformulation apparaît sous ces extraits si elle passe les contrôles. Les réponses incomplètes, expirées et contradictoires gardent le message documentaire correspondant.

Exemple de sortie pour une question sur la durée du stage, si le passage [1] indique une durée minimale de six semaines consécutives.

```json
{
  "answer": "Le stage de L2 dure au minimum 6 semaines consécutives.",
  "references": [1]
}
```

Le mode connecté applique un prompt dédié lorsque des sources publiques sont présentes. Il demande au modèle de distinguer le Web des consignes de l’établissement et de ne tirer aucun détail d’un simple titre de presse.

Pour comparer une modification du prompt, rejouez `tests/evaluate.py --mode full` avec le même modèle et les mêmes documents. Relisez les sorties qui modifient un seuil, omettent une exception ou citent un passage qui ne justifie pas le fait.
